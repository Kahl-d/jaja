"""interviews.py: interviews booked this week, measured channel rates and the weekly target (the weekly target)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import interviews as iv  # noqa: E402


def app(root: Path, slug: str, events: list[str]) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    body = "\n".join(events)
    (d / "application.md").write_text(f"---\nslug: {slug}\nstage: applied\n---\n## Events\n{body}\n", encoding="utf-8")


def test_booked_counts_interview_events_by_week(tmp_path):
    import tracker
    line = lambda at, t: tracker.format_event(at, t, "manual", 1.0, "x")
    app(tmp_path, "a", [line("2026-09-29T17:00:00Z", "applied"), line("2026-09-30T18:00:00Z", "screen")])
    app(tmp_path, "b", [line("2026-09-22T17:00:00Z", "interview"), line("2026-09-22T19:00:00Z", "interview")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert (b["this_week"], b["last_week"], b["week_of"]) == (1, 1, "2026-09-28")   # one per application and day


def test_target_is_the_owners_or_none(tmp_path):
    assert iv.target(tmp_path) is None
    (tmp_path / "state" / "recruiter").mkdir(parents=True)
    (tmp_path / "state" / "recruiter" / "target.yaml").write_text("weekly_interviews: 3\n", encoding="utf-8")
    assert iv.target(tmp_path) == 3


def test_propose_uses_the_measured_rates():
    assert iv.propose({"warm": 0.25, "cold": 0.03}, 4, 20) == 1.6


def test_rates_count_interviews_not_rejections(tmp_path):
    import tracker
    from datetime import datetime, timezone
    line = lambda at, t: tracker.format_event(at, t, "manual", 1.0, "x")

    def via(slug, v, events):
        app(tmp_path, slug, events)
        p = tmp_path / "applications" / slug / "application.md"
        p.write_text(p.read_text(encoding="utf-8").replace("stage: applied\n", f"stage: applied\napplied_via: {v}\n"),
                     encoding="utf-8")
    via("cold-rejected", "ats", [line("2026-09-20T17:00:00Z", "applied"), line("2026-09-25T17:00:00Z", "rejected")])
    via("cold-quiet", "ats", [line("2026-09-20T17:00:00Z", "applied")])
    via("warm-screen", "referral", [line("2026-09-20T17:00:00Z", "applied"), line("2026-09-24T17:00:00Z", "screen")])
    r = iv.rates(tmp_path, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert (r["cold_k"], r["cold_n"], r["warm_k"], r["warm_n"]) == (0, 2, 1, 1)   # a rejection is a reply, not an interview


def test_booked_reads_mixed_case_event_types(tmp_path):
    # review finding 5: booked() and rates() must agree on a mixed-case event type
    import tracker
    line = lambda at, t: tracker.format_event(at, t, "manual", 1.0, "x")
    app(tmp_path, "a", [line("2026-09-29T17:00:00Z", "applied"), line("2026-09-30T18:00:00Z", "Screen")])
    assert iv.booked(tmp_path, date(2026, 10, 1))["this_week"] == 1
