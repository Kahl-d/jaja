"""posts_sweep_check.py: the posts lane's morning sweep file, state/posts/sweep-<date>.jsonl, checked
row by row against its one format, and deduped on lead_id within the file and across earlier days. No network."""
from __future__ import annotations

import hashlib
import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import posts_sweep_check as psc  # noqa: E402

POST = "https://www.linkedin.com/posts/jane-doe_hiring-agents-activity-7380000000000000001-AbCd"
XPOST = "https://x.com/janedoe/status/1975000000000000001"


def row(**over):
    url = over.pop("url", POST)
    base = {"platform": "linkedin", "kind": "hiring_post", "url": url, "author": "Jane Doe",
            "author_title": "CTO at Acme AI", "author_url": "https://www.linkedin.com/in/jane-doe/",
            "company": "Acme AI", "posted_at": "2026-10-05T15:00:00Z", "seen_at": "2026-10-06T14:10:00Z",
            "text": "We're hiring a founding AI engineer in SF.", "role_hint": "Founding AI Engineer",
            "location_hint": "San Francisco, CA", "query": '"hiring" "AI engineer"',
            "lead_id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]}
    base.update(over)
    return base


def write(folder, day, rows):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"sweep-{day}.jsonl"
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows), encoding="utf-8")
    return path


def test_lead_id_is_sha1_of_url_first_12():
    assert psc.lead_id(POST) == hashlib.sha1(POST.encode("utf-8")).hexdigest()[:12]
    assert len(psc.lead_id(XPOST)) == 12


def test_a_good_file_passes(tmp_path):
    x = row(platform="x", url=XPOST, author_url="https://x.com/janedoe", lead_id=psc.lead_id(XPOST))
    person = row(kind="person", url="https://www.linkedin.com/in/john-roe/", author="John Roe", text="",
                 role_hint=None, posted_at=None, query="watch:John Roe",
                 lead_id=psc.lead_id("https://www.linkedin.com/in/john-roe/"))
    startup = row(kind="startup", url="https://acme.ai", author="Jane Doe", text="Seed, agents for RF design",
                  query="feed", lead_id=psc.lead_id("https://acme.ai"))
    rep = psc.check_file(write(tmp_path, "2026-10-06", [row(), x, person, startup]))
    assert rep["errors"] == [] and rep["rows"] == 4 and rep["new"] == 4 and rep["repeats"] == []


def test_missing_and_unknown_keys_are_refused(tmp_path):
    r = row()
    del r["company"]
    r["extra"] = 1
    rep = psc.check_file(write(tmp_path, "2026-10-06", [r]))
    text = " ".join(rep["errors"])
    assert "line 1" in text and "missing company" in text and "unknown key extra" in text


def test_wrong_lead_id_platform_kind_and_bad_json(tmp_path):
    rows = [row(lead_id="000000000000"), row(platform="threads"), row(kind="job"), "{not json"]
    rep = psc.check_file(write(tmp_path, "2026-10-06", rows))
    errs = rep["errors"]
    assert any(e.startswith("line 1") and "lead_id" in e for e in errs)
    assert any(e.startswith("line 2") and "platform" in e for e in errs)
    assert any(e.startswith("line 3") and "kind" in e for e in errs)
    assert any(e.startswith("line 4") and "JSON" in e for e in errs)


def test_url_must_fit_platform_and_kind_and_carry_no_query(tmp_path):
    feed = "https://www.linkedin.com/feed/update/urn:li:activity:7380000000000000002/"
    tracked = POST + "?utm_source=share"
    rows = [row(url=XPOST, lead_id=psc.lead_id(XPOST)),                      # an X URL on a linkedin row
            row(url=tracked, lead_id=psc.lead_id(tracked)),                  # a tracking query breaks the dedupe key
            row(kind="person", url=POST, lead_id=psc.lead_id(POST)),          # a person row needs a profile URL
            row(url=feed, lead_id=psc.lead_id(feed))]                         # the feed form of a post is fine
    rep = psc.check_file(write(tmp_path, "2026-10-06", rows))
    lines = sorted({e.split(":")[0] for e in rep["errors"]})
    assert lines == ["line 1", "line 2", "line 3"]


def test_dates_and_required_text(tmp_path):
    rows = [row(seen_at="2026-10-06"),                                        # seen_at needs a time and a zone
            row(posted_at="yesterday"),
            row(posted_at="2026-10-07T00:00:00Z"),                            # posted after it was seen
            row(text="  "),                                                   # a hiring post needs its text
            row(posted_at="2026-10-05")]                                      # a date alone is fine for posted_at
    rep = psc.check_file(write(tmp_path, "2026-10-06", rows))
    lines = sorted({e.split(":")[0] for e in rep["errors"]})
    assert lines == ["line 1", "line 2", "line 3", "line 4"]


def test_query_is_required_and_author_url_is_a_url(tmp_path):
    rep = psc.check_file(write(tmp_path, "2026-10-06", [row(query=""), row(author_url="jane-doe")]))
    assert sorted({e.split(":")[0] for e in rep["errors"]}) == ["line 1", "line 2"]


def test_duplicate_within_a_file_is_an_error(tmp_path):
    rep = psc.check_file(write(tmp_path, "2026-10-06", [row(), row(query="feed")]))
    assert any(e.startswith("line 2") and "duplicate lead_id" in e for e in rep["errors"])


def test_repeats_across_days_are_reported_not_refused(tmp_path):
    write(tmp_path, "2026-10-04", [row()])
    write(tmp_path, "2026-10-07", [row(url=XPOST, platform="x", lead_id=psc.lead_id(XPOST))])   # a later day: ignored
    path = write(tmp_path, "2026-10-06", [row(), row(url=XPOST, platform="x", lead_id=psc.lead_id(XPOST))])
    rep = psc.check_file(path)
    assert rep["errors"] == [] and rep["new"] == 1
    assert rep["repeats"] == [{"line": 1, "lead_id": psc.lead_id(POST), "first_seen": "2026-10-04"}]


def test_file_name_must_be_sweep_date(tmp_path):
    path = tmp_path / "linkedin-2026-10-06.jsonl"
    path.write_text(json.dumps(row()) + "\n", encoding="utf-8")
    rep = psc.check_file(path)
    assert any("sweep-<YYYY-MM-DD>.jsonl" in e for e in rep["errors"])


def test_cli_prints_labelled_counts_and_exits_on_errors(tmp_path, capsys):
    good = write(tmp_path / "a", "2026-10-06", [row()])
    assert psc.main(["--file", str(good)]) == 0
    out = capsys.readouterr().out
    assert "errors: 0" in out and "rows: 1" in out and "new: 1" in out and "repeats: 0" in out
    bad = write(tmp_path / "b", "2026-10-06", [row(lead_id="x")])
    assert psc.main(["--file", str(bad)]) == 1
    assert "errors: 1" in capsys.readouterr().out


def test_cli_id_prints_the_lead_id(capsys):
    assert psc.main(["id", POST]) == 0
    assert capsys.readouterr().out.strip() == psc.lead_id(POST)


# --------------------------------------------------------------------------- targets/post-queries.yaml
def _queries():
    import yaml
    from layout import JS as js
    return (yaml.safe_load((js / "targets" / "post-queries.yaml").read_text(encoding="utf-8")),
            yaml.safe_load((js / "targets" / "x-watch.yaml").read_text(encoding="utf-8")))


def test_post_queries_are_ranked_and_each_names_its_family():
    cfg, _ = _queries()
    for platform in ("linkedin", "x"):
        rows = cfg[platform]["queries"]
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        assert len({r["id"] for r in rows}) == len(rows) and all(r["q"] and r["why"] for r in rows)
        assert all(r["family"] for r in rows)                            # every query names the role family it serves
    li = cfg["linkedin"]["queries"]
    assert li[0]["where"]                                                 # the first query says where it looks


def test_x_daily_is_what_x_watch_runs_and_fits_the_cap():
    cfg, watch = _queries()
    by_id = {r["id"]: r["q"] for r in cfg["x"]["queries"]}
    assert [by_id[i] for i in cfg["x"]["daily"]] == [q["q"] for q in watch["queries"]]
    runs = len(cfg["x"]["daily"]) + 1                                     # plus the people query
    assert runs * int(watch["max_results"]) * 0.015 <= 0.45 + 1e-9
    assert all(len(q) <= 512 for q in by_id.values())
