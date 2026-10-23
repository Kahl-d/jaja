#!/usr/bin/env python3
"""stale_check.py - is a role's package still about the posting we will apply to? Checked before anyone writes.

Why: a page can be built from a package whose JD has since changed (a new required skill appears), and the editor and
recruiter review loops are then spent finding that out. This compares, deterministically:
  - the JD the matrix was built from (research/targets/<slug>.matrix.yaml `jd:`) with applications/<slug>/jd.md;
  - the package's age (research/targets/<slug>.md) against --max-days (default 21);
  - the snapshot date of jd.md (`snapshot_at:`) against --max-days.
Exit 0 FRESH, 1 STALE (the reasons printed; rebuild the matrix with jd_matrix.py build and refresh the package's
changed rows before drafting), 2 nothing to compare.

Usage:
  python3 scripts/stale_check.py <slug> [--max-days 21]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import yaml

from agency_paths import DATA_ROOT as JS  # noqa: E402
from agency_paths import SCRIPTS_DIR  # noqa: E402
sys.path.insert(0, str(SCRIPTS_DIR))


def body(text: str) -> str:
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    return re.sub(r"\s+", " ", text.replace("—", " - ")).strip().lower()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--max-days", type=int, default=21)
    a = ap.parse_args(argv)
    today = dt.date.today()
    jd = JS / "applications" / a.slug / "jd.md"
    matrix = JS / "research" / "targets" / f"{a.slug}.matrix.yaml"
    package = JS / "research" / "targets" / f"{a.slug}.md"
    if not jd.is_file() or not matrix.is_file():
        print(f"nothing to compare: {'jd.md' if not jd.is_file() else 'matrix'} missing")
        return 2
    reasons = []
    m = yaml.safe_load(matrix.read_text()) or {}
    src = JS / str(m.get("jd") or "")
    if src.is_file() and src.resolve() != jd.resolve():
        import evidence_slice as es
        old_t, new_t = body(src.read_text(errors="replace")), body(jd.read_text(errors="replace"))
        ow, nw = set(es.words(old_t)), set(es.words(new_t))
        jac = len(ow & nw) / max(len(ow | nw), 1)
        names = {n.lower() for r in es.taxonomy() for n in es.skill_names(r)}
        hit = lambda t: {n for n in names if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t)}  # noqa: E731
        new_skills = sorted(hit(new_t) - hit(old_t))
        if new_skills:
            reasons.append(f"the live JD names skills the matrix's JD did not: {', '.join(new_skills[:12])}")
        if jac < 0.6:
            reasons.append(f"the JD text changed a lot since the matrix was built (word overlap {jac:.0%})")
    built = str(m.get("built") or "")
    if built and (today - dt.date.fromisoformat(built[:10])).days > a.max_days:
        reasons.append(f"the matrix was built {built} (over {a.max_days} days)")
    if package.is_file():
        age = (today - dt.date.fromtimestamp(package.stat().st_mtime)).days
        if age > a.max_days:
            reasons.append(f"the package was last written {age} days ago")
    snap = re.search(r"(?m)^snapshot_at:\s*'?(\d{4}-\d{2}-\d{2})", jd.read_text(errors="replace"))
    if snap and (today - dt.date.fromisoformat(snap.group(1))).days > a.max_days:
        reasons.append(f"jd.md was read {snap.group(1)}: re-read the live posting (fetch_ats.py) before writing")
    if reasons:
        print(f"STALE {a.slug}")
        print("\n".join(f"- {r}" if not r.startswith("  ") else r for r in reasons))
        return 1
    print(f"FRESH {a.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
