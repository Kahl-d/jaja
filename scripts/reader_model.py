#!/usr/bin/env python3
"""reader_model.py: check a Role Package's Reader model (section 6) before anyone writes the page.

The point of the Reader model: reverse engineer the job description, the role, resumes and the people's posts to learn
exactly what the reader wants, then present the candidate's real work in that reader's own words.

The role-analyst writes `## 6 Reader model` (roles/role-analyst.md step 6). This script checks its form, never its
judgment:
  - Wants: 3 to 5 rows `W<n> | "<their words>" | <URL>, <date>`, each with a quote, a source URL and a date;
  - Their words: rows `<their term> | <our term> | ev-<id>`, each with an evidence id;
  - Answer table: every one of W1 to W3 has a row `W<n> | <the line> | ev-<id>`, or is named in section 8 as a
    question for the owner;
  - with --resume V/resume.yaml: each answer line is found on that page (a warning when it is not: the page does not
    yet meet that want).

Usage:
  python3 scripts/reader_model.py check research/targets/<slug>.md [--resume resumes/variants/<slug>/resume.yaml] [--json]
Exit 0 clean (warnings allowed), 1 findings, 2 usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SECTION = re.compile(r"^##\s+(\d+|Sources)\b(.*)$", re.M)
WANT = re.compile(r"^\s*[-*]?\s*(W[1-9])\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$")
URL = re.compile(r"https?://\S+")
DATE = re.compile(r"\b20\d\d-\d\d(?:-\d\d)?\b")
EV = re.compile(r"\bev-[a-z0-9-]+\b")
QUOTE = re.compile(r"[\"“].{3,}[\"”]")
READER_STOP = {"their", "there", "which", "would", "about", "these", "those", "where", "while", "should", "could",
               "other", "every", "being", "build", "using", "within", "across", "through", "strong", "experience"}


def sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    marks = list(SECTION.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end():end]
    return out


def _block(body: str, label: str) -> str:
    """The lines under a bold label (`**Wants**`, `- **Answer table** ...`) up to the next bold label."""
    lines = body.splitlines()
    out: list[str] = []
    on = False
    for line in lines:
        if re.search(r"\*\*[^*]+\*\*", line) and not WANT.match(line):
            on = label.lower() in line.lower()
            continue
        if on:
            out.append(line)
    return "\n".join(out)


def _norm(s: str) -> str:
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _top_of_page(resume: Path) -> str:
    """What a reader sees in the first 30 seconds: each of the first two Experience entries' context line (summary or
    position) and its first two bullets."""
    try:
        import yaml
        data = yaml.safe_load(resume.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001  (not a resume.yaml: no top region, no warning)
        return ""
    exp = (((data.get("cv") or {}).get("sections") or {}).get("Experience")) or []
    parts: list[str] = []
    for entry in exp[:2]:
        if isinstance(entry, dict):
            parts += [str(entry.get("summary") or ""), str(entry.get("position") or "")]
            parts += [str(h) for h in (entry.get("highlights") or [])[:2]]
    return _norm(" ".join(parts))


def check(package: Path, resume: Path | None = None) -> dict[str, Any]:
    text = package.read_text(encoding="utf-8")
    secs = sections(text)
    errors: list[str] = []
    warnings: list[str] = []
    body = secs.get("6")
    if body is None or "reader model" not in text.lower():
        return {"errors": ["no `## 6 Reader model` section (role-analyst step 6)"], "warnings": [], "wants": 0}
    wants = [WANT.match(l) for l in _block(body, "Wants").splitlines()]
    wants = [m for m in wants if m]
    if not 3 <= len(wants) <= 5:
        errors.append(f"Wants: {len(wants)} rows; 3 to 5 ranked rows `W<n> | \"their words\" | URL, date` are needed")
    for m in wants:
        wid, words, src = m.group(1), m.group(2), m.group(3)
        if not QUOTE.search(words):
            errors.append(f"{wid}: the want is not quoted in the reader's words")
        if not URL.search(src) and not URL.search(words):
            errors.append(f"{wid}: no source URL")
        if not DATE.search(src):
            errors.append(f"{wid}: no date on the source")
    vocab = [l for l in _block(body, "Their words").splitlines() if l.count("|") >= 2]
    if not vocab:
        errors.append("Their words: no `their term | our term | ev-id` rows")
    for l in vocab:
        if not EV.search(l) and "none" not in l.lower():
            errors.append(f"Their words row without an ev- id: {l.strip()[:80]}")
    answers = {m.group(1): m for m in (WANT.match(l) for l in _block(body, "Answer table").splitlines()) if m}
    questions = secs.get("8", "")
    page = _norm(resume.read_text(encoding="utf-8")) if resume else ""
    top = _top_of_page(resume) if resume else ""
    if resume:                       # the page meets each want in the reader's own words, not only our line
        for m in wants:
            q = QUOTE.search(m.group(2))
            said = [w for w in _norm(q.group(0) if q else "").split() if len(w) > 4 and w not in READER_STOP]
            if said and not any(w in page.split() or w.rstrip("s") in page for w in said):
                warnings.append(f"{m.group(1)}: none of the reader's words ({', '.join(said[:4])}) is on the page")
    for wid in ("W1", "W2", "W3"):
        if wid not in {m.group(1) for m in wants}:
            continue
        row = answers.get(wid)
        if row is None:
            if re.search(rf"\b{wid}\b", questions):
                warnings.append(f"{wid}: no true line yet; asked in section 8")
            else:
                errors.append(f"{wid}: no answer row and no question in section 8 (a want is answered or asked)")
            continue
        if not EV.search(row.group(3)) and not EV.search(row.group(2)):
            errors.append(f"{wid}: the answer row carries no ev- id")
        if resume:
            line = _norm(row.group(2))
            words = [w for w in line.split() if len(w) > 3]
            hit = sum(w in page for w in words) / max(1, len(words))
            if hit < 0.6:
                warnings.append(f"{wid}: the answer line is not on {resume} yet ({hit:.0%} of its words)")
            elif wid == "W1" and top:
                top_hit = sum(w in top for w in words) / max(1, len(words))
                if top_hit < 0.6:
                    warnings.append("W1: met only below the first 30 seconds (not in the first two entries' context and "
                                    "first two bullets); carry it there in the reader's words (tailor step 2)")
    size = len(text.encode("utf-8"))
    if size > PACKAGE_CAP:
        warnings.append(f"the package is {size / 1024:.1f} KB, over the 12 KB form (roles/role-analyst.md)")
    return {"errors": errors, "warnings": warnings, "wants": len(wants), "answered": sorted(answers)}


PACKAGE_CAP = 12 * 1024    # roles/role-analyst.md "The package form ... at most 12 KB" (exceeding it is a warning)
# Held text in a package's matrix is not gated here: held terms there are negations in a ceiling or gap note ("no
# knowledge graph", "never Docker"). check_holds gates the page itself.


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("package", type=Path)
    c.add_argument("--resume", type=Path, default=None)
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.package.is_file() or (a.resume and not a.resume.is_file()):
        print(f"no such file: {a.package if not a.package.is_file() else a.resume}", file=sys.stderr)
        return 2
    res = check(a.package, a.resume)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for e in res["errors"]:
            print(f"ERROR {e}")
        for w in res["warnings"]:
            print(f"WARN  {w}")
        print(f"reader model: wants {res['wants']}, errors {len(res['errors'])}, warnings {len(res['warnings'])}")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
