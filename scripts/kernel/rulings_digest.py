#!/usr/bin/env python3
"""rulings_digest.py - the rulings digest a lane or role sees at start, and the words of any ruling on demand.

Why: a long hub/rulings.yaml is slow to parse on every session and subagent start and, printed whole, crowds out the
context; clipped newest-first quotes also let the oldest universal safety rulings fall to a bare id.
Now the hooks (lane_rules.py, role_rules.py) print rulings_common.digest from a compiled index:

  <hub>/rulings.index.json   every standing ruling: id, date, topic, scope, kind, one-line reading, pinned flag
                             (gitignored; rebuilt when rulings.yaml or rulings-pins.yaml is newer)
  <hub>/rulings-pins.yaml    the curated principles shown first to every lane and role

rulings.yaml stays the one place the words live; this script and the hooks share one implementation
(hooks/rulings_common.py). Read-only on rulings.yaml.

Usage:
  python3 scripts/kernel/rulings_digest.py build
  python3 scripts/kernel/rulings_digest.py digest --lane recruiter [--cap 2500] [--all]
  python3 scripts/kernel/rulings_digest.py digest --role resume-tailor
  python3 scripts/kernel/rulings_digest.py show [ ...]      # the verbatim quote and reading
  python3 scripts/kernel/rulings_digest.py stats                     # bytes per lane digest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_paths as _ap  # noqa: E402
ROOT = _ap.CODE_ROOT
sys.path.insert(0, str(ROOT / "hooks"))
import rulings_common as rc  # noqa: E402

DEFAULT_HUB = _ap.DATA_ROOT / "hub"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["build", "digest", "show", "stats"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--hub", type=Path, default=DEFAULT_HUB)
    ap.add_argument("--lane")
    ap.add_argument("--role")
    ap.add_argument("--cap", type=int, default=rc.DIGEST_CAP)
    ap.add_argument("--days", type=int, default=rc.DIGEST_DAYS)
    ap.add_argument("--all", action="store_true", help="every standing ruling for the scope, no cap, no window")
    a = ap.parse_args(argv)
    reg = a.hub / "rulings.yaml"
    if a.cmd == "build":
        rows = rc.build_index(reg)
        print(f"{a.hub / 'rulings.index.json'}: {len(rows)} standing, {sum(r['pinned'] for r in rows)} pinned")
        return 0
    if a.cmd == "show":
        by_id = {str(r["id"]): r for r in rc.load_rulings(reg)}
        for i in a.ids:
            r = by_id.get(i)
            print(f"{i}: not found" if not r else
                  f"{i} ({r.get('date')}, {r.get('status')}, scope {r.get('scope')}):\n"
                  f"  \"{str(r.get('quote') or '').strip()}\"\n  {str(r.get('reading') or '').strip()}\n")
        return 0
    rows = rc.index_rows(reg)
    if a.cmd == "digest":
        if not (a.lane or a.role):
            ap.error("digest needs --lane or --role")
        cap, days = (10**9, 10**5) if a.all else (a.cap, a.days)
        print(rc.digest(rows, lane=a.lane, role=a.role, cap=cap, days=days))
        return 0
    lanes = sorted({x for r in rows for x in r["lanes"] if x != "all"})
    print(f"rulings.yaml {reg.stat().st_size} bytes; {len(rows)} standing, {sum(r['pinned'] for r in rows)} pinned")
    for x in lanes:
        print(f"  lane {x:14} digest {len(rc.digest(rows, lane=x, cap=a.cap)):>6} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
