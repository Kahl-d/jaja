#!/usr/bin/env python3
"""nightly_sweep.py - the nightly sweep as one deterministic command (an optional routine; none ships enabled).

Runs, in order, and records one line per step in the routine's receipt:
    1. watchdog        scripts/kernel/watchdog.py: state file and inbox alerts, before the briefing counts the inbox
    2. sweep           scripts/kernel/sweep.py: HOME.md, HOME.json and the registries (runtime root)
    3. lint            scripts/kernel/lint.py
    4. sync --check    scripts/kernel/sync.py --check (the code root)
A step that raises is recorded as failed and the next step still runs.

Receipt: <runtime>/logs/runs/nightly-sweep/<date>.md with status ok (all clean), warn (ran, but found
lint findings, adapter drift, the briefing over its line budget or a SILENT/ERROR routine) or failed (a
step raised); sentinel NO_ACTION when ok, else NEEDS_YOU. Exit 0 when ok, else 1.

CLI:  python3 scripts/kernel/nightly_sweep.py [--root R | --code P --data P --runtime P] [--today D | --now ISO] [--no-receipt]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint  # noqa: E402
import sweep  # noqa: E402
import sync  # noqa: E402
import watchdog  # noqa: E402  (scripts/kernel/watchdog.py; sys.path[0] above shadows the PyPI package of that name)
from roots import Roots, add_root_args, from_args, roots as as_roots  # noqa: E402

if not hasattr(watchdog, "check"):  # pragma: no cover - only when another "watchdog" module was imported first
    raise ImportError(f"imported {watchdog.__file__}, not scripts/kernel/watchdog.py")

ROUTINE = "nightly-sweep"


def run(root: Any, now: dt.datetime, today: dt.date) -> tuple[str, list[str]]:
    root = as_roots(root)
    """(status, one line per step)."""
    lines: list[str] = []
    status = "ok"

    def step(label: str, fn: Callable[[], tuple[bool, str]]) -> None:
        nonlocal status
        try:
            clean, text = fn()
        except Exception as exc:  # recorded, never swallowed silently
            status = "failed"
            lines.append(f"{label}: failed: {type(exc).__name__}: {exc}")
            return
        if not clean and status == "ok":
            status = "warn"
        lines.append(f"{label}: {text}")

    def watch() -> tuple[bool, str]:
        report = watchdog.check(root, now, today=today)
        bad = [f"{r['name']} {r['state']}" for r in report["routines"] if r["state"] in ("SILENT", "ERROR")]
        text = watchdog.summary(report)
        if bad:
            text += " · " + ", ".join(bad)
        if report["alerts_written"]:
            text += f" · {len(report['alerts_written'])} inbox alert(s) written"
        return not bad, text

    def home() -> tuple[bool, str]:
        sweep.main(root, today)
        lines_ = (root.runtime / "HOME.md").read_text(encoding="utf-8").splitlines()
        return len(lines_) <= sweep.HOME_MAX_LINES, f"HOME.md {len(lines_)} lines - {lines_[-1] if lines_ else 'empty'}"

    def check_lint() -> tuple[bool, str]:
        findings = lint.run(root)
        return not findings, "clean" if not findings else f"{len(findings)} finding(s); first: {findings[0]}"

    def check_sync() -> tuple[bool, str]:
        report = sync.diff(root.code)
        drift = [f"{kind} {path}" for kind, paths in report.items() for path in paths]
        return not drift, "adapters up to date" if not drift else f"{len(drift)} file(s) drifted; first: {drift[0]}"

    step("watchdog", watch)
    step("sweep", home)
    step("lint", check_lint)
    step("sync --check", check_sync)
    return status, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_root_args(ap)
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--today", help="YYYY-MM-DD: now (if today) or 12:00 local on that day")
    when.add_argument("--now", type=watchdog.parse_now, help="ISO timestamp (naive = local time)")
    ap.add_argument("--no-receipt", action="store_true", help="run the steps without writing the receipt")
    args = ap.parse_args(argv)
    root = from_args(args)
    if args.now:
        now = args.now
    elif args.today:
        day = watchdog.as_date(args.today)
        if day is None:
            ap.error(f"--today {args.today!r} is not YYYY-MM-DD")
        now = dt.datetime.now().replace(microsecond=0) if day == dt.date.today() else dt.datetime.combine(day, dt.time(12))
    else:
        now = dt.datetime.now().replace(microsecond=0)
    status, lines = run(root, now, now.date())
    for line in lines:
        print(line)
    if not args.no_receipt:
        path = watchdog.write_receipt(root, ROUTINE, now, status=status, notes=lines,
                                      sentinel="NO_ACTION" if status == "ok" else "NEEDS_YOU")
        print(f"receipt: {watchdog.rel(root.runtime, path)}")
    print(f"{ROUTINE}: {status}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
