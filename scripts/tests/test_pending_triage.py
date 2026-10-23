"""pending_triage.py: a dry-run proposal for the postings parked in jobs-pending/."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp

import fetch_ats as fa
import pending_triage as pt

TODAY = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(pt, "_sleep", lambda s: None)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "js"
    for d in ("jobs", "jobs-pending", "targets"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Tiny AI, slug: tiny-ai, tier: strong, ats_vendor: ashby, board_token: tiny}\n")
    return root


def _park(root, jid, *, source="hn", company="acme", title="AI Engineer", url=None, gate="pass", posted=None):
    rec = fa.make_record({"name": company.title(), "slug": company}, title=title, source=source,
                         source_url=url or f"https://news.ycombinator.com/item?id={jid}", apply_url=None,
                         ats_vendor="unknown", posted_at=posted or (TODAY - timedelta(days=10)).isoformat(),
                         location="San Francisco, CA", remote="onsite", comp=(None, None, None), employment_type=None,
                         jd_text="We build LLM agents in Python.", now=NOW)
    rec.update({"id": jid, "gate": gate, "status": "fit_checked" if gate != "fail" else "skipped"})
    fa.save_job(root, rec)                                                      # written to jobs/ first ...
    src = root / "jobs" / f"{jid}.md"
    src.rename(root / "jobs-pending" / f"{jid}.md")                             # ... then parked
    return rec


def test_board_postings_are_left_to_the_board_scan_and_others_are_scored(world):
    _park(world, "tiny-1", source="ashby", company="tiny-ai", url="https://jobs.ashbyhq.com/tiny/1")
    _park(world, "hn-1")
    _park(world, "hn-2", title="Sales Director")
    _park(world, "hn-fail", gate="fail")
    out = pt.triage(world, today=TODAY)
    assert out["counts"]["board_rescan"] == 1 and out["counts"]["gate_fail"] == 1
    assert [p["id"] for p in out["proposed"]] == ["hn-1"]                    # at the import cut, gates pass
    assert out["counts"]["below_cut"] == 1


def test_the_import_cut_is_its_own_and_sits_under_the_daily_cut(world):
    _park(world, "hn-1")                                                     # 10 days old: about 67
    assert pt.PENDING_CUT < 72
    assert [p["id"] for p in pt.triage(world, today=TODAY)["proposed"]] == ["hn-1"]
    assert pt.triage(world, today=TODAY, cut=72)["proposed"] == []


def test_a_posting_already_in_jobs_is_not_proposed_again(world):
    _park(world, "hn-1")
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title="AI Engineer", source="hn",
                         source_url="https://news.ycombinator.com/item?id=hn-1", apply_url=None, ats_vendor="unknown",
                         posted_at=None, location=None, remote="unknown", comp=(None, None, None), employment_type=None,
                         jd_text="x", now=NOW)
    rec["id"] = "live-copy"
    fa.save_job(world, rec)
    out = pt.triage(world, today=TODAY)
    assert out["proposed"] == [] and out["counts"]["already_in_jobs"] == 1


def test_the_liveness_check_marks_gone_postings(world):
    _park(world, "hn-1", url="https://jobs.example.com/1")
    _park(world, "hn-2", url="https://jobs.example.com/2")
    http = FakeHttp({("GET", "https://jobs.example.com/robots.txt"): (404, "User-agent: *\nDisallow: /"),  # 4xx: no rules
                     ("GET", "https://jobs.example.com/1"): (200, "<h1>AI Engineer</h1>"),
                     ("GET", "https://jobs.example.com/2"): (404, "")})
    out = pt.triage(world, today=TODAY, http=http, live=True)
    live = {p["id"]: p["live"] for p in out["proposed"]}
    assert live == {"hn-1": True, "hn-2": False}
    assert [c[1] for c in http.calls].count("https://jobs.example.com/robots.txt") == 1   # once per host


def test_the_liveness_check_obeys_robots_txt(world):
    _park(world, "hn-1", url="https://jobs.example.com/private/1")
    _park(world, "hn-2", url="https://down.example.com/2")
    http = FakeHttp({("GET", "https://jobs.example.com/robots.txt"): (200, "User-agent: *\nDisallow: /private/\n"),
                     ("GET", "https://down.example.com/robots.txt"): (503, "")})
    out = pt.triage(world, today=TODAY, http=http, live=True)
    notes = {p["id"]: (p["live"], p.get("live_note")) for p in out["proposed"]}
    assert notes["hn-1"][0] is None and "robots.txt" in notes["hn-1"][1]            # never fetched
    assert notes["hn-2"][0] is None and "robots.txt" in notes["hn-2"][1]            # 5xx robots = full disallow
    assert {c[1] for c in http.calls} == {"https://jobs.example.com/robots.txt", "https://down.example.com/robots.txt"}


def test_it_never_writes_to_jobs(world):
    _park(world, "hn-1")
    pt.triage(world, today=TODAY)
    assert list((world / "jobs").glob("*.md")) == []


def test_render_names_the_counts_and_the_import_command(world):
    _park(world, "hn-1")
    text = pt.render(pt.triage(world, today=TODAY))
    assert "board_rescan" in text and "hn-1" in text and "mv jobs-pending/hn-1.md jobs/" in text


def test_render_keeps_each_row_one_table_row(world):
    _park(world, "hn-1", title="AI Engineer | Remote \u2014 US")
    row = [line for line in pt.render(pt.triage(world, today=TODAY)).splitlines() if "hn-1" in line][0]
    assert row.count("|") == 8 and "\u2014" not in row and "AI Engineer / Remote - US" in row


def test_proposals_come_highest_score_first(world):
    _park(world, "hn-a", posted=(TODAY - timedelta(days=20)).isoformat())
    _park(world, "hn-b", posted=(TODAY - timedelta(days=4)).isoformat())
    scores = [p["hunt_score"] for p in pt.triage(world, today=TODAY, cut=0)["proposed"]]
    assert [p["id"] for p in pt.triage(world, today=TODAY, cut=0)["proposed"]] == ["hn-b", "hn-a"]
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[1]


def test_a_blocked_page_is_unknown_not_gone(world):
    _park(world, "hn-1", url="https://jobs.example.com/1")
    _park(world, "hn-2", url="https://jobs.example.com/2")
    _park(world, "hn-3", url="https://jobs.example.com/3")
    _park(world, "hn-4", url="https://jobs.example.com/4")
    http = FakeHttp({("GET", "https://jobs.example.com/robots.txt"): (404, ""),
                     ("GET", "https://jobs.example.com/1"): (403, ""),             # bot wall: says nothing
                     ("GET", "https://jobs.example.com/2"): (410, ""),             # gone
                     ("GET", "https://jobs.example.com/3"): TimeoutError("slow"),
                     ("GET", "https://jobs.example.com/4"): (302, "")})             # a redirect not followed: unknown
    out = {p["id"]: p for p in pt.triage(world, today=TODAY, http=http, live=True)["proposed"]}
    assert out["hn-1"]["live"] is None and "HTTP 403" in out["hn-1"]["live_note"]
    assert out["hn-2"]["live"] is False
    assert out["hn-3"]["live"] is None and "no answer" in out["hn-3"]["live_note"]
    assert out["hn-4"]["live"] is None
    assert "unknown (HTTP 403" in pt.render({"date": "2026-09-30", "cut": 60, "daily_cut": 72, "live": True,
                                             "counts": {}, "proposed": [out["hn-1"]]})


def test_a_posting_the_scout_already_skipped_is_not_proposed(world):
    _park(world, "hn-1")
    rec = _park(world, "hn-2")
    path = world / "jobs-pending" / "hn-2.md"
    path.write_text(path.read_text().replace("status: fit_checked", "status: skipped"))
    out = pt.triage(world, today=TODAY)
    assert [p["id"] for p in out["proposed"]] == ["hn-1"] and out["counts"]["scout_skipped"] == 1
