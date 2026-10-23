#!/usr/bin/env python3
"""letters_check.py: the form of a cover letter, a why-us answer and an anything-else note.

A cover letter presents the candidate's ideas and uses real experience to connect them; it is not a list. This
script checks what a script can:

  every paragraph ends with an evidence comment <!-- ev:... -->; word counts within the caps (config
  `policy.letters.caps`, or --cap); no em-dash when `style.no_em_dash` is on; with `style.letters.prose_only`
  on, no bullet list and no run of three or more comma-separated tool names; no phrase from
  `style.letters.banned_phrases` (regex fragments); no term from `policy.nda_terms`; held text through
  check_holds; a number about the governed employer (`policy.numbers_ledger.employer_pattern`) only from a
  measured, released row of the numbers ledger.

The editor judges the rest (ideas first, simple words, real experience as proof): rubric S12a to S12d.

Usage: python3 scripts/letters_check.py resumes/variants/<slug>/ [--cap N] [--json]
Reads cover.md, why-us.md and else.md when present. Exit 0 clean, 1 findings, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import agency_config as cfg

_DEFAULT_CAPS = {"cover.md": (150, 400), "why-us.md": (0, 150), "else.md": (0, 150)}


def _caps() -> dict[str, tuple[int, int]]:
    """Word caps (low, high) per letter file, from `policy.letters.caps` ({file: [low, high]}) over the defaults."""
    caps = dict(_DEFAULT_CAPS)
    for name, pair in (cfg.get("policy.letters.caps", {}) or {}).items():
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            caps[str(name)] = (int(pair[0]), int(pair[1]))
    return caps


FILES = _caps()
LONG_SENTENCE = int(cfg.get("policy.letters.long_sentence", 30) or 30)
# One proof per paragraph: the work an ev- id belongs to, by prefix (`policy.letters.work_of`: {prefix: work});
# ids outside the map are neutral. A paragraph that mixes two works is a finding.
WORK_OF: dict[str, str] = {str(k): str(v) for k, v in (cfg.get("policy.letters.work_of", {}) or {}).items()}
EV = re.compile(r"<!--\s*ev:[^>]+-->")
COMMENT = re.compile(r"<!--.*?-->", re.S)
_BANNED_PARTS = [str(p) for p in (cfg.get("style.letters.banned_phrases", []) or []) if str(p).strip()]
BANNED = re.compile("|".join(_BANNED_PARTS), re.I) if _BANNED_PARTS else None
PROSE_ONLY = bool(cfg.get("style.letters.prose_only", False))
TOOL_RUN = re.compile(r"\b[A-Z][\w.+-]*(?:,\s+[A-Z][\w.+-]*){2,}")


def paragraphs(text: str) -> list[str]:
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")]


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'][A-Za-z0-9'.-]*", COMMENT.sub(" ", text)))


def check_file(path: Path, cap: int | None = None) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lo, hi = FILES.get(path.name, (0, 150))
    if cap:
        lo, hi = 0, cap
    out: list[str] = []
    paras = paragraphs(text)
    names = [re.escape(str(n).lower()) for n in (cfg.get("identity.signature_names", []) or []) if str(n).strip()]
    signoff = re.compile(r"^(" + "|".join(names + ["best,?", "thank you,?", "thanks,?"]) + r")\s*$", re.I)
    for i, p in enumerate(paras, 1):
        if signoff.match(COMMENT.sub("", p).strip()):
            continue
        if not EV.search(p):
            out.append(f"{path.name}: paragraph {i} has no <!-- ev:... --> comment")
        works = {WORK_OF[m.group(1)] for m in re.finditer(r"ev-([a-z]+)-", " ".join(EV.findall(p))) if m.group(1) in WORK_OF}
        if path.name == "cover.md" and len(works) > 1:
            out.append(f"{path.name}: paragraph {i} mixes proofs from {', '.join(sorted(works))}; one proof per paragraph")
    n = words(text)
    if n > hi or n < lo:
        out.append(f"{path.name}: {n} words (allowed {lo} to {hi})")
    if cfg.get("style.no_em_dash", False) and "\u2014" in text:
        out.append(f"{path.name}: em-dash")
    if PROSE_ONLY and re.search(r"^\s*([-*]|\d+\.)\s+", COMMENT.sub("", text), re.M):
        out.append(f"{path.name}: a list; ideas are told as prose, not a list")
    for m in TOOL_RUN.finditer(COMMENT.sub(" ", text)) if PROSE_ONLY else ():
        out.append(f"{path.name}: a run of names ('{m.group(0)[:60]}'); tell the idea, not the stack")
    for s in re.split(r"(?<=[.!?])\s+", COMMENT.sub(" ", text)):
        n_words = len(re.findall(r"[A-Za-z0-9'][A-Za-z0-9'.-]*", s))
        if n_words > LONG_SENTENCE:
            out.append(f"{path.name}: a {n_words}-word sentence ('{s.strip()[:50]}...'); keep sentences to about 20 words")
    for m in BANNED.finditer(COMMENT.sub(" ", text)) if BANNED else ():
        out.append(f"{path.name}: banned phrase '{m.group(0)}'")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import numbers_ledger  # noqa: PLC0415
    for f in numbers_ledger.check(path, numbers_ledger.LEDGER)["numbers"]:
        if f["verdict"] != "ok":
            out.append(f"{path.name}: {f['where']}: governed-employer number {f['number']!r}: {f['why']}")
    for term in cfg.get("policy.nda_terms", []) or []:
        if str(term).strip() and re.search(rf"\b{re.escape(str(term))}\b", text, re.I):
            out.append(f"{path.name}: names the NDA partner")
            break
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import check_holds  # noqa: PLC0415
        for hit in check_holds.check_text(COMMENT.sub(" ", text)) or []:
            term = getattr(hit, "held", None) or str(hit)
            out.append(f"{path.name}: held text {str(term)[:60]!r}")
    except Exception:  # noqa: BLE001  (the editor runs check_holds anyway)
        pass
    return out


def check_dir(folder: Path, cap: int | None = None) -> dict[str, list[str]]:
    return {name: check_file(folder / name, cap) for name in FILES if (folder / name).is_file()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cap", type=int, default=None, help="the form's word cap, overriding the defaults")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.folder.is_dir():
        print(f"no such folder: {a.folder}", file=sys.stderr)
        return 2
    res = check_dir(a.folder, a.cap)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for name, findings in res.items():
            for f in findings:
                print(f"FINDING {f}")
        print(f"letters: {len(res)} file(s), {sum(len(v) for v in res.values())} finding(s)")
    return 1 if any(res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
