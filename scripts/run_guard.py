#!/usr/bin/env python3
"""run_guard.py: a run that hangs ends itself, with its stack on disk and a true failed receipt (SETUP-FINAL A1, A2).

On 2026-10-03 two js-hunt-boards runs hung for two hours and wrote nothing until the scheduled task's background limit
killed them: no stack, no verdict, the watchdog the only witness. Per-request timeouts cannot cover every way a run
can stop (a DNS lookup has no timeout; a parser can loop). The guard is the whole-run limit inside the process:

  arm(limit, stack_dir, label, on_expire)   at the limit: every thread's stack to <stack_dir>/hang-<label>.txt,
                                            on_expire(top_frame, stack_path) (the caller writes its failed receipt),
                                            then os._exit(EXIT_CODE)
  limit_s(budget_min)                       budget x 1.5 plus MARGIN_S (35 min for the hunt's 20 min budget, far
                                            inside the task's 2 h limit); None when the run has no budget

A Python thread does the work (it runs while the main thread waits in a socket or a DNS lookup, both of which
release the GIL). faulthandler's own C watchdog is armed BACKSTOP_S later with exit=True, so a process whose Python
cannot run at all still leaves its stack and exits.

T-2026-0027: the limit is counted on the wall clock. On macOS every timed wait (threading.Timer, faulthandler's
watchdog) and time.monotonic count mach_absolute_time, which stops while the machine sleeps. The 2026-10-04 22:11 run
lost its lid at 22:30 and got about 20 awake minutes in 2 h 25 min, so its 2100 s Timer never came due, while the
app's 2 h limit (wall clock) killed it. The guard thread now wakes every TICK_S and compares the wall clock, so after a
sleep it fires within one tick of awake time, and the stack file and on_expire say how long the machine was asleep.
"""
from __future__ import annotations

import faulthandler
import io
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

EXIT_CODE = 3
MARGIN_S = 5 * 60.0
BACKSTOP_S = 60.0
TICK_S = 1.0                     # how often the guard reads the wall clock (awake seconds)
ASLEEP_NOTE_S = 60.0             # a gap between wall and awake time this long is named in the stack file and receipt
_wall = time.time                # counts the time the machine sleeps
_awake = time.monotonic          # on macOS (mach_absolute_time) it does not


def limit_s(budget_min: float | None) -> float | None:
    """The whole-run limit for a run budget in minutes (None or 0: the run has no budget, so no guard)."""
    if not budget_min:
        return None
    return float(budget_min) * 60 * 1.5 + MARGIN_S


def span(seconds: float) -> str:
    """'35 min' for a real limit, '3 s' under two minutes."""
    return f"{seconds / 60:.0f} min" if seconds >= 120 else f"{seconds:.0f} s"


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "run"


def stacks_text() -> str:
    """Every live thread's stack, main thread first, as text."""
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    main = threading.main_thread().ident
    out = io.StringIO()
    for ident in sorted(frames, key=lambda i: (i != main, str(names.get(i)))):
        out.write(f"Thread {names.get(ident, ident)}:\n")
        out.write("".join(traceback.format_stack(frames[ident])))
        out.write("\n")
    return out.getvalue()


def top_frame(ident: int | None = None) -> str:
    """'function (file.py:line)' of the innermost frame of a thread (the main thread by default)."""
    frame = sys._current_frames().get(ident or threading.main_thread().ident)
    if frame is None:
        return "unknown"
    return f"{frame.f_code.co_name} ({Path(frame.f_code.co_filename).name}:{frame.f_lineno})"


class _WallTimer(threading.Thread):
    """threading.Timer counted on the wall clock: it polls every TICK_S, so a machine sleep cannot postpone it."""

    def __init__(self, limit: float, t0: float, fn: Callable[[], Any]):
        super().__init__(name="run-guard", daemon=True)
        self._limit, self._t0, self._fn, self._done = limit, t0, fn, threading.Event()

    def cancel(self) -> None:
        self._done.set()

    def run(self) -> None:
        tick = min(TICK_S, self._limit)
        while not self._done.wait(tick):
            if _wall() - self._t0 >= self._limit:
                self._fn()
                return


class Guard:
    def __init__(self, timer: _WallTimer | None, backstop: Any = None):
        self._timer, self._backstop = timer, backstop

    def disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if self._backstop is not None:
            faulthandler.cancel_dump_traceback_later()
            fh, self._backstop = self._backstop, None
            fh.close()
            try:
                if os.path.getsize(fh.name) == 0:
                    os.unlink(fh.name)
            except OSError:
                pass


def arm(limit: float | None, stack_dir: Path | str, label: str,
        on_expire: Callable[[str, Path], Any] | None = None) -> Guard:
    """Start the guard. Returns a Guard whose disarm() the caller runs when the run ends in time."""
    if not limit:
        return Guard(None)
    stack_dir = Path(stack_dir)
    path = stack_dir / f"hang-{_safe(label)}.txt"
    wall0, awake0 = _wall(), _awake()

    def fire() -> None:
        top = top_frame()
        asleep = (_wall() - wall0) - (_awake() - awake0)
        if asleep >= ASLEEP_NOTE_S:              # a sleep, not a hang, used up the limit: say so
            top = f"{top}, the machine asleep for {span(asleep)} of the run"
        try:
            stack_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(f"run guard: {label} passed its {limit:.0f} s limit; innermost frame {top}\n\n"
                            + stacks_text(), encoding="utf-8")
        except OSError:
            pass
        try:
            if on_expire is not None:
                on_expire(top, path)
        finally:
            try:
                sys.stderr.write(f"run guard: {label} passed its {limit:.0f} s limit in {top}; stack {path}\n")
                sys.stderr.flush()
            except Exception:                    # no stderr (closed or None) must not cost the exit code
                pass
            finally:
                os._exit(EXIT_CODE)

    timer = _WallTimer(limit, wall0, fire)
    timer.start()
    backstop = None
    try:                                     # one file per run, removed on disarm while empty
        stack_dir.mkdir(parents=True, exist_ok=True)
        backstop = open(stack_dir / f"backstop-{_safe(label)}.txt", "w", encoding="utf-8")   # one per run
        faulthandler.dump_traceback_later(limit + BACKSTOP_S, repeat=False, file=backstop, exit=True)
    except (OSError, ValueError, RuntimeError):
        if backstop is not None:
            backstop.close()
        backstop = None
    return Guard(timer, backstop)
