#!/usr/bin/env python3
"""base_drift.py: was this variant cut from the base that ships today? (trust test 2026-10-01, F71)

Base 74 was promoted while two variants cut from base 73 were in review; the blind compare then judged one of
them against 74, and no tool noticed. This script finds every line of a variant that is verbatim in an archived base
(resumes/bases/archive/general-iter<N>/resume.yaml) but not in the current base (resumes/bases/general/resume.yaml):
those lines came from an older base and must be rebased (taken from the current base, or kept as a deliberate change
listed in NOTES `## Plan`) before the editor signs and before base-compare runs ( compares against the base that
ships).

Usage: python3 scripts/base_drift.py resumes/variants/<slug>/ [--json]
Exit 0: no stale base line; 1: stale lines (listed with the base they came from); 2: usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
EV = re.compile(r"<!--.*?-->", re.S)


def lines_of(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k not in ("sidecar",):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            t = re.sub(r"\s+", " ", EV.sub("", x)).strip()
            if len(t) > 40:               # a claim, not a date, a label or a URL
                out.add(t)
    walk(data.get("cv") if isinstance(data, dict) else data)
    return out


def current_iteration(root: Path) -> str:
    readme = root / "resumes" / "bases" / "general" / "README.md"
    m = re.search(r"Iteration (\d+)", readme.read_text(encoding="utf-8")) if readme.is_file() else None
    return m.group(1) if m else "?"


def drift(variant: Path, root: Path = JS_ROOT) -> dict[str, Any]:
    current = lines_of(root / "resumes" / "bases" / "general" / "resume.yaml")
    mine = lines_of(variant / "resume.yaml")
    stale: list[dict[str, str]] = []
    archives = sorted((root / "resumes" / "bases" / "archive").glob("general-iter*/resume.yaml"),
                      key=lambda p: -int(re.sub(r"\D", "", p.parent.name) or 0))
    seen: set[str] = set()
    for arch in archives:                 # newest archive first: a line is reported once, with its latest base
        old = lines_of(arch)
        it = re.sub(r"\D", "", arch.parent.name)
        for line in sorted((mine & old) - current - seen):
            stale.append({"from_base": it, "line": line[:160]})
            seen.add(line)
    return {"variant": str(variant), "current_base": current_iteration(root), "stale": stale}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("variant", type=Path)
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not (a.variant / "resume.yaml").is_file():
        print(f"no resume.yaml in {a.variant}", file=sys.stderr)
        return 2
    res = drift(a.variant, a.root)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for s in res["stale"]:
            print(f"STALE from base {s['from_base']}: {s['line']}")
        print(f"base drift: {len(res['stale'])} line(s) from an older base; the base is iteration {res['current_base']}"
              + ("" if not res["stale"] else ": rebase them (take the current base's line or list the change in NOTES)"))
    return 1 if res["stale"] else 0


if __name__ == "__main__":
    sys.exit(main())
