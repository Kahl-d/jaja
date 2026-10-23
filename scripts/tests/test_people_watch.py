"""people_watch.py: the people who post about hiring, watched on X and read on LinkedIn."""
from __future__ import annotations

import json
from datetime import date

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import people_watch as pw

TODAY = date(2026, 9, 29)
FILE = """people:
  - {name: Sam Arden, x_handle: sam_arden, linkedin_url: "https://www.linkedin.com/in/samarden/", company: acme-ai,
     why: "posts platform hiring calls", added: 2026-09-29, source: research, status: watch}
  - {name: Ana Ruiz, linkedin_url: "https://www.linkedin.com/in/ana-ruiz", company: tiny-ai,
     why: "founder; posts roles weekly", added: 2026-09-29, source: prospects, status: followed}
  - {name: Old Friend, x_handle: oldfriend, company: x, why: "no longer hiring", added: 2026-09-01, source: owner,
     status: dropped}
"""


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "js"
    (r / "targets").mkdir(parents=True)
    (r / "targets" / "people-watch.yaml").write_text(FILE)
    return r


def test_load_validates_every_person_needs_a_reason_a_channel_and_a_known_status(root):
    people = pw.load(root)
    assert [p["name"] for p in people] == ["Sam Arden", "Ana Ruiz", "Old Friend"]
    for bad, match in (("why: \"founder; posts roles weekly\"", "why"), ("status: followed", "status"),
                       ("linkedin_url: \"https://www.linkedin.com/in/ana-ruiz\", ", "channel")):
        text = FILE.replace(bad, {"why": "why: \"\"", "status": "status: stalking"}.get(match, ""), 1)
        (root / "targets" / "people-watch.yaml").write_text(text)
        with pytest.raises(pw.WatchError, match=match):
            pw.load(root)


def test_a_bad_handle_or_a_non_profile_linkedin_url_is_refused(root):
    (root / "targets" / "people-watch.yaml").write_text(FILE.replace("x_handle: sam_arden", "x_handle: 'sam arden!'"))
    with pytest.raises(pw.WatchError, match="x_handle"):
        pw.load(root)
    (root / "targets" / "people-watch.yaml").write_text(
        FILE.replace("https://www.linkedin.com/in/samarden/", "https://www.linkedin.com/search/results/people/?q=sam"))
    with pytest.raises(pw.WatchError, match="linkedin.com/in/"):
        pw.load(root)


def test_x_handles_are_the_active_people_only(root):
    assert pw.x_handles(root) == ["sam_arden"]                        # dropped people are not watched
    assert pw.x_handles(root.parent / "nowhere") == []


def test_x_queries_are_chunked_under_the_query_limit():
    handles = [f"person_{i:03d}" for i in range(60)]
    queries = pw.x_queries(handles, limit=200)
    assert all(len(q) <= 200 for q in queries)
    assert sorted(h for q in queries for h in handles if f"from:{h} " in q + " " or f"from:{h})" in q) == handles
    assert all(q.endswith("-is:retweet") for q in queries)


def test_the_linkedin_plan_names_people_with_the_guardrail_command_and_rotates(root):
    plan = pw.linkedin_plan(root, today=TODAY, limit=20)
    assert [p["name"] for p in plan] == ["Sam Arden", "Ana Ruiz"]
    assert "browser_research.py view --person \"Sam Arden\" --url https://www.linkedin.com/in/samarden/recent-activity/all/" \
        in plan[0]["command"] and "--named-by \"people-watch\"" in plan[0]["command"]
    pw.mark_read(root, "Sam Arden", TODAY)
    assert [p["name"] for p in pw.linkedin_plan(root, today=TODAY, limit=20)] == ["Ana Ruiz", "Sam Arden"]
    assert len(pw.linkedin_plan(root, today=TODAY, limit=1)) == 1


def test_the_linkedin_plan_never_exceeds_parts_a_share_of_the_daily_views(root):
    assert pw.A_DAILY_VIEWS == 20
    with pytest.raises(pw.WatchError, match="20"):
        pw.linkedin_plan(root, today=TODAY, limit=21)


def test_cli_check_and_plan(root, capsys):
    assert pw.main(["check", "--root", str(root)]) == 0
    assert "3 people (2 active)" in capsys.readouterr().out
    assert pw.main(["linkedin-plan", "--root", str(root), "--today", "2026-09-29"]) == 0
    assert "Sam Arden" in capsys.readouterr().out


def test_x_api_watch_adds_the_people_watch_handles(root, monkeypatch):
    import x_api as xa
    cfg = root / "targets" / "x-watch.yaml"
    cfg.write_text("queries: []\npeople: [someone_else]\nmax_results: 10\n")
    seen = []

    class C:
        js = root
    monkeypatch.setattr(xa, "_search_raw", lambda client, q, n: seen.append(q) or [])
    xa.watch(C(), cfg, today=TODAY)
    assert any("from:sam_arden" in q for q in seen) and any("from:someone_else" in q for q in seen)
