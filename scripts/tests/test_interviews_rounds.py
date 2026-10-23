"""Interviews booked count one per round, never one per event day. An
invite and the round it schedules are logged as two events of the same stage (screen 2026-09-29 for the
invite, screen 2026-10-02 when it was held), and they are one round. A round counts in the week it was booked, its
first event; a later stage (screen, then interviewing) is a new round. Fixture tree only."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import interviews as iv  # noqa: E402
import tracker  # noqa: E402

line = lambda at, t: tracker.format_event(at, t, "manual", 1.0, "x")   # noqa: E731


def app(root: Path, slug: str, events: list[str]) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    (d / "application.md").write_text(f"---\nslug: {slug}\nstage: applied\n---\n## Events\n" + "\n".join(events) + "\n",
                                      encoding="utf-8")


def test_an_invite_and_the_round_it_schedules_are_one(tmp_path):
    app(tmp_path, "quantco", [line("2026-09-29T16:49:00Z", "screen"), line("2026-10-02T17:00:00Z", "screen")])
    app(tmp_path, "formco", [line("2026-09-29T12:00:00Z", "applied"), line("2026-09-29T18:30:00Z", "screen")])
    b = iv.booked(tmp_path, date(2026, 10, 2))
    assert b["this_week"] == 2 and b["last_week"] == 0
    assert sorted((i["slug"], i["date"]) for i in b["items"]) == [("formco", "2026-09-29"), ("quantco", "2026-09-29")]


def test_a_later_stage_is_a_new_round_and_counts_when_booked(tmp_path):
    app(tmp_path, "acme", [line("2026-09-22T10:00:00Z", "screen"), line("2026-09-24T10:00:00Z", "screen"),
                           line("2026-09-30T10:00:00Z", "interview"), line("2026-10-01T10:00:00Z", "interview")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert (b["this_week"], b["last_week"]) == (1, 1)              # the screen round last week, the interview this week
    assert [i["stage"] for i in b["items"]] == ["screen", "interviewing"]


def test_a_round_booked_last_week_held_this_week_counts_last_week(tmp_path):
    app(tmp_path, "beta", [line("2026-09-25T10:00:00Z", "screen"), line("2026-09-29T10:00:00Z", "screen")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert (b["this_week"], b["last_week"]) == (0, 1)


def test_two_real_rounds_of_the_same_stage_count_twice(tmp_path):
    """Review finding 1: tech interview booked and held, then the onsite booked and held: two rounds."""
    app(tmp_path, "loop", [line("2026-09-22T10:00:00Z", "interview"), line("2026-09-24T10:00:00Z", "interview"),
                           line("2026-09-29T10:00:00Z", "interview"), line("2026-10-01T10:00:00Z", "interview")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert (b["this_week"], b["last_week"]) == (1, 1)


def test_a_long_gap_starts_a_new_round(tmp_path):
    app(tmp_path, "gap", [line("2026-09-01T10:00:00Z", "screen"), line("2026-09-29T10:00:00Z", "screen")])
    assert iv.booked(tmp_path, date(2026, 10, 1))["this_week"] == 1             # 28 days apart: not the same round


def test_a_rejection_breaks_the_round(tmp_path):
    """Review finding 2: the company re-engages after a rejection: a new round."""
    app(tmp_path, "back", [line("2026-09-25T10:00:00Z", "screen"), line("2026-09-26T10:00:00Z", "rejected"),
                           line("2026-09-30T10:00:00Z", "screen")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert (b["this_week"], b["last_week"]) == (1, 1)


def test_a_date_only_stamp_is_its_own_day(tmp_path):
    """Review finding 3: '2026-09-28' is Monday, never Sunday through a UTC midnight."""
    app(tmp_path, "day", [line("2026-09-28", "screen")])
    b = iv.booked(tmp_path, date(2026, 10, 1))
    assert b["this_week"] == 1 and b["items"][0]["date"] == "2026-09-28"


def test_an_override_names_its_stage(tmp_path):
    """Review finding 7: override:screen is the owner's correction to a screen; it books that round."""
    app(tmp_path, "ov", [line("2026-09-29T10:00:00Z", "override:screen")])
    assert iv.booked(tmp_path, date(2026, 10, 1))["this_week"] == 1
