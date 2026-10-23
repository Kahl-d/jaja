"""Charter section 11: interviews held counted apart from booked, and upcoming rounds with a structured time
(interviews.rounds / held / upcoming / totals; tracker metrics; the brief's line). Fixture tree only."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import interviews as iv  # noqa: E402
import tracker  # noqa: E402

KEYS = {"slug", "company", "stage", "booked", "status", "round_at", "date_pt", "time_pt", "times_pt", "held_on",
        "held_by", "ended", "flag"}


def ev(at: str, t: str, evidence: str = "x") -> str:
    return tracker.format_event(at, t, "manual", 1.0, evidence)


def app(root: Path, slug: str, events: list[str], company: str = "Acme") -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    (d / "application.md").write_text(f"---\nslug: {slug}\ncompany: {company}\nstage: applied\n---\n## Events\n"
                                      + "\n".join(events) + "\n", encoding="utf-8")


def test_a_held_event_counts_held_on_its_day_and_booked_on_the_booking(tmp_path):
    app(tmp_path, "quantco", [ev("2026-09-29T16:49:00Z", "screen"), ev("2026-10-02T17:00:00Z", "screen")])
    today = date(2026, 10, 2)
    assert iv.booked(tmp_path, today)["this_week"] == 1
    h = iv.held(tmp_path, today)
    assert h["this_week"] == 1 and h["items"][0]["date"] == "2026-10-02" and h["items"][0]["by"] == "held event"
    assert iv.totals(tmp_path, today) == {"booked": 1, "held": 1, "upcoming": 0, "unconfirmed": 0, "ended": 0}


def test_a_later_stage_means_the_round_before_was_held(tmp_path):
    app(tmp_path, "acme", [ev("2026-09-22T10:00:00Z", "screen", "round_at=2026-09-24T17:00:00Z"),
                           ev("2026-09-30T10:00:00Z", "interview")])
    [screen, onsite] = [iv.round_view(r, date(2026, 10, 1)) for r in iv.rounds(tmp_path)]
    assert (screen["status"], screen["held_on"], screen["held_by"]) == ("held", "2026-09-24", "next round")
    assert onsite["status"] == "upcoming" and onsite["flag"].startswith("no round time recorded")


def test_a_passed_time_without_a_held_record_is_unconfirmed_never_held(tmp_path):
    app(tmp_path, "beta", [ev("2026-09-22T10:00:00Z", "screen", "round_at=2026-09-25T18:00:00Z")])
    [v] = [iv.round_view(r, date(2026, 10, 1)) for r in iv.rounds(tmp_path)]
    assert v["status"] == "unconfirmed" and v["held_on"] is None and "no held event" in v["flag"]
    assert iv.held(tmp_path, date(2026, 10, 1))["last_week"] == 0 and iv.upcoming(tmp_path, date(2026, 10, 1)) == []


def test_two_recorded_times_are_a_conflict_flagged_never_guessed(tmp_path):
    """A round on 2026-10-09: the invite says 11:30 PT, the calendar 11:45 PT."""
    app(tmp_path, "formco", [ev("2026-09-29T18:30:00Z", "screen", "invite; round_at=2026-10-09T18:30:00Z"),
                                ev("2026-09-30T16:00:00Z", "screen", "calendar; round_at=2026-10-09T18:45:00Z")],
        company="FormCo")
    [u] = iv.upcoming(tmp_path, date(2026, 10, 6))
    assert set(u) == KEYS
    assert u["status"] == "upcoming" and u["round_at"] is None and u["time_pt"] is None
    assert u["times_pt"] == ["2026-10-09 11:30", "2026-10-09 11:45"]
    assert u["flag"] == "records disagree: 2026-10-09 11:30 PT vs 2026-10-09 11:45 PT; confirm the time, never guess"
    assert iv.held(tmp_path, date(2026, 10, 6))["this_week"] == 0


def test_one_time_is_structured_and_a_reschedule_replaces_it(tmp_path):
    app(tmp_path, "gamma", [ev("2026-10-01T10:00:00Z", "screen", "round_at=2026-10-08T17:00:00Z"),
                            ev("2026-10-02T10:00:00Z", "screen", "moved [reschedule] round_at=2026-10-09T18:30:00Z")])
    [u] = iv.upcoming(tmp_path, date(2026, 10, 6))
    assert (u["round_at"], u["date_pt"], u["time_pt"], u["flag"]) == ("2026-10-09T18:30:00Z", "2026-10-09", "11:30", None)
    assert iv.booked(tmp_path, date(2026, 10, 2))["this_week"] == 1


def test_upcoming_is_soonest_first_and_untimed_last(tmp_path):
    app(tmp_path, "late", [ev("2026-10-01T10:00:00Z", "screen", "round_at=2026-10-12T17:00:00Z")])
    app(tmp_path, "soon", [ev("2026-10-02T10:00:00Z", "interview", "round_at=2026-10-07T17:00:00Z")])
    app(tmp_path, "untimed", [ev("2026-10-03T10:00:00Z", "screen")])
    assert [u["slug"] for u in iv.upcoming(tmp_path, date(2026, 10, 6))] == ["soon", "late", "untimed"]


def test_a_rejection_ends_an_unheld_round(tmp_path):
    app(tmp_path, "delta", [ev("2026-09-22T10:00:00Z", "screen"), ev("2026-09-23T10:00:00Z", "rejected")])
    assert iv.totals(tmp_path, date(2026, 10, 1)) == {"booked": 1, "held": 0, "upcoming": 0, "unconfirmed": 0,
                                                      "ended": 1}


def test_the_line_metrics_and_cli_carry_held(tmp_path, capsys):
    app(tmp_path, "quantco", [ev("2026-09-29T16:49:00Z", "applied"), ev("2026-09-30T16:49:00Z", "screen"),
                                ev("2026-10-02T17:00:00Z", "screen")])
    assert "Held this week: 1; last week 0." in iv.line(tmp_path, date(2026, 10, 2), datetime(2026, 10, 2, tzinfo=timezone.utc))
    m = tracker.metrics(tmp_path, datetime(2026, 10, 2, 20, tzinfo=timezone.utc))
    assert m["interviews"] == {"booked": 1, "held": 1, "upcoming": 0, "unconfirmed": 0, "ended": 0}
    assert iv.main(["--root", str(tmp_path), "--today", "2026-10-02", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"booked", "held", "upcoming", "totals", "target", "rates"} <= set(data) and data["held"]["this_week"] == 1
