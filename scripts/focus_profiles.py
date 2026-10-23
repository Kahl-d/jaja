#!/usr/bin/env python3
"""focus_profiles.py: one profile per role focus, picked by title, each with its best foot forward.

the owner, 2026-10-01: "we want to make sure that we have like a profile for every of that focus and we have
like a best foot that we can present forward. The core of our work remains the same; it's just that presentation
and our vision aligns with that role."

A focus profile is a lens file in profile/lenses/<family>.md: sections 1 to 6 read the focus (its reader, what must
be seen first, the real work under it, traps), and section 7 is the best foot forward (headline, vision line, page
order, Skills rows, projects, why-this-role answer, story, outreach line, questions).

  list            every profile: family, status, titles covered, section 7 present
  pick "<title>"  the profiles whose titles_covered patterns match a job title, best match first (the role-analyst's
                  step 1 starts here; the JD's first third still decides between two)
  check           every profile has frontmatter (family, titles_covered, status), sections 1 to 7, and in section 7
                  every item of the best foot; exit 1 with the gaps

Usage: python3 scripts/focus_profiles.py {list | pick "<title>" | check} [--root R] [--json]
No network. Exit 0 ok, 1 gaps (check) or no match (pick), 2 usage error.
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
SECTION7_ITEMS = ("headline", "vision", "page order", "skills", "projects", "why this role", "story", "outreach",
                  "questions")
STOP = {"senior", "staff", "principal", "lead", "sr", "jr", "junior", "ii", "iii", "i", "iv", "the", "and", "of", "for",
        "a", "an", "to", "engineer", "engineering", "software", "member", "technical", "mts", "new", "grad"}


_LENS_CACHE: dict[str, tuple[tuple, list[dict[str, Any]]]] = {}


def _lens_files(root: Path) -> list[Path]:
    return [p for p in sorted((Path(root) / "profile" / "lenses").glob("*.md")) if p.name != "README.md"]


def _cached(root: Path) -> list[dict[str, Any]]:
    """The parsed lenses, read once per process. The key is
    each file's name, mtime and size, so an edited or added lens is read again. Callers outside this module get a copy."""
    files = _lens_files(root)
    sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    key = str(Path(root).resolve())
    hit = _LENS_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    rows = _read_lenses(root, files)
    _LENS_CACHE[key] = (sig, rows)
    return rows


def lenses(root: Path) -> list[dict[str, Any]]:
    return copy.deepcopy(_cached(root))


def _read_lenses(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    out = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        fm: dict[str, Any] = {}
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                fm = {"_error": "frontmatter is not valid YAML"}
        heads = re.findall(r"^## (\d+)\.", text, re.M)
        s7 = ""
        m7 = re.search(r"^## 7\..*?(?=^## \d+\.|\Z)", text, re.M | re.S)
        if m7:
            s7 = m7.group(0)
        out.append({"family": fm.get("family") or p.stem, "path": str(p.relative_to(root)), "status": fm.get("status"),
                    "titles": list(fm.get("titles_covered") or []), "sections": [int(h) for h in heads],
                    "section7": s7, "error": fm.get("_error"),
                    "requires_any": [str(w).lower() for w in (fm.get("requires_any") or [])],
                    "specialist": [str(w).lower() for w in (fm.get("specialist_words") or [])]})
    return out


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+]+", s.lower()) if w not in STOP}


def pick(root: Path, title: str) -> list[dict[str, Any]]:
    """Rank profiles for a title. A specialist word of a profile in the title (`specialist_words:`, "evaluation",
    "harness", "founding") ranks first (2.25: the market hires specialists, so "ML Engineer - Automated
    Evaluation" is an evaluation seat before it is an ML seat); then a glob match on a titles_covered pattern (2.0, or
    1.5 when it only contains it); then word overlap."""
    t = title.strip()
    tw = _words(t)
    ranked = []
    for lens in _cached(root):
        need = lens.get("requires_any") or []
        if need and not any(re.search(rf"\b{re.escape(w)}\b", t.lower()) for w in need):
            continue                       # a secondary focus is picked only when the title names its domain
        best = 0.0
        why = ""
        hit = [w for w in lens.get("specialist") or [] if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t.lower())]
        if hit:
            best, why = 2.25, f"specialist word '{hit[0]}'"
        for pat in lens["titles"] if best < 2.25 else []:
            pat = str(pat)
            if fnmatch.fnmatch(t.lower(), pat.lower()) or pat.lower() == t.lower():
                best, why = 2.0, f"title matches '{pat}'"
                break
            if fnmatch.fnmatch(t.lower(), "*" + pat.lower().lstrip("*")):     # "Applied AI Engineer" ~ "AI Engineer*"
                if 1.5 > best:
                    best, why = 1.5, f"title contains '{pat}'"
                continue
            pw = _words(pat)
            if pw and tw:
                ov = len(pw & tw) / len(pw | tw)
                if ov > best:
                    best, why = ov, f"words shared with '{pat}'"
        if best >= 0.34:
            ranked.append({"family": lens["family"], "score": round(best, 2), "why": why, "path": lens["path"]})
    return sorted(ranked, key=lambda r: -r["score"])


def check(root: Path) -> list[str]:
    gaps = []
    for lens in lenses(root):
        f = lens["family"]
        if lens.get("error"):
            gaps.append(f"{f}: {lens['error']}")
        if not lens["titles"]:
            gaps.append(f"{f}: no titles_covered")
        if not lens["status"]:
            gaps.append(f"{f}: no status")
        missing = [n for n in range(1, 8) if n not in lens["sections"]]
        if missing:
            gaps.append(f"{f}: missing section(s) {', '.join(map(str, missing))}")
        s7 = lens["section7"].lower()
        if s7:
            for item in SECTION7_ITEMS:
                if item not in s7:
                    gaps.append(f"{f}: section 7 has no '{item}' item")
            if "—" in lens["section7"]:
                gaps.append(f"{f}: em-dash in section 7")
    return gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("pick")
    p.add_argument("title")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        rows = [{k: v for k, v in x.items() if k != "section7"} | {"section7": bool(x["section7"])}
                for x in lenses(a.root)]
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['family']:32} {str(r['status']):9} s7={'yes' if r['section7'] else 'no ':3} "
                      f"titles={len(r['titles'])}")
        return 0
    if a.cmd == "pick":
        rows = pick(a.root, a.title)
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['score']:.2f}  {r['family']:32} {r['why']}")
            if not rows:
                print("no profile matches this title: the role-analyst writes a draft lens section and flags it")
        return 0 if rows else 1
    gaps = check(a.root)
    for g in gaps:
        print(f"GAP {g}")
    print(f"focus profiles: {len(lenses(a.root))}, gaps {len(gaps)}")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
