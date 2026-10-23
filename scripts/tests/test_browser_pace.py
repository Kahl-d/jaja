"""browser_pace.py: paced, never-bulk browser reads, and a block stops every lane."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_pace as bp  # noqa: E402

T0 = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def test_reads_of_one_host_are_spaced(tmp_path):
    st = tmp_path / "pace.json"
    url = "https://www.careers.example.test/jobs/123"
    assert bp.check(url, T0, st, tmp_path)[0]
    bp.mark(url, T0, st)
    ok, msg = bp.check("https://careers.example.test/jobs/456", T0 + timedelta(seconds=10), st, tmp_path)
    assert not ok and msg.startswith("WAIT 51 s")                       # www. and bare host are one host
    assert bp.check(url, T0 + timedelta(seconds=61), st, tmp_path)[0]
    assert bp.check("https://jobs.acme.test/x", T0 + timedelta(seconds=10), st, tmp_path)[0]   # another host: free


def test_a_block_stops_every_read_until_its_time(tmp_path):
    st = tmp_path / "pace.json"
    url = "https://www.careers.example.test/jobs/123"
    bp.blocked(url, T0, 24, "rate limited: temporarily blocked", st)
    ok, msg = bp.check(url, T0 + timedelta(hours=3), st, tmp_path)
    assert not ok and msg.startswith("BLOCKED careers.example.test") and "do not retry" in msg
    assert bp.check(url, T0 + timedelta(hours=25), st, tmp_path)[0]


def test_a_platform_may_ask_for_a_longer_gap(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "platforms.yaml").write_text(
        "platforms:\n  - id: example\n    url: https://www.careers.example.test\n    browser_pacing_s: 120\n", encoding="utf-8")
    assert bp.gap_for("careers.example.test", tmp_path) == 120
    assert bp.gap_for("jobs.acme.test", tmp_path) == 60
