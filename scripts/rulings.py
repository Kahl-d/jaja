#!/usr/bin/env python3
"""rulings.py: record the owner's rulings once, verbatim, with an id nobody else holds.

On 2026-10-01 three rulings were numbered twice (ORCH and SETUP each took the next id in its own file,
). This script is the one way to add a ruling to hub/rulings.yaml:

  next      prints the next free id: one past the highest K-<n> in hub/rulings.yaml AND in every `## K-<n>` header of
            hub/ORCHESTRATOR.md and hub/lanes/*.md (so a ruling written in a lane file first is never renumbered).
  add       appends one ruling: --quote is his exact words (repeat --quote for a follow-on message; joined with " || "),
            the time comes from `date` unless --time is given, --scope-lanes/--scope-roles/--scope-paths, --topic,
            --reading, --source. Refuses an id that is taken, an empty quote, an em-dash in the reading.
  check     validates the registry: ids unique, every entry has quote, date, source, status; prints the count.

Usage:
  python3 scripts/rulings.py next
  python3 scripts/rulings.py add --quote "..." [--quote "..."] --source "hub/lanes/SETUP.md" --topic resume \\
      --scope-lanes resume,apply --scope-roles resume-tailor --reading "Reading: ..." [--id] [--time "12:10 PDT"]
  python3 scripts/rulings.py check
Exit 0 ok, 1 refused or invalid, 2 usage error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
ID = re.compile(r"\bK-(\d+)[a-z]?\b")
HEADER = re.compile(r"^#{2,3}\s+(?:RULING\b.*?\b)?K-(\d+)", re.M)
TOPICS = {"apply", "hunt", "lanes", "memory", "other", "outreach", "people", "portfolio", "presence", "research",
          "resume", "salary", "visa"}


def registry(root: Path) -> Path:
    return root / "hub" / "rulings.yaml"


def load(root: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(registry(root).read_text(encoding="utf-8")) or {}
    return list(data.get("rulings") or [])


def taken_ids(root: Path) -> set[int]:
    nums = {int(m.group(1)) for r in load(root) for m in [ID.search(str(r.get("id", "")))] if m}
    files = [root / "hub" / "ORCHESTRATOR.md", *sorted((root / "hub" / "lanes").glob("*.md"))]
    for f in files:
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            nums |= {int(m.group(1)) for m in HEADER.finditer(text)}
            nums |= {int(m.group(1)) for m in re.finditer(r"recorded as K-(\d+)", text)}
    return nums


def next_id(root: Path) -> str:
    nums = taken_ids(root)
    return f"K-{max(nums) + 1 if nums else 1}"


def _now_time() -> str:
    out = subprocess.run(["date", "+%H:%M %Z"], capture_output=True, text=True, env={**os.environ,
                         "TZ": "America/Los_Angeles"})
    return out.stdout.strip()


def _today() -> str:
    out = subprocess.run(["date", "+%F"], capture_output=True, text=True, env={**os.environ,
                         "TZ": "America/Los_Angeles"})
    return out.stdout.strip()


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def add(root: Path, quotes: list[str], source: str, topic: str, reading: str, lanes: list[str], roles: list[str],
        paths: list[str], rid: str | None = None, time: str | None = None, date: str | None = None) -> dict[str, Any]:
    quote = " || ".join(q.strip() for q in quotes if q and q.strip())
    if not quote:
        raise ValueError("empty quote: a ruling is his exact words")
    if topic not in TOPICS:
        raise ValueError(f"topic {topic!r} not in {sorted(TOPICS)}")
    if "—" in reading:
        raise ValueError("em-dash in the reading")
    rows = load(root)
    if rid:
        if rid in {str(r.get("id")) for r in rows}:
            raise ValueError(f"{rid} is already in rulings.yaml")
    else:
        rid = next_id(root)
    entry = {"id": rid, "date": date or _today(), "time": time or _now_time(), "quote": quote, "source": source,
             "reading": reading, "topic": topic, "scope": {"lanes": lanes, "roles": roles, "paths": paths},
             "status": "standing", "enforced_by": "none"}
    text = registry(root).read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += yaml.safe_dump([entry], sort_keys=False, width=120, allow_unicode=True)
    yaml.safe_load(text)                                     # never leave the registry unparseable
    fd, tmp = tempfile.mkstemp(dir=registry(root).parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, registry(root))
    return entry


def check(root: Path) -> list[str]:
    rows = load(root)
    errs: list[str] = []
    seen: set[str] = set()
    for r in rows:
        rid = str(r.get("id", ""))
        if rid in seen:
            errs.append(f"{rid}: duplicate id")
        seen.add(rid)
        for k in ("date", "source", "status"):
            if not r.get(k):
                errs.append(f"{rid}: no {k}")
        if not r.get("quote") and not r.get("reading"):         # early rulings kept only as a reading (paraphrase)
            errs.append(f"{rid}: neither quote nor reading")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next")
    sub.add_parser("check")
    a = sub.add_parser("add")
    a.add_argument("--quote", action="append", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--topic", required=True)
    a.add_argument("--reading", default="")
    a.add_argument("--scope-lanes", default="")
    a.add_argument("--scope-roles", default="")
    a.add_argument("--scope-paths", default="")
    a.add_argument("--id", default=None)
    a.add_argument("--time", default=None)
    a.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "next":
        print(next_id(args.root))
        return 0
    if args.cmd == "check":
        errs = check(args.root)
        for e in errs:
            print(f"ERROR {e}")
        print(f"rulings: {len(load(args.root))} entries, {len(errs)} errors")
        return 1 if errs else 0
    try:
        e = add(args.root, args.quote, args.source, args.topic, args.reading, _split(args.scope_lanes),
                _split(args.scope_roles), _split(args.scope_paths), args.id, args.time, args.date)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"recorded {e['id']} ({e['date']} {e['time']}): {e['quote'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
