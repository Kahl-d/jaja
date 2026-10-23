"""hn_hiring.py --algolia: HN Algolia "hiring" stories posted between the monthly threads."""
from __future__ import annotations

from datetime import timedelta

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp

import hn_hiring as hn  # noqa: E402

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
T0 = int(NOW.timestamp())


def _hit(oid, title, *, author="founder", url=None, text=None, ago_h=1):
    return {"objectID": str(oid), "title": title, "author": author, "url": url, "story_text": text,
            "created_at_i": T0 - ago_h * 3600, "_tags": ["story", f"author_{author}", f"story_{oid}"]}


ACME = _hit(50100001, "Acme AI (YC W26) is hiring a Founding AI Engineer", url="https://acme.ai/jobs/founding",
            text="<p>We build agents. We sponsor visas.</p>", ago_h=2)
NOURL = _hit(50100002, "Lumen Labs is hiring a Machine Learning Engineer", text="Remote US. Email jobs@lumen.example.test",
             ago_h=5)
SALES = _hit(50100003, "Acme AI is hiring an Account Executive", url="https://acme.ai/jobs/ae", ago_h=3)
CHAT = _hit(50100004, "Why is hiring so broken?", ago_h=4)
TALK = _hit(50100005, "Show HN: A tool I built for my side project", ago_h=6)
THREAD = _hit(50100006, "Ask HN: Who is hiring? (October 2026)", author="whoishiring", ago_h=7)
WANTS = _hit(50100007, "Ask HN: Who wants to be hired? (October 2026)", author="whoishiring", ago_h=7)
BRACKET = _hit(50100008, "[Hiring] Senior AI Engineer at Northwind (Remote)", url="https://northwind.io/careers",
               ago_h=8)


def _routes(by_query=None, default=()):
    """One route for search_by_date; the response depends on params["query"]."""
    by_query = by_query or {}

    def serve(params):
        return {"hits": list(by_query.get(params["query"], default)), "nbHits": 0}
    return {("GET", ALGOLIA): serve}


def _all_hits():
    return [ACME, NOURL, SALES, CHAT, TALK, THREAD, WANTS, BRACKET]


def test_hiring_title_regex_keeps_announcements_and_drops_talk():
    assert hn.is_hiring_title(ACME["title"]) and hn.is_hiring_title(BRACKET["title"])
    assert hn.is_hiring_title("We're hiring: ML engineers at Foo")
    for t in (CHAT["title"], TALK["title"], THREAD["title"], WANTS["title"], "Ask HN: Is hiring frozen everywhere?",
              "The hiring market in 2026", "How we are hiring engineers now"):
        assert not hn.is_hiring_title(t), t


def test_a_hiring_story_becomes_a_record_with_id_source_and_url():
    http = FakeHttp(_routes({"hiring": [ACME]}))
    records, info = hn.collect_algolia(http, NOW, since=T0 - 86400)
    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == "acme-ai-hn-50100001"
    assert rec["source"] == "hn" and rec["company"] == "Acme AI" and rec["title"] == "Founding AI Engineer"
    assert rec["source_url"] == "https://news.ycombinator.com/item?id=50100001"
    assert rec["apply_url"] == "https://acme.ai/jobs/founding"
    assert "We sponsor visas." in rec["_jd_text"] and "<untrusted_jd>" in rec["_body"]
    assert rec["posted_at"]
    assert info["newest"] == ACME["created_at_i"]


def test_a_story_without_url_falls_back_to_the_hn_item_link():
    records, _ = hn.collect_algolia(FakeHttp(_routes({"hiring": [NOURL]})), NOW, since=0)
    assert records[0]["apply_url"] == "https://news.ycombinator.com/item?id=50100002"
    assert records[0]["id"] == "lumen-labs-hn-50100002"


def test_bracket_hiring_title_takes_company_from_at_clause():
    records, _ = hn.collect_algolia(FakeHttp(_routes({"hiring": [BRACKET]})), NOW, since=0)
    assert records[0]["company"] == "Northwind" and records[0]["title"] == "Senior AI Engineer"


def test_non_hiring_titles_and_monthly_threads_are_dropped():
    http = FakeHttp(_routes({"hiring": [CHAT, TALK, THREAD, WANTS]}))
    records, info = hn.collect_algolia(http, NOW, since=0, relevant_only=False)
    assert records == []
    assert info["hits"] == 4 and info["hiring"] == 0


def test_duplicates_across_queries_appear_once_and_each_query_is_one_request():
    http = FakeHttp(_routes(default=[ACME, NOURL]))
    records, info = hn.collect_algolia(http, NOW, since=0)
    assert sorted(r["id"] for r in records) == ["acme-ai-hn-50100001", "lumen-labs-hn-50100002"]
    assert len(http.calls) == len(hn.ALGOLIA_QUERIES)
    assert [c[2]["query"] for c in http.calls] == list(hn.ALGOLIA_QUERIES)
    for _, url, params in http.calls:
        assert url == ALGOLIA and params["tags"] == "story" and params["numericFilters"] == "created_at_i>0"
        assert 0 < params["hitsPerPage"] <= 1000
    assert info["unique"] == 2


def test_title_filter_drops_non_ai_roles_unless_all():
    http = FakeHttp(_routes({"hiring": [ACME, SALES]}))
    kept, _ = hn.collect_algolia(http, NOW, since=0)
    assert [r["id"] for r in kept] == ["acme-ai-hn-50100001"]
    everything, _ = hn.collect_algolia(FakeHttp(_routes({"hiring": [ACME, SALES]})), NOW, since=0,
                                       relevant_only=False)
    assert sorted(r["id"] for r in everything) == ["acme-ai-hn-50100001", "acme-ai-hn-50100003"]


def test_cli_first_run_looks_back_a_bounded_window_then_saves_newest(root):
    http = FakeHttp(_routes({"hiring": _all_hits()}))
    assert hn.main(["--root", str(root), "--algolia"], http=http, now=NOW) == 0
    since = T0 - int(timedelta(days=hn.ALGOLIA_LOOKBACK_DAYS).total_seconds())
    assert http.calls[0][2]["numericFilters"] == f"created_at_i>{since}"
    newest = max(h["created_at_i"] for h in _all_hits())
    state = hn.load_state(root)
    assert state["algolia_since"] == newest
    assert "last_thread_id" not in state                       # the monthly collector did not run
    jobs = sorted(p.stem for p in (root / "jobs").glob("*.md"))
    assert len(jobs) == 3                                     # Acme, Lumen, Northwind; the AE role is filtered
    # the next run asks only for newer posts
    http2 = FakeHttp(_routes())
    assert hn.main(["--root", str(root), "--algolia"], http=http2, now=NOW + timedelta(hours=6)) == 0
    assert {c[2]["numericFilters"] for c in http2.calls} == {f"created_at_i>{newest}"}
    assert hn.load_state(root)["algolia_since"] == newest       # nothing newer seen: unchanged


def test_algolia_state_keeps_the_monthly_keys_and_monthly_keeps_algolia(root):
    hn.save_state(root, 4001, NOW)
    hn.main(["--root", str(root), "--algolia"], http=FakeHttp(_routes({"hiring": [ACME]})), now=NOW)
    state = hn.load_state(root)
    assert state["last_thread_id"] == 4001 and state["algolia_since"] == ACME["created_at_i"]
    hn.save_state(root, 4002, NOW)
    assert hn.load_state(root)["algolia_since"] == ACME["created_at_i"]


def test_cli_algolia_dry_run_writes_nothing(root, capsys):
    rc = hn.main(["--root", str(root), "--algolia", "--dry-run"], http=FakeHttp(_routes({"hiring": _all_hits()})),
                 now=NOW)
    assert rc == 0
    assert not (root / "jobs").exists() and not (root / "state").exists()
    out = capsys.readouterr().out
    assert "dry-run (nothing written)" in out and "3 new" in out


def test_cli_algolia_limit_caps_records_and_all_keeps_non_ai(root, capsys):
    rc = hn.main(["--root", str(root), "--algolia", "--all", "--limit", "2", "--dry-run"],
                 http=FakeHttp(_routes({"hiring": _all_hits()})), now=NOW)
    assert rc == 0
    assert "2 new" in capsys.readouterr().out
    rc = hn.main(["--root", str(root), "--algolia", "--all", "--dry-run"],
                 http=FakeHttp(_routes({"hiring": _all_hits()})), now=NOW)
    assert "4 new" in capsys.readouterr().out                  # the AE role too


# Titles the live API returned: articles about hiring must not pass.
@pytest.mark.parametrize("title,hiring", [
    ("Hiring a Founding CTO", True),
    ("Hiring Staff Machine Learning Engineer", True),
    ("We're hiring engineers because of the edge AI gives us", True),
    ("Hiring Breakpoints", False),
    ("Hiring Smart", False),
    ("Hiring and Firing a DBA", False),
    ("Analysis of 88,975 HN Who Is Hiring Job Posts", False),
])
def test_article_titles_about_hiring_do_not_pass(title, hiring):
    assert hn.is_hiring_title(title) is hiring
