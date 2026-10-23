"""T-2026-0027: a run the Mac slept through still ends itself at its run guard, and the receipt says the machine slept.

The js-hunt-boards run of 2026-10-04 started 22:11:29 PDT with budget_min 20 (guard limit 2100 s). The lid closed at
22:30:14 (pmset: 'Clamshell Sleep' on battery) and the Mac stayed asleep, with dark wakes of a few seconds, until the
app killed the run at its 2 h background limit at 00:36:55. Awake time in those 2 h 25 min was about 20 minutes. On
macOS a Python timed wait (threading.Timer, faulthandler.dump_traceback_later) counts mach_absolute_time, which stops
while the machine sleeps, so neither the guard (2100 s) nor the backstop (2160 s) ever came due; the app's limit counts
the wall clock and did. The test replays that run in a process: the main thread blocks in a socket read with no
timeout, and the wall clock jumps 2 h (the sleep) while almost no awake time passes."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SLEPT_S = 2 * 3600.0


def test_a_run_the_machine_slept_through_ends_at_its_guard_with_a_true_receipt(tmp_path):
    root, os_root = tmp_path / "js", tmp_path / "os"
    root.mkdir()
    os_root.mkdir()
    code = textwrap.dedent(f"""
        import socket, sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import pipeline, run_guard
        lid = {{"closed": False}}
        real_wall = time.time
        run_guard._wall = lambda: real_wall() + ({SLEPT_S!r} if lid["closed"] else 0.0)
        def fetch_when_the_lid_closed(*a, **k):
            a_end, b_end = socket.socketpair()      # a board that never answers, read with no timeout
            lid["closed"] = True                    # the Mac sleeps 2 h; the process gets almost no awake time
            a_end.recv(1)
        pipeline.run_pipeline = fetch_when_the_lid_closed
        sys.exit(pipeline.main(["--root", {str(root)!r}, "--os-root", {str(os_root)!r},
                                "--budget-min", "20", "--routine-receipt", "js-hunt-boards"]))
    """)
    t0 = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                              cwd=str(SCRIPTS))
    except subprocess.TimeoutExpired:
        raise AssertionError("the guard never fired: 2 h of wall clock passed and the run was still blocked") from None
    took = time.monotonic() - t0
    sys.path.insert(0, str(SCRIPTS))
    import run_guard
    assert proc.returncode == run_guard.EXIT_CODE, (proc.returncode, proc.stderr[-800:])
    assert took < 20, f"the guard took {took:.1f} s of awake time after the wake"
    receipts = list((os_root / "logs" / "runs" / "js-hunt-boards").glob("*.md"))
    assert len(receipts) == 1
    text = receipts[0].read_text(encoding="utf-8")
    assert "· failed" in text and "run guard" in text and "fetch_when_the_lid_closed" in text, text
    assert "asleep" in text, text                    # the receipt says the machine slept, not only "hung"
    stacks = list((root / "state" / "pipeline").glob("hang-*.txt"))
    assert len(stacks) == 1
    stack = stacks[0].read_text(encoding="utf-8")
    assert "fetch_when_the_lid_closed" in stack and "asleep" in stack


def test_without_a_wall_clock_jump_a_short_wait_does_not_fire_a_long_guard(tmp_path):
    """The guard polls the wall clock; it must not fire early when no time has passed."""
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import run_guard
        fired = []
        g = run_guard.arm(60, {str(tmp_path)!r}, "t", on_expire=lambda top, path: fired.append(top))
        time.sleep(2.5)
        g.disarm()
        print("FIRED" if fired else "QUIET")
    """)], capture_output=True, text=True, timeout=30, cwd=str(SCRIPTS))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.strip() == "QUIET"
    assert not list(Path(tmp_path).glob("hang-*.txt"))
