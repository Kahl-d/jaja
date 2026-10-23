"""browser_research.py click-check and plain connects: a plain LinkedIn connect or
follow is allowed only with --allow-connect, the owner's yes recorded (--yes) and pace.py's check at go; without them the
refusal says exactly that; notes, messages and every other signal stay refused. Writes only under tmp_path."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_research as br  # noqa: E402
import pace  # noqa: E402

NOW = datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc)      # 10:00 Pacific
RULE = "only with `python3 scripts/pace.py check` exit 0 and the owner's yes recorded"


@pytest.mark.parametrize("label", ["Connect", "Follow", "+ Follow", "Send without a note"])
def test_without_the_flag_a_plain_connect_is_refused_and_says_how(label):
    ok, reason = br.click_check(label)
    assert not ok and "signals to the person" in reason and RULE in reason, reason


def test_with_the_flag_a_yes_and_pace_go_it_is_allowed(tmp_path):
    ok, reason = br.click_check("Connect", allow_connect=True, yes="cards-2026-10-06 #3", root=tmp_path, now=NOW)
    assert ok and "pace.py record --platform linkedin --action connect" in reason, reason
    ok, reason = br.click_check("+ Follow", allow_connect=True, yes="A-2026-0412", root=tmp_path, now=NOW)
    assert ok and "--action follow" in reason, reason


def test_the_flag_without_a_recorded_yes_is_refused(tmp_path):
    ok, reason = br.click_check("Connect", allow_connect=True, yes="  ", root=tmp_path, now=NOW)
    assert not ok and "yes recorded" in reason


def test_pace_refusals_carry_through(tmp_path):
    pace.record(tmp_path, "linkedin", "connect", NOW - timedelta(seconds=30))
    ok, reason = br.click_check("Connect", allow_connect=True, yes="A-1", root=tmp_path, now=NOW)
    assert not ok and "wait" in reason                                    # 120 s between acts
    for i in range(pace.CAPS["linkedin"]["connect"] - 1):
        pace.record(tmp_path, "linkedin", "connect", NOW - timedelta(hours=1, minutes=5 * i))
    ok, reason = br.click_check("Connect", allow_connect=True, yes="A-1", root=tmp_path,
                                now=NOW + timedelta(minutes=10))
    assert not ok and "cap reached" in reason
    pace.record(tmp_path, "linkedin", "warning", NOW + timedelta(minutes=11), note="weekly limit notice")
    ok, reason = br.click_check("Follow", allow_connect=True, yes="A-1", root=tmp_path,
                                now=NOW + timedelta(minutes=20))
    assert not ok and "stopped for the day" in reason


@pytest.mark.parametrize("label", ["Message", "Add a note", "Comment", "Like", "Send", "Apply", "More", "Unfollow"])
def test_the_flag_never_opens_any_other_signal(tmp_path, label):
    ok, reason = br.click_check(label, allow_connect=True, yes="A-1", root=tmp_path, now=NOW)
    assert not ok, reason


def test_cli_passes_the_flag(tmp_path, capsys):
    root = ["--root", str(tmp_path)]
    assert br.main([*root, "click-check", "Connect"]) == 2
    assert RULE in capsys.readouterr().out
    assert br.main([*root, "click-check", "Connect", "--allow-connect", "--now", NOW.isoformat()]) == 2
    assert br.main([*root, "click-check", "Connect", "--allow-connect", "--yes", "A-1",
                    "--now", NOW.isoformat()]) == 0
    assert list(tmp_path.rglob("pace-*.jsonl")) == []          # the check never writes the ledger; record does
