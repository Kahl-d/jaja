"""pace.py `read` (ticket 2): page-heavy reads of LinkedIn and X count against a daily SOFT cap per platform. The cap
asks for a decision (check exits 1 until --over-soft-cap), never lifts a stop; a warning recorded with a read stops
every action on that platform for the rest of the day. Existing act caps and the 120 s gap are unchanged."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from disc_harness import *  # noqa: F401,F403

import pace  # noqa: E402

T0 = datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)


def test_read_caps_are_soft_and_per_platform():
    assert pace.READ_CAPS == {"linkedin": 150, "x": 100}
    assert pace.CAPS["linkedin"] == {"connect": 15, "message": 30, "comment": 10, "follow": 20}      # unchanged
    assert pace.CAPS["x"] == {"follow": 10, "message": 5, "comment": 5}


def test_reads_have_no_gap_and_do_not_move_the_act_gap(tmp_path):
    pace.record(tmp_path, "linkedin", "read", T0)
    ok, why = pace.check(tmp_path, "linkedin", "read", T0 + timedelta(seconds=1))
    assert ok and why == "go: linkedin read 1/150 today (soft cap)"
    assert pace.check(tmp_path, "linkedin", "connect", T0 + timedelta(seconds=1))[0]     # a read is not an act
    pace.record(tmp_path, "linkedin", "connect", T0 + timedelta(seconds=2))
    assert pace.check(tmp_path, "linkedin", "read", T0 + timedelta(seconds=3))[0]        # an act does not gate a read


def test_the_soft_cap_asks_then_goes_on_with_the_override(tmp_path):
    for i in range(100):
        pace.record(tmp_path, "x", "read", T0 + timedelta(seconds=i))
    ok, why = pace.check(tmp_path, "x", "read", T0 + timedelta(hours=1))
    assert not ok and "soft cap reached" in why and "100/100" in why and "--over-soft-cap" in why
    ok, why = pace.check(tmp_path, "x", "read", T0 + timedelta(hours=1), over_soft_cap=True)
    assert ok and "over the soft cap" in why and "100/100" in why
    assert pace.check(tmp_path, "x", "follow", T0 + timedelta(hours=1))[0]               # other actions unaffected
    ok, why = pace.record(tmp_path, "x", "read", T0 + timedelta(hours=1))
    assert ok and "past the soft cap" in why and "101/100" in why                        # recorded, not an error


def test_a_warning_with_a_read_stops_every_action_on_that_platform(tmp_path):
    ok, why = pace.record(tmp_path, "linkedin", "read", T0, warning="unusual activity notice")
    assert ok and "linkedin is stopped for the rest of the day" in why
    later = T0 + timedelta(hours=4)
    for action in ("read", "connect", "message", "comment", "follow"):
        ok, why = pace.check(tmp_path, "linkedin", action, later, over_soft_cap=True)
        assert not ok and "stopped for the day" in why and "unusual activity notice" in why, action
    assert pace.check(tmp_path, "x", "read", later)[0]                                   # the other platform runs
    assert pace.check(tmp_path, "linkedin", "read", T0 + timedelta(days=1))[0]           # a new Pacific day


def test_the_read_row_is_in_the_ledger_before_the_warning_row(tmp_path):
    import json
    pace.record(tmp_path, "x", "read", T0, note="x.com/someone", warning="rate limit notice")
    rows = [json.loads(line) for line in pace.ledger(tmp_path, T0).read_text().splitlines()]
    assert [(r["action"], r["note"]) for r in rows] == [("read", "x.com/someone"), ("warning", "rate limit notice")]


def test_a_warning_on_any_other_action_is_refused_nothing_written(tmp_path):
    try:
        pace.record(tmp_path, "x", "follow", T0, warning="x")
    except pace.PaceError as exc:
        assert "--warning goes with a read" in str(exc)
    else:
        raise AssertionError("--warning is for reads")
    assert not pace.ledger(tmp_path, T0).exists()


def test_cli_read_check_override_and_warning(tmp_path, capsys):
    base = ["--root", str(tmp_path), "--now", T0.isoformat()]
    assert pace.main(["record", "--platform", "x", "--action", "read", *base]) == 0
    assert pace.main(["check", "--platform", "x", "--action", "read", *base]) == 0
    for i in range(99):
        pace.record(tmp_path, "x", "read", T0)
    assert pace.main(["check", "--platform", "x", "--action", "read", *base]) == 1
    assert pace.main(["check", "--platform", "x", "--action", "read", "--over-soft-cap", *base]) == 0
    assert pace.main(["record", "--platform", "x", "--action", "read", "--warning", "limit notice", *base]) == 0
    assert pace.main(["check", "--platform", "x", "--action", "read", "--over-soft-cap", *base]) == 1
    assert "stopped for the day" in capsys.readouterr().out
