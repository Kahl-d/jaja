#!/usr/bin/env python3
"""answers_check.py - the gate on the form-answer bank, profile/answers/<family>.md.

Every answer and every question goes through proper research: a best foot forward per focus.
A bank file holds draft answers to the questions application forms ask most, per focus profile. Sponsorship and salary
answers are NOT in the bank (legal/timers.md and profile/private.yaml through apply_pack.py); nor is a company's "why
us" (APPLY writes it per role from the Reader model).

Checks, per file:
  - frontmatter `status` says draft (the owner approves; a bank answer is never pasted as final by an agent);
  - the six sections are present (SECTIONS), each heading carrying its cap "(<N> words max)", and each section's words
    (the [ev:...] tags not counted) are within the cap;
  - every sentence ends with an [ev:<id>,...] tag, one sentence per tag; every id exists in profile/evidence.yaml and is
    resume-ok (an internal-only id is a finding);
  - no em-dash; check_holds clean (profile/PROFILE-HOLDS.md).

CLI:  python3 scripts/answers_check.py [--root JS] [file ...]     (default: every profile/answers/*.md, and every focus
      family in profile/lenses/ with no bank file, reported missing)
      exit 0 clean, 1 findings (a wrong path, a folder or an unknown family counts), 2 nothing to check (no bank
      file at all, or a focus family in profile/lenses/ without a bank: named `missing:`, never clean; the drafter
      says so and uses its other inputs).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_holds  # noqa: E402
import profile_titles as pt  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

SECTIONS = ("Why this role", "A project you are proud of", "A technical challenge", "Why AI and agents",
            "What you want next", "100-word summary")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*\((\d+)\s+words max\)\s*$", re.M)
TAG_RE = re.compile(r"\[ev:([^\]]*)\]")
EM_DASH = "\u2014"


def _evidence(root: Path) -> dict:
    data = yaml.safe_load((root / "profile" / "evidence.yaml").read_text(encoding="utf-8")) or {}
    return data.get("evidence") or {}


def _sections(body: str) -> list[tuple[str, int, str]]:
    marks = list(HEADING_RE.finditer(body))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out.append((m.group(1).strip(), int(m.group(2)), body[m.end():end]))
    return out


def check_file(root: Path, path: Path) -> list[str]:
    root, path = Path(root), Path(path)
    text = path.read_text(encoding="utf-8")
    findings: list[str] = []
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta = (yaml.safe_load(m.group(1)) or {}) if m else {}
    body = text[m.end():] if m else text
    if "draft" not in str(meta.get("status") or "").lower():
        findings.append(f"status is {meta.get('status')!r}: a bank file stays a draft for the owner")
    evidence = _evidence(root)
    secs = _sections(body)
    names = [s[0] for s in secs]
    for want in SECTIONS:
        if not any(n.lower().startswith(want.lower()) for n in names):
            findings.append(f"missing section: {want}")
    for name, cap, sec in secs:
        prose = TAG_RE.sub(" ", sec)
        words = len(re.findall(r"[A-Za-z0-9][\w'’.,%$/+-]*", prose))
        if words > cap:
            findings.append(f"{name}: {words} words, over the {cap}-word cap")
        for para in [p.strip() for p in sec.strip().split("\n") if p.strip()]:
            for chunk in re.split(r"(?<=\])\s+", para):
                chunk = chunk.strip()
                if not chunk:
                    continue
                tag = TAG_RE.search(chunk)
                if not tag or not chunk.endswith("]"):
                    findings.append(f"{name}: no [ev:...] tag at the end of: {chunk[:80]}")
                    continue
                before = chunk[:tag.start()]
                if re.search(r"[.!?][\"'”’)]?\s+[A-Z]", before):
                    findings.append(f"{name}: one sentence per [ev:...] tag: {before[:80]}")
                for eid in [i.strip() for i in tag.group(1).split(",") if i.strip()]:
                    rec = evidence.get(eid)
                    if rec is None:
                        findings.append(f"{name}: {eid} unknown in profile/evidence.yaml")
                    elif str(rec.get("discretion") or "resume-ok") != "resume-ok":
                        findings.append(f"{name}: {eid} is {rec.get('discretion')}, never in a form answer")
    for n, line in enumerate(text.splitlines(), 1):
        if EM_DASH in line:
            findings.append(f"em-dash on line {n}")
    holds = pt.load_holds(root / "profile" / "PROFILE-HOLDS.md")
    for hit in check_holds.check_file(path, holds):
        if not hit.gap:
            findings.append(f"held text {hit.held!r} on line {hit.line}")
    return findings


def default_files(root: Path) -> list[Path]:
    """Every bank file, then every focus family in profile/lenses/ that has no bank file (named so a default run reports
    it `missing:`; otherwise a default run would say 0 missing while a family had no bank)."""
    answers = Path(root) / "profile" / "answers"
    banks = sorted(answers.glob("*.md"))
    have = {f.stem for f in banks}
    lenses = sorted((Path(root) / "profile" / "lenses").glob("*.md"))
    return banks + [answers / f"{lens.stem}.md" for lens in lenses if lens.stem != "README" and lens.stem not in have]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("files", nargs="*", type=Path)
    args = p.parse_args(argv)
    files = args.files or default_files(args.root)
    if not files:
        print("nothing to check: no profile/answers/*.md", file=sys.stderr)
        return 2
    bad = missing = 0
    for f in files:
        rel = f.relative_to(args.root) if f.is_absolute() and f.is_relative_to(args.root) else f
        if f.is_dir():
            bad += 1
            print(f"FINDING {rel}: not a bank file (a folder); name profile/answers/<family>.md")
            continue
        if not f.is_file():                                   # read as given, like check_file
            if f.stem != "README" and (args.root / "profile" / "lenses" / f"{f.stem}.md").is_file():
                missing += 1
                print(f"missing: {rel} (no bank for this family; missing is not empty)")
            else:
                bad += 1
                print(f"FINDING {rel}: no such file, and {f.stem!r} is not a focus family (profile/lenses/); "
                      "check the path")
            continue
        found = check_file(args.root, f)
        if found:
            bad += 1
            for x in found:
                print(f"FINDING {rel}: {x}")
        else:
            print(f"clean: {rel}")
    print(f"answers_check: {len(files) - bad - missing} clean, {bad} with findings, {missing} missing, of {len(files)}")
    return 1 if bad else 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
