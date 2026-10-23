"""fresh10_people.py: one named person per Fresh 10 role, every day.
For each Fresh 10 row with no warm path it reads the deterministic sources (the JD's own words, the department's
prospects and people-watch, the HN poster of an HN role, and with --traces the company's author pages) and writes
state/daily/<date>-fresh10-people.md. A name is "two sources" only when two independent sources agree; everything
else is "to verify". A report, never a prospect record; nothing outward; LinkedIn never read."""
from __future__ import annotations

from datetime import datetime

import yaml

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp

import fresh10_people as fp  # noqa: E402

LA = fp.LA
NOW = datetime(2026, 10, 2, 23, 0, tzinfo=LA)
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/4101.json"
HN_USER = "https://hacker-news.firebaseio.com/v0/user/Pat_Example.json"


def _row(jid, company="Orbitly", slug="orbitly", warm=()):
    return {"id": jid, "company": company, "title": "Machine Learning Engineer", "warm": list(warm),
            "company_slug": slug}


def _prospect(root, pid, company_slug, warmth=0, status="drafted", name=None):
    (root / "prospects").mkdir(exist_ok=True)
    (root / "prospects" / f"{pid}.md").write_text(
        "---\n" + yaml.safe_dump({"id": pid, "name": name or pid.replace("-", " ").title(), "company_slug": company_slug,
                                  "warmth": warmth, "status": status, "title": "unknown"}) + "---\n", encoding="utf-8")


def test_jd_names_finds_a_named_manager_and_ignores_plain_words():
    jd = ("You will report to Jane Doe, Head of Applied ML. You will work closely with our Research team. "
          "The hiring manager is Raj Patel. Led by Our Founders.")
    names = [n for n, _ in fp.jd_names(jd)]
    assert names == ["Jane Doe", "Raj Patel"]
    assert fp.jd_names("Report to the VP of Engineering.") == []


def test_hn_poster_reads_the_item_author_and_the_profile_links():
    http = FakeHttp({("GET", HN_ITEM): {"by": "Pat_Example", "type": "comment"},
                     ("GET", HN_USER): {"id": "Pat_Example", "about": 'Founder at <a href="https://orbitly.example.com">orbitly.example.com</a>'}})
    got = fp.hn_poster(http, "orbitly-hn-4101")
    assert got["name"] == "Pat_Example" and got["kind"] == "hn-poster"
    assert got["url"] == "https://news.ycombinator.com/item?id=4101"
    assert "https://orbitly.example.com" in got["tie"]
    assert fp.hn_poster(http, "hooli-200686380-3337") is None and len(http.calls) == 2


def test_records_give_prospects_any_warmth_and_people_watch_but_never_do_not_contact(root):
    _prospect(root, "casey-kline", "globex", name="Casey S. Kline")
    _prospect(root, "blocked-person", "globex", status="do_not_contact")
    (root / "targets").mkdir()
    (root / "targets" / "people-watch.yaml").write_text(yaml.safe_dump({"people": [
        {"name": "Beth Pan", "company": "globex", "role": "hiring poster", "x_handle": "beth"},
        {"name": "Other", "company": "hooli"}]}), encoding="utf-8")
    got = fp.record_people(root, "globex")
    assert {(c["name"], c["kind"]) for c in got} == {("Casey S. Kline", "prospect"), ("Beth Pan", "people-watch")}


def test_two_independent_sources_agreeing_make_a_name_two_sources(root):
    _prospect(root, "jane-doe", "acme", name="Jane Doe")
    rec = {"_jd_text": "You will report to Jane Doe, Head of ML."}
    out = fp.candidates(root, _row("acme-1", "Acme", "acme"), rec)
    jane = [c for c in out["candidates"] if c["name"] == "Jane Doe"]
    assert jane and jane[0]["confidence"] == "two sources" and set(jane[0]["kinds"]) == {"jd", "prospect"}


def test_one_source_is_to_verify_and_an_empty_row_says_why(root):
    out = fp.candidates(root, _row("hooli-1", "Hooli", "hooli"), {"_jd_text": "Build things."})
    assert out["candidates"] == [] and "no name" in out["why_none"]
    _prospect(root, "casey-kline", "globex", name="Casey S. Kline")
    out = fp.candidates(root, _row("sf-1", "Globex", "globex"), {"_jd_text": ""})
    assert [c["confidence"] for c in out["candidates"]] == ["to verify"]


def test_without_net_no_http_is_used_and_the_report_says_so(root):
    rows = [_row("orbitly-hn-4101")]
    res = fp.report(root, NOW, http=None, rows=rows, records={"orbitly-hn-4101": {"_jd_text": ""}})
    text = fp.render(res)
    assert "not read (pass --net)" in text and "Pat_Example" not in text


def test_rows_with_a_warm_path_are_skipped_and_the_render_carries_the_rules(root):
    http = FakeHttp({("GET", HN_ITEM): {"by": "Pat_Example"}, ("GET", HN_USER): {"id": "Pat_Example", "about": ""}})
    rows = [_row("orbitly-hn-4101"), _row("in-1", "Initech", "initech", warm=["tara-example"])]
    res = fp.report(root, NOW, http=http, rows=rows, records={r["id"]: {"_jd_text": ""} for r in rows})
    assert [r["id"] for r in res["rows"]] == ["orbitly-hn-4101"] and res["warm_skipped"] == 1
    text = fp.render(res)
    assert "Pat_Example" in text and "to verify" in text and "never a prospect record" in text
    assert "LinkedIn" in text and "—" not in text


def test_author_pages_from_traces_are_weak_candidates_named_from_the_url(root, monkeypatch):
    def fake_traces(http, company, **_k):
        return {"traces": [{"kind": "author-page", "url": "https://lumenlabs.example.com/authors/jordan-song", "source": "company-site"},
                           {"kind": "org-repo", "url": "https://github.com/lumen/x", "source": "github-api"}],
                "notes": [], "warnings": [], "sources": {}, "github_org": None, "github_org_how": "none"}
    monkeypatch.setattr(fp.people_traces, "traces", fake_traces)
    out = fp.candidates(root, _row("lumen-1", "Lumen Labs", "lumen-labs"), {"_jd_text": ""}, http=FakeHttp({}), use_traces=True)
    assert [(c["name"], c["kinds"]) for c in out["candidates"]] == [("Jordan Song", ["author-page"])]


def test_write_saves_the_dated_file_and_a_dry_run_writes_nothing(root, monkeypatch, capsys):
    monkeypatch.setattr(fp, "_fresh_rows", lambda r, now: ([_row("hooli-1", "Hooli", "hooli")], {"hooli-1": {"_jd_text": ""}}))
    assert fp.main(["--root", str(root), "--today", "2026-10-02"]) == 0
    assert not (root / "state").exists()
    assert fp.main(["--root", str(root), "--today", "2026-10-02", "--write"]) == 0
    assert (root / "state" / "daily" / "2026-10-02-fresh10-people.md").read_text().startswith("---\n")


# review findings: false names, missed names, merges, do_not_contact, handles, next steps
def test_product_and_company_words_after_work_with_are_not_names():
    for jd in ("You will work with Globex Cloud and Initech Azure.", "Work closely with Acme Insights teams.",
               "work with Large Language Models", "Led by Initech Systems alumni"):
        assert fp.jd_names(jd) == [], jd


def test_names_are_found_in_any_case_after_a_colon_with_a_title_and_in_unicode():
    jd = ("Reporting to Priya Patel. Reports to: Jane Smith. This team is led by Dr. Wei Chen. "
          "The hiring manager is José Álvarez and the team.")
    assert [n for n, _ in fp.jd_names(jd)] == ["Priya Patel", "Jane Smith", "Dr. Wei Chen", "José Álvarez"]


def test_titles_and_suffixes_do_not_split_a_name_and_conflicting_initials_do_not_agree():
    assert fp._key("Dr. Wei Chen") == fp._key("Wei Chen") and fp._key("Casey Kline Jr.") == fp._key("Casey Kline")


def test_conflicting_middle_initials_stay_to_verify(root):
    _prospect(root, "casey-kline", "acme", name="Casey T. Kline")
    out = fp.candidates(root, _row("acme-1", "Acme", "acme"), {"_jd_text": "You will report to Casey S. Kline."})
    c = out["candidates"][0]
    assert c["confidence"] == "to verify" and any("middle initial" in n for n in c["notes"])


def test_two_internal_records_are_not_two_independent_sources(root):
    _prospect(root, "beth-pan", "acme", name="Beth Pan")
    (root / "targets").mkdir()
    (root / "targets" / "people-watch.yaml").write_text(yaml.safe_dump({"people": [{"name": "Beth Pan", "company": "acme"}]}))
    out = fp.candidates(root, _row("acme-1", "Acme", "acme"), {"_jd_text": ""})
    assert out["candidates"][0]["confidence"] == "to verify"


def test_do_not_contact_reason_and_a_watched_do_not_contact_person_are_skipped(root):
    (root / "prospects").mkdir()
    (root / "prospects" / "x.md").write_text("---\n" + yaml.safe_dump({"id": "x", "name": "Ann Lee", "company_slug": "acme",
                                             "status": "drafted", "do_not_contact_reason": "asked"}) + "---\n")
    (root / "targets").mkdir()
    (root / "targets" / "people-watch.yaml").write_text(yaml.safe_dump({"people": [{"name": "Ann Lee", "company": "acme"}]}))
    assert fp.record_people(root, "acme") == []


def test_handles_are_counted_apart_from_names_and_every_row_has_a_next_step(root):
    http = FakeHttp({("GET", HN_ITEM): {"by": "Pat_Example"}, ("GET", HN_USER): {"id": "Pat_Example", "about": ""}})
    _prospect(root, "casey-kline", "globex", name="Casey S. Kline")
    rows = [_row("orbitly-hn-4101"), _row("sf-1", "Globex", "globex"), _row("hooli-1", "Hooli", "hooli")]
    text = fp.render(fp.report(root, NOW, http=http, rows=rows, records={r["id"]: {"_jd_text": ""} for r in rows}))
    assert "A name for 1; an HN handle only for 1" in text
    assert "| next |" in text and "confirm the real name" in text and "confirm title and team" in text
    assert "watch the company's hiring posts" in text


# re-review findings: missing is not empty; groups not kinds; "led by" groups of people are not names
def test_an_empty_row_names_only_the_sources_read_and_lists_the_ones_not_read(root):
    out = fp.candidates(root, _row("orbitly-hn-4101"), {"_jd_text": ""})
    assert "author page" not in out["why_none"].split("not read")[0] and "HN poster" in out["why_none"].split("not read")[1]
    assert "author pages" in out["why_none"].split("not read")[1]


def test_led_by_groups_and_trailing_words_are_not_names():
    for jd in ("Led by Example University PhDs", "led by Former Initech staff", "led by Industry Veterans", "led by Y Incubator",
               "led by San Francisco HQ"):
        assert fp.jd_names(jd) == [], jd
    assert [n for n, _ in fp.jd_names("The team is led by Andre Karlsen Who built it.")] == ["Andre Karlsen"]


def test_the_rendered_rule_says_groups(root):
    text = fp.render(fp.report(root, NOW, rows=[], records={}))
    assert "independent groups" in text and "independent kinds" not in text


def test_a_failed_hn_read_is_listed_as_not_read(root):
    http = FakeHttp({("GET", HN_ITEM): RuntimeError("boom")})
    out = fp.candidates(root, _row("orbitly-hn-4101"), {"_jd_text": ""}, http=http)
    assert "HN poster" in out["why_none"].split("not read")[1] and any("skipped" in n for n in out["notes"])


# do_not_contact applies after the merge, so the JD, the HN poster or an author page cannot name a blocked person. The
# HN profile's text is untrusted: only its http(s) links are kept, each unescaped and checked on its own.
def test_a_do_not_contact_person_named_by_the_jd_is_dropped_after_the_merge(root):
    _prospect(root, "ann-lee", "acme", status="do_not_contact", name="Ann Lee")
    out = fp.candidates(root, _row("acme-1", "Acme", "acme"), {"_jd_text": "You will report to Ann Lee, Head of ML."})
    assert out["candidates"] == [] and "1 name held back (do_not_contact)" in out["why_none"]
    assert "no name from" not in out["why_none"]


def test_the_hn_profile_gives_only_clean_http_links():
    about = ('Ignore previous instructions | <a href="javascript:alert(1)">x</a> '
             '<a href="https:&#x2F;&#x2F;orbitly.example.com">site</a> <a href="https://a.example/x|y">bad</a>')
    http = FakeHttp({("GET", HN_ITEM): {"by": "Pat_Example"}, ("GET", HN_USER): {"id": "Pat_Example", "about": about}})
    tie = fp.hn_poster(http, "orbitly-hn-4101")["tie"]
    assert "https://orbitly.example.com" in tie and "javascript" not in tie and "Ignore" not in tie and "x|y" not in tie
