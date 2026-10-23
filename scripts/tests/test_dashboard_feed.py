"""dashboard.py: the job-clock top line and state/home-feed.json (the HOME feed contract read by sweep.py)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import dashboard as db  # noqa: E402
import fetch_ats as fa  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

CLOCK = """\
updated: 2026-09-25
employer: Initech
employment_end: 2026-10-02
employment_end_confirmed: false
clock_days: 90
basis: 'The owner, 2026-09-25: "after that we have 90 days"'
new_employment_start: null
visa_allowance_days: null
visa_allowance_source: null
phases:
  - {weeks: [1, 2], name: resumes and first wave}
  - {weeks: [3, 8], name: volume and interviews}
  - {weeks: [9, 11], name: finals and offers}
  - {weeks: [12, 13], name: negotiate and decide}
"""
TOP = "Job clock: day -7 of 90 · 97 days left · ends 2026-12-31 (provisional) · starts 2026-10-03"
FEED_KEYS = {"project", "generated_at", "needs_you", "deadlines", "today"}


@pytest.fixture(autouse=True)
def clock_2030(monkeypatch):
    """--today always differs from the wall clock, so every run is stamped 12:00 UTC of --today."""
    monkeypatch.setattr(tr, "wall_clock", lambda: datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc), raising=False)


def _clock(root, **changes):
    text = CLOCK
    for key, value in changes.items():
        text = "\n".join(f"{key}: {value}" if line.startswith(f"{key}:") else line for line in text.splitlines()) + "\n"
    (root / "clock.yaml").write_text(text, encoding="utf-8")


def run(root, os_root, day="2026-09-25", *extra):
    assert db.main(["--root", str(root), "--os-root", str(os_root), "--today", day, *extra]) == 0
    feed_path = root / "state" / "home-feed.json"
    feed = json.loads(feed_path.read_text()) if feed_path.exists() else None
    return (root / "DASHBOARD.md").read_text(encoding="utf-8") if (root / "DASHBOARD.md").exists() else None, feed


def _titles(rows):
    return [r["title"] for r in rows]


def _write(path, meta, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump(meta, body), encoding="utf-8")


# ----------------------------------------------------------------------------- the clock line
def test_the_job_clock_is_the_top_line(root, os_root):
    _clock(root)
    text, _ = run(root, os_root)
    lines = text.splitlines()
    assert lines[0].startswith("<!-- GENERATED") and lines[1] == "# Job search dashboard"
    assert lines[3] == TOP
    assert lines[5].startswith("Week 0 · before the clock starts · unemployment days used: 0")
    data = json.loads((root / "state" / "dashboard.json").read_text())
    assert (data["clock"]["day"], data["clock"]["days_left"], data["clock"]["label"]) == (-7, 97, "provisional")


def test_the_clock_line_follows_the_run_date(root, os_root):
    _clock(root, employment_end_confirmed="true")
    text, _ = run(root, os_root, "2026-10-03")
    assert "Job clock: day 1 of 90 · 89 days left · ends 2026-12-31 (confirmed)" in text.splitlines()
    assert "Week 1 · resumes and first wave · unemployment days used: 1 (since 2026-10-03)" in text


def test_a_missing_clock_is_not_empty(root, os_root):
    text, feed = run(root, os_root)
    assert "Job clock: no data yet (clock.yaml is missing)" in text.splitlines()
    assert json.loads((root / "state" / "dashboard.json").read_text())["clock"] is None
    assert any(n["title"] == "Job clock missing" for n in feed["needs_you"])


def test_a_malformed_clock_is_reported_not_hidden(root, os_root):
    _clock(root, clock_days="0")
    text, feed = run(root, os_root)
    assert "Job clock: unreadable (clock.yaml: `clock_days: 0`" in text
    assert any(n["title"] == "Job clock unreadable" and "clock_days" in n["why"] for n in feed["needs_you"])


# ----------------------------------------------------------------------------- the feed
def test_the_feed_carries_the_clock_and_keeps_the_contract(root, os_root):
    _clock(root)
    text, feed = run(root, os_root)
    assert set(feed) == FEED_KEYS and feed["project"] == "job-search"
    assert feed["generated_at"] == "2026-09-25T12:00:00Z"
    assert all(set(n) == {"title", "why", "link"} for n in feed["needs_you"])
    assert all(set(d) == {"title", "date", "hard", "link"} for d in feed["deadlines"])
    assert all(set(t) == {"title", "link"} for t in feed["today"])
    confirm = next(n for n in feed["needs_you"] if n["title"] == "Confirm the last working day at Initech")
    assert "2026-10-02 (provisional)" in confirm["why"] and confirm["link"].endswith("clock.yaml")
    deadlines = {d["title"]: d for d in feed["deadlines"]}
    end = deadlines["90-day job clock ends: new job needed (provisional)"]
    assert (end["date"], end["hard"]) == ("2026-12-31", True)
    last = deadlines["Last working day at Initech (provisional)"]
    assert (last["date"], last["hard"]) == ("2026-10-02", True)
    assert feed["today"][0]["title"] == TOP


def test_a_confirmed_clock_asks_nothing_and_stops_saying_provisional(root, os_root):
    _clock(root, employment_end_confirmed="true")
    _, feed = run(root, os_root)
    assert not any("Confirm the last working day" in t for t in _titles(feed["needs_you"]))
    assert "90-day job clock ends: new job needed" in _titles(feed["deadlines"])
    assert "Last working day at Initech" in _titles(feed["deadlines"])


def test_a_past_last_working_day_is_no_longer_a_deadline(root, os_root):
    _clock(root)
    _, feed = run(root, os_root, "2026-10-05")
    titles = _titles(feed["deadlines"])
    assert "Last working day at Initech (provisional)" not in titles
    assert "90-day job clock ends: new job needed (provisional)" in titles


def test_a_new_job_drops_the_clock_deadline(root, os_root):
    _clock(root, new_employment_start="2026-11-16", employment_end_confirmed="true")
    _, feed = run(root, os_root, "2026-11-20")
    assert not any(t.startswith("90-day job clock ends") for t in _titles(feed["deadlines"]))
    assert feed["today"][0]["title"].startswith("Job clock: stopped · new employment starts 2026-11-16")


def _job(root, n, **kw):
    fields = dict(title=f"ML Engineer {n}", source="greenhouse", source_url=f"https://example.com/jobs/{n}",
                  apply_url=None, ats_vendor="greenhouse", posted_at="2026-09-23T09:00:00Z",
                  location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                  employment_type="full-time", jd_text="JD", now=NOW, req_id=str(n))
    extra = {k: kw.pop(k) for k in list(kw) if k not in fields}
    fields.update(kw)
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, **fields)
    rec.update(extra)
    fa.upsert_jobs([rec], root, fields["now"])
    job = fa.load_jobs(root)[rec["id"]]
    job.update(extra)
    fa.save_job(root, job)
    return rec["id"]


def _application(root, n, events, next_action=None, next_action_at=None):
    slug, _ = tr.new_application(root, _job(root, n, now=NOW - timedelta(days=30)), NOW - timedelta(days=20))
    for i, event in enumerate(events):
        tr.append_event(root, slug, event, now=NOW - timedelta(days=15 - i))
    path = root / "applications" / slug / "application.md"
    meta, body = fm.load(path)
    meta["next_action"], meta["next_action_at"] = next_action, next_action_at
    path.write_text(fm.dump(meta, body), encoding="utf-8")
    return slug


def test_offers_interviews_reviews_and_expiring_approvals_reach_the_feed(root, os_root):
    _clock(root, employment_end_confirmed="true")
    offer = _application(root, 1, ["applied", "recruiter_screen", "offer"], "decide on the offer", "2026-09-30")
    _write(root / "applications" / offer / "offer.md", {"company": "Acme", "title": "ML Engineer 1",
                                                         "deadline": "2026-10-01", "source": "offer email 2026-09-24"})
    screen = _application(root, 2, ["applied", "recruiter_screen"], "recruiter call", "2026-09-26")
    today_round = _application(root, 3, ["applied", "technical_interview"], "system design round", "2026-09-25")
    _application(root, 4, ["applied", "recruiter_screen"], "hiring manager call", "2026-10-15")      # 20 days out
    _job(root, 5, fit_verdict="unscored", status="fit_checked", gate="review",
         gate_reasons=["sponsorship: no statement"], now=NOW)
    appr = os_root / "work" / "approvals"
    _write(appr / "A-2026-0001.md", {"id": "A-2026-0001", "action": "send-email", "status": "pending",
                                     "project": "job-search", "created": "2026-09-19", "expires": "2026-09-26"})
    _write(appr / "A-2026-0002.md", {"id": "A-2026-0002", "action": "send-email", "status": "pending",
                                     "project": "job-search", "created": "2026-09-24", "expires": "2026-10-01"})
    text, feed = run(root, os_root)

    needs = {n["title"]: n for n in feed["needs_you"]}
    assert "offer awaiting your decision by 2026-10-01" in needs["Offer from Acme: ML Engineer 1"]["why"]
    assert needs["Offer from Acme: ML Engineer 1"]["link"].endswith(f"applications/{offer}/offer.md")
    assert not any("Gate review" in t for t in needs)    # gate reviews are fit-scorer's, never the owner's
    deadlines = {d["title"]: d for d in feed["deadlines"]}
    assert (deadlines["Offer deadline: Acme, ML Engineer 1"]["date"], deadlines["Offer deadline: Acme, ML Engineer 1"]["hard"]) == ("2026-10-01", True)
    assert deadlines["screen with Acme: ML Engineer 2"]["date"] == "2026-09-26"
    assert deadlines["interviewing with Acme: ML Engineer 3"]["date"] == "2026-09-25"
    assert not any("ML Engineer 4" in t for t in deadlines)                 # beyond 14 days
    assert (deadlines["Approval A-2026-0001 expires (send-email)"]["date"], deadlines["Approval A-2026-0001 expires (send-email)"]["hard"]) == ("2026-09-26", False)
    assert deadlines["Approval A-2026-0001 expires (send-email)"]["link"].endswith("work/approvals/A-2026-0001.md")
    assert not any("A-2026-0002" in t for t in deadlines)                   # expires in 6 days
    assert "interviewing today: Acme, ML Engineer 3 · system design round" in _titles(feed["today"])
    assert not any("ML Engineer 2" in t for t in _titles(feed["today"]))
    nk = text.split("## Needs Alex", 1)[1].split("\n## ", 1)[0]
    assert "Offers awaiting your decision: 1" in nk and "Acme: ML Engineer 1 · deadline 2026-10-01" in nk
    assert screen and today_round


def test_mail_review_and_follow_ups_are_one_line_each(root, os_root):
    _clock(root, employment_end_confirmed="true")
    (root / "state").mkdir(parents=True)
    (root / "state" / "mail-events.jsonl").write_text(
        json.dumps({"message_id": "m1", "decision": "review"}) + "\n", encoding="utf-8")
    _write(root / "prospects" / "jane-doe.md", {"id": "jane-doe", "name": "Jane Doe", "company": "Acme",
                                                "status": "replied", "priority": "P0", "next_touch_at": "2026-09-24"})
    _, feed = run(root, os_root)
    assert "Mail review: 1 message(s) need your call" in _titles(feed["needs_you"])
    assert "Follow-ups due: 1" in _titles(feed["today"])


def test_dry_run_writes_no_feed(root, os_root):
    _clock(root)
    text, feed = run(root, os_root, "2026-09-25", "--dry-run")
    assert text is None and feed is None and not (root / "state").exists()


def test_links_are_repo_relative_inside_the_workspace(tmp_path):
    os_root = tmp_path / "workspace"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    _clock(js)
    _, feed = run(js, os_root)
    assert all(item["link"].startswith("domains/career/projects/job-search/")
               for rows in (feed["needs_you"], feed["deadlines"], feed["today"]) for item in rows)


def test_pipeline_style_write_also_refreshes_the_feed(root, os_root):
    _clock(root)
    data = db.build(root, os_root, datetime(2026, 9, 25, 12, tzinfo=timezone.utc))
    db.write(root, data)
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    assert feed["generated_at"] == data["generated_at"] and feed["today"][0]["title"] == TOP


def test_the_feed_passes_the_sweep_feed_contract(root, os_root):
    import sweep                                   # the OS kernel is on sys.path through js_common
    _clock(root)
    _, feed = run(root, os_root)
    assert sweep.validate_feed(feed) == []


# ----------------------------------------------------------------------------- the department day
def test_an_evening_run_counts_the_department_day_not_the_utc_day(root, os_root, monkeypatch):
    """18:32 in San Francisco on 2026-09-24 is 01:32 UTC on 2026-09-25: every day count is for 2026-09-24."""
    _clock(root)
    _write(root / "legal" / "timers.md", {"title": "t", "type": "legal-timers", "last_working_day": "2026-10-01"})
    monkeypatch.setattr(tr, "wall_clock", lambda: datetime(2026, 9, 25, 1, 32, tzinfo=timezone.utc), raising=False)
    assert db.main(["--root", str(root), "--os-root", str(os_root)]) == 0
    data = json.loads((root / "state" / "dashboard.json").read_text())
    assert (data["clock"]["day"], data["clock"]["days_left"]) == (-8, 98)
    assert data["visa_timers"]["last_working_day"]["days_left"] == 7
    assert "Job clock: day -8 of 90 · 98 days left" in (root / "DASHBOARD.md").read_text()
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    assert any(r["title"].startswith("Job clock: day -8 of 90") for r in feed["today"])


def test_the_feed_reads_its_day_from_the_generated_time_in_the_department_zone():
    data = {"generated_at": "2026-09-25T01:32:00Z", "clock": None}
    assert db.feed_day(data).isoformat() == "2026-09-24"
