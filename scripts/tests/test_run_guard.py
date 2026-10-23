"""SETUP-FINAL A1/A2: a run that hangs ends itself with a true failed receipt and a stack file.

On 2026-10-03 the 09:14 and 12:26 js-hunt-boards runs hung for two hours and wrote nothing (no stack, no verdict) until
the task's background limit killed them. These tests plant a hang in a real process and require the run guard to
(1) dump every thread's stack to a file, (2) write the routine's failed receipt naming where it hung, (3) exit 3,
long before any outside limit. A process is used because the guard ends its process (os._exit)."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _run(code: str, timeout: float = 30) -> tuple[subprocess.CompletedProcess, float]:
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True,
                          timeout=timeout, cwd=str(SCRIPTS))
    return proc, time.monotonic() - t0


def test_a_hung_pipeline_run_ends_itself_with_a_failed_receipt_and_its_stack(tmp_path):
    root, os_root = tmp_path / "js", tmp_path / "os"
    root.mkdir()
    os_root.mkdir()
    proc, took = _run(f"""
        import sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import pipeline, run_guard
        run_guard.MARGIN_S = 1.0                      # the real margin is minutes; the test cannot wait that long
        def planted_hang(*a, **k):
            time.sleep(600)                           # stands in for a socket that never answers
        pipeline.run_pipeline = planted_hang
        sys.exit(pipeline.main(["--root", {str(root)!r}, "--os-root", {str(os_root)!r},
                                "--budget-min", "0.02", "--routine-receipt", "js-hunt-boards"]))
    """)
    assert proc.returncode == run_guard_exit(), (proc.returncode, proc.stderr[-800:])
    assert took < 20, f"the guard took {took:.1f} s"
    receipts = list((os_root / "logs" / "runs" / "js-hunt-boards").glob("*.md"))
    assert len(receipts) == 1
    text = receipts[0].read_text(encoding="utf-8")
    assert "· failed" in text and "NEEDS_YOU" in text
    assert "run guard" in text and "planted_hang" in text, text      # the receipt names where it hung
    stacks = list((root / "state" / "pipeline").glob("hang-*.txt"))
    assert len(stacks) == 1 and "planted_hang" in stacks[0].read_text(encoding="utf-8")


def test_a_run_that_finishes_in_time_is_not_touched_by_the_guard(tmp_path):
    root, os_root = tmp_path / "js", tmp_path / "os"
    root.mkdir()
    os_root.mkdir()
    proc, _ = _run(f"""
        import sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import run_guard
        run_guard.MARGIN_S = 1.0
        fired = []
        g = run_guard.arm(0.6, stack_dir={str(root)!r}, label="t", on_expire=lambda top, path: fired.append(top))
        time.sleep(0.2)
        g.disarm()
        time.sleep(2.5)
        print("FIRED" if fired else "QUIET")
    """)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.strip() == "QUIET"
    assert not list(Path(root).glob("hang-*.txt"))


def test_the_guard_limit_is_budget_times_one_and_a_half_plus_the_margin():
    sys.path.insert(0, str(SCRIPTS))
    import run_guard
    assert run_guard.limit_s(20) == 20 * 60 * 1.5 + run_guard.MARGIN_S
    assert run_guard.limit_s(None) is None and run_guard.limit_s(0) is None


def run_guard_exit() -> int:
    sys.path.insert(0, str(SCRIPTS))
    import run_guard
    return run_guard.EXIT_CODE


def test_an_interrupt_still_disarms_the_guard(tmp_path, monkeypatch):
    """Review CS1 #2: a KeyboardInterrupt (or SystemExit) left the 35-minute timer and the backstop armed."""
    sys.path.insert(0, str(SCRIPTS))
    import pipeline
    import run_guard
    seen = []

    class Spy:
        def disarm(self):
            seen.append("disarmed")

    monkeypatch.setattr(run_guard, "arm", lambda *a, **k: Spy())

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(pipeline, "run_pipeline", interrupted)
    root, os_root = tmp_path / "js", tmp_path / "os"
    root.mkdir()
    os_root.mkdir()
    import pytest
    with pytest.raises(KeyboardInterrupt):
        pipeline.main(["--root", str(root), "--os-root", str(os_root), "--stages", "fetch", "--budget-min", "20"])
    assert seen == ["disarmed"]


def test_each_run_has_its_own_backstop_file(tmp_path):
    """Review CS1 #3: one fixed backstop file was truncated by the next run's arm, losing a wedged run's stack."""
    sys.path.insert(0, str(SCRIPTS))
    import run_guard
    wedged = tmp_path / "backstop-run-1.txt"
    wedged.write_text("Thread 0x1 (most recent call first): ...", encoding="utf-8")
    g = run_guard.arm(600, tmp_path, "run-2")
    g.disarm()
    assert wedged.read_text(encoding="utf-8").startswith("Thread 0x1")
    assert not (tmp_path / "backstop-run-2.txt").exists()          # an empty one is removed on disarm


def test_no_stderr_cannot_cost_the_exit_code(tmp_path):
    """Review CS1 #4: with stderr closed the write raised before os._exit, and the backstop ended it 60 s later, exit 1."""
    proc, took = _run(f"""
        import sys, time
        sys.path.insert(0, {str(SCRIPTS)!r})
        import run_guard
        run_guard.BACKSTOP_S = 30.0
        sys.stderr = None
        run_guard.arm(0.5, {str(tmp_path)!r}, "t")
        time.sleep(20)
    """)
    assert proc.returncode == run_guard_exit() and took < 10
