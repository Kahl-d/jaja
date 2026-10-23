#!/usr/bin/env python3
"""effort.py: the one-line effort level (E1 to E5) of a role, for the Recruiter's cards and queue builders (W4).

Charter §7 (the owner, 2026-10-05): "Effort follows importance: big company, a role needing heavy alignment, a high-fit or
high-chance role, a dream company, a warm path: more research and a tailored page. A normal role the base fits: the
base resume." The levels are the OS effort scale (AGENTS.md: E1 answer in place ... E5 a project), read for one role:

  E5  tailored, full research   a dream company AND (a warm path OR fit >= 4.5): the reverse-engineered recruiter,
                                the people map, a tailored page and answers, the person reached
  E4  tailored                  a dream company, a warm path (a person in conversation or the job came through one),
                                or fit >= 4.5: research and a tailored page
  E3  aligned                   fit 4.0 to 4.5, or a big company (companies.yaml segment big-tech, ai-lab,
                                enterprise or giant, or hunt_score.BIG_H1B+ recent H-1B approvals), or a role the
                                caller marks as needing heavy alignment: the coverage matrix and an aligned page
  E2  base, quick apply         every other role that passes the gates (fit under 4.0, or not scored yet): the base
                                resume, a quick apply
  E1  none                      a gate fail: nothing to apply to (the gate's reason stands)

The first rule that matches wins, top down. Fit is the fit-scorer's 1.0 to 5.0 score (None: not scored). Pure: the
caller passes what it read; effort_for(rec, company, warm) reads a job record and its companies.yaml entry.

CLI: python3 scripts/effort.py --fit 4.6 [--tier dream] [--warm] [--big] [--heavy] [--gate pass]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

LEVELS = ("E1", "E2", "E3", "E4", "E5")
HIGH_FIT = 4.5
ALIGN_FIT = 4.0
BIG_SEGMENTS = {"big-tech", "ai-lab", "enterprise", "giant"}     # the same set as hunt_score.BIG_SEGMENTS
BIG_H1B = 100                                                    # hunt_score.BIG_H1B


def effort(fit: float | None, *, tier: str | None = None, warm: bool = False, big: bool = False,
           heavy_alignment: bool = False, gate: str | None = "pass") -> tuple[str, str]:
    """(level, the one-line reason) by the rules in the module docstring."""
    if str(gate or "pass") == "fail":
        return "E1", "gate fail: not applied"
    dream = str(tier or "").lower() == "dream"
    high = fit is not None and fit >= HIGH_FIT
    if dream and (warm or high):
        return "E5", "dream company with " + ("a warm path" if warm else f"fit {fit:g}") + ": full research, tailored page"
    if dream or warm or high:
        why = "dream company" if dream else "warm path" if warm else f"fit {fit:g}"
        return "E4", f"{why}: research and a tailored page"
    if fit is not None and fit >= ALIGN_FIT:
        return "E3", f"fit {fit:g}: coverage matrix and an aligned page"
    if big or heavy_alignment:
        return "E3", ("big company" if big else "heavy alignment") + ": coverage matrix and an aligned page"
    return "E2", ("not scored yet" if fit is None else f"fit {fit:g}") + ": base resume, quick apply"


def is_big(company: dict[str, Any] | None) -> bool:
    c = company or {}
    return c.get("segment") in BIG_SEGMENTS or (c.get("h1b_approvals_recent") or 0) >= BIG_H1B


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def effort_for(rec: dict[str, Any], company: dict[str, Any] | None = None, warm: bool = False,
               heavy_alignment: bool = False) -> tuple[str, str]:
    """effort() for a job record (fit_score, gate) and its companies.yaml entry (tier, segment)."""
    return effort(_num(rec.get("fit_score")), tier=(company or {}).get("tier"), warm=warm, big=is_big(company),
                  heavy_alignment=heavy_alignment, gate=rec.get("gate"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fit", type=float, default=None)
    ap.add_argument("--tier")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--big", action="store_true")
    ap.add_argument("--heavy", action="store_true")
    ap.add_argument("--gate", default="pass")
    args = ap.parse_args(argv)
    level, why = effort(args.fit, tier=args.tier, warm=args.warm, big=args.big, heavy_alignment=args.heavy,
                        gate=args.gate)
    print(f"{level} {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
