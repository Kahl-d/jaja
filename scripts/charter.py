#!/usr/bin/env python3
"""charter.py - print only the sections of AGENCY-CHARTER.md a lane needs, plus the goal and the safety lines.

Why (docs/superpowers/specs/2026-10-07-token-efficiency-redesign.md, section 4.3): every lane read the whole 21 KB
charter at start, though each card names the two or three sections that are its own. Section 0 (the goal and the
clock), 0.1 (what gets interviews fastest) and "Safety lines that stay" always print. The Recruiter still reads every line (its card says so).

Usage:
  python3 scripts/charter.py 2 6 8        # sections 0, 2, 6, 8 and the safety lines
  python3 scripts/charter.py --list       # the section headings
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHARTER = Path(__file__).resolve().parents[1] / "docs" / "AGENCY-CHARTER.md"
ALWAYS = ("0", "0.1", "safety")


def sections(text: str) -> list[tuple[str, str, str]]:
    """(key, heading, body): key is the section number ("0", "0.1", "6") or "safety"."""
    parts = re.split(r"(?m)^(## .*)$", text)
    out = []
    for i in range(1, len(parts), 2):
        h = parts[i].strip()
        m = re.match(r"## (\d+(?:\.\d+)?)\.?\s", h)
        key = m.group(1) if m else ("safety" if "safety" in h.lower() else h[3:].lower())
        out.append((key, h, parts[i + 1].rstrip()))
    return out


def main(argv: list[str]) -> int:
    text = CHARTER.read_text(encoding="utf-8")
    secs = sections(text)
    if "--list" in argv:
        print("\n".join(h for _, h, _ in secs))
        return 0
    want = set(ALWAYS) | {a.rstrip(".") for a in argv}
    head = text.split("\n## ", 1)[0].rstrip()
    print(head + f"\n\n(sections {', '.join(sorted(want))} of {CHARTER.name}; the rest: python3 scripts/charter.py --list)\n")
    for key, h, body in secs:
        if key in want:
            print(f"{h}\n{body}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
