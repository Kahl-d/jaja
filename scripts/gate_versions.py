#!/usr/bin/env python3
"""gate_versions.py: the version (sha256, first 12) of every page gate script, one line, for NOTES `## Gates`.

A reviewer re-running a gate needs to know whether a different result means the page changed or the script did
(trust test 2026-10-01, F46: three gate scripts changed between an editor's rounds 1 and 2).
Usage: python3 scripts/gate_versions.py   (prints `gates: render_resume <sha> · ats_check <sha> · ...`)
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATES = ("render_resume", "ats_check", "check_holds", "rubric_check", "reader_model", "numbers_ledger", "jd_matrix",
         "letters_check", "page_layout_check", "screener_check")


def versions() -> dict[str, str]:
    out = {}
    for g in GATES:
        p = HERE / f"{g}.py"
        out[g] = hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "missing"
    from agency_paths import DATA_ROOT  # noqa: E402
    holds = DATA_ROOT / "profile" / "PROFILE-HOLDS.md"
    out["PROFILE-HOLDS"] = hashlib.sha256(holds.read_bytes()).hexdigest()[:12] if holds.is_file() else "missing"
    return out


def main() -> int:
    print("gates: " + " · ".join(f"{k} {v}" for k, v in versions().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
