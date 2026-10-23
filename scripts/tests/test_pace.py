"""pace.py (human pace): a hard daily counter for outward acts on LinkedIn and X. A day ledger
per Pacific day; caps per action; 120 s between two acts on one platform; a recorded warning or CAPTCHA stops that
platform for the day. Tiny fixture roots; no network."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import pace  # noqa: E402

T0 = datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)                 # 09:00 Pacific


def test_caps_are_the_charter_numbers():
    assert pace.CAPS["linkedin"] == {"connect": 15, "message": 30, "comment": 10, "follow": 20}
    assert pace.CAPS["x"] == {"follow": 10, "message": 5, "comment": 5}
    assert pace.GAP_S == 120


def test_check_go_then_too_soon_then_go(tmp_path):
    ok, why = pace.check(tmp_path, "linkedin", "connect", T0)
    assert ok and "0/15" in why
    pace.record(tmp_path, "linkedin", "connect", T0)
    ok, why = pace.check(tmp_path, "linkedin", "message", T0 + timedelta(seconds=30))
    assert not ok and "wait 90 s" in why                                # the gap is per platform, any action
    assert pace.check(tmp_path, "x", "follow", T0 + timedelta(seconds=30))[0]          # another platform is free
    assert pace.check(tmp_path, "linkedin", "message", T0 + timedelta(seconds=120))[0]


def test_the_cap_stops_the_action_not_the_others(tmp_path):
    for i in range(15):
        pace.record(tmp_path, "linkedin", "connect", T0 + timedelta(minutes=3 * i))
    later = T0 + timedelta(hours=2)
    ok, why = pace.check(tmp_path, "linkedin", "connect", later)
    assert not ok and "cap" in why and "15/15" in why
    assert pace.check(tmp_path, "linkedin", "comment", later)[0]


def test_a_warning_or_captcha_stops_the_platform_for_the_day(tmp_path):
    pace.record(tmp_path, "linkedin", "captcha", T0, note="security check page")
    ok, why = pace.check(tmp_path, "linkedin", "comment", T0 + timedelta(hours=3))
    assert not ok and "stopped for the day" in why and "security check page" in why
    assert pace.check(tmp_path, "x", "follow", T0 + timedelta(hours=3))[0]
    tomorrow = T0 + timedelta(days=1)                                   # a new Pacific day, a new ledger
    assert pace.check(tmp_path, "linkedin", "comment", tomorrow)[0]


def test_the_day_is_pacific_and_the_ledger_is_a_jsonl_per_day(tmp_path):
    late = datetime(2026, 10, 7, 6, 30, tzinfo=timezone.utc)            # 23:30 Pacific on 10-06
    pace.record(tmp_path, "x", "follow", late, note="@someone")
    path = tmp_path / "state" / "posts" / "pace-2026-10-06.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["platform"] == "x" and row["action"] == "follow" and row["note"] == "@someone"


def test_an_action_a_platform_does_not_have_is_refused(tmp_path):
    try:
        pace.check(tmp_path, "x", "connect", T0)
    except pace.PaceError as exc:
        assert "x has no connect" in str(exc)
    else:
        raise AssertionError("x connect must be refused")


def test_cli_exit_codes(tmp_path, capsys):
    base = ["--root", str(tmp_path), "--now", T0.isoformat()]
    assert pace.main(["check", "--platform", "linkedin", "--action", "connect", *base]) == 0
    assert pace.main(["record", "--platform", "linkedin", "--action", "connect", *base]) == 0
    assert pace.main(["check", "--platform", "linkedin", "--action", "connect", *base]) == 1
    assert pace.main(["record", "--platform", "linkedin", "--action", "warning", "--note", "limit notice", *base]) == 0
    out = capsys.readouterr().out
    assert "go:" in out and "wait" in out and "recorded" in out
    assert pace.main(["check", "--platform", "x", "--action", "connect", *base]) == 2
