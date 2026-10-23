#!/usr/bin/env python3
"""check_holds.py - held text (profile/PROFILE-HOLDS.md) in any file, with the Markdown blind spots closed.

    python3 scripts/check_holds.py FILE... [--json] [--holds profile/PROFILE-HOLDS.md]

A plain substring check misses held text that Markdown emphasis or a line break splits: a held string such as
"**+2.1 Macro-F1** over BERT-base" can be split by bold markers or a line break. Three passes;
each occurrence is reported once, by the strictest pass that finds it. The matching is
profile_titles.held_hits, the same code behind title_problems and `render_resume.py lint`:

  exact   every held string, case-sensitive, as a substring of the raw file (frontmatter included)
  plain   the same against the visible text: HTML comments dropped, links and images reduced to their
          text, emphasis markers (**, __, *, _ at word edges, backticks) and blockquote markers removed,
          every whitespace run (line breaks included) one space
  loose   the visible text with whitespace removed and typographic dashes, quotes and ellipses mapped to
          ASCII; a match starts and ends on a word boundary and never fuses two held words into one
          ("jordansmith" is not "Jordan Smith"); case is folded only for held strings of 12+ characters
          ("ALMA" is held, "Alma" is right). A loose hit means "read it": spacing, case or dash style
          differ from the held text
  mode    a held list of mode names also holds each name on its own: "<Name>
          mode" in any case (the visible text, so emphasis and a line break cannot split it) is a hit. The names
          are read from the holds list itself (mode_names), so lifting the hold lifts the check. A word like
          "debug mode" is flagged too: read it, and reword it if it is the product
Frontmatter is metadata: only the exact pass reads it. The holds list includes the optional gitignored
profile/private-holds.txt beside it (one held string per line; profile_titles.load_holds), so a figure can be
held without being printed in a tracked file. A hit on one names the file and the entry number, never the text:
every output (the text report and --json) prints it as `<private hold #N>` (profile_titles.shown).

Gap register (Markdown files only): a Term hold (a skill held off resumes, such as vLLM) named inside a
section headed "Coverage matrix" or "Gaps ..." is a recorded gap, not a claim (the profile-editor records
gaps there and never fills them). It prints as a `gap` note and does not fail. Quoted held text fails
everywhere, the gap register included, and so does a term that is also quoted in a conflict row.
A company dossier (companies/*.md) has a second register: its company-fact sections (Snapshot, Product, Team, Stack,
Recent news, Hiring signals, H-1B, E-Verify, Interview process, Board fields, Sources, Superseded) describe the
company, so a Term hold named there ("Docker" in the company's stack) is a note, not the owner's claim. The sections
about the owner (Why this company for the owner, People to reach, Questions to ask them) are checked in full.
A coverage matrix (*.matrix.yaml) quotes the employer: its JD fields (text, terms, synonyms, heading, title) are the
posting's words, so any held string there is a note; the fields that are ours (answer, ceiling, note) are checked in
full, quoted holds included.

Exit 0 when nothing is found, 1 on any hit, 2 on a usage error (a missing file, or a missing or empty
holds list: missing is not empty, so nothing is called clean). Deterministic, no network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profile_titles as pt  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
GAP_HEADING_RE = re.compile(r"^(?:coverage matrix|gaps?)\b", re.I)
COMPANY_FACT_RE = re.compile(r"(snapshot|product|team|stack|recent news|news|hiring signals|h-1b|e-verify|"
                             r"interview process|board fields|sources|superseded)\b", re.I)
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
MARKDOWN_SUFFIXES = (".md", ".markdown")
# A held list of capitalized names ("Plan, Code, Agent, Debug", "(Plan, Code, Agent, Debug)", "Plan, Code, Agent and
# Debug"): three or more single words. Read from the holds themselves (a hold that lists a product's mode names),
# so the check follows the list.
MODE_LIST_RE = re.compile(r"^\(?([A-Z][A-Za-z]+(?:, [A-Z][A-Za-z]+)+(?:,? and [A-Z][A-Za-z]+)?)\)?$")
MODE_SPLIT_RE = re.compile(r",\s*(?:and\s+)?|\s+and\s+")
MODE_PASS = "mode"
CHECK_PASSES = (*pt.PASSES, MODE_PASS)
WHY = {"exact": "",
       "plain": "emphasis, a link, a comment or a line break split it",
       "loose": "spacing, case or dash style differ: read it",
       MODE_PASS: "a held mode name used on its own: describe the product without its mode names",
       "gap": "in a register (a recorded gap, a company fact or the employer's own words), not a claim"}


@dataclass(frozen=True)
class Hit:
    held: str
    pass_: str        # exact | plain | loose | mode
    line: int         # 1-based line of the first matched character
    column: int       # 1-based column of the first matched character
    kind: str         # quoted | term
    ref: str          # the holds row(s): conflict id, evidence id or the term
    gap: bool = False  # a term named in a gap register: a note, not a failure

    def as_dict(self) -> dict[str, Any]:
        """The hit for a JSON report: `held` is the text for a PROFILE-HOLDS.md hold and `<private hold #N>` for a
        private one (whose text is never printed); `private_entry` is N, or None."""
        n = pt.private_entry(self.held)
        return {"held": self.held if n is None else pt.shown(self.held), "private_entry": n, "pass": self.pass_,
                "line": self.line, "column": self.column, "kind": self.kind, "ref": self.ref, "gap": self.gap}


def frontmatter_span(text: str) -> tuple[int, int] | None:
    """(start, end) of a leading `---` block closed by a `---` line, else None."""
    m = re.match(r"---[ \t]*\r?\n", text)
    if not m:
        return None
    close = re.compile(r"^---[ \t]*\r?$", re.M).search(text, m.end())
    return (0, close.end()) if close else None


def gap_spans(text: str, start: int = 0, company: bool = False) -> list[tuple[int, int]]:
    """Character spans of every gap register: from a heading "Coverage matrix" or "Gaps ..." to the next heading
    of the same or a higher level. Scanning begins at `start` (after frontmatter, whose YAML comments start
    with '#'); headings inside fenced code are ignored."""
    heads: list[tuple[int, int, str]] = []
    offset, fenced = start, False
    for line in text[start:].splitlines(keepends=True):
        if FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            m = HEADING_RE.match(line.rstrip("\r\n"))
            if m:
                heads.append((offset, len(m.group(1)), m.group(2).strip()))
        offset += len(line)
    spans = []
    for i, (start, level, title) in enumerate(heads):
        if GAP_HEADING_RE.match(title) or (company and COMPANY_FACT_RE.match(title)):
            end = next((o for o, lv, _ in heads[i + 1:] if lv <= level), len(text))
            spans.append((start, end))
    return spans


JD_KEYS = ("text", "terms", "synonyms", "heading", "title")
JD_KEY_RE = re.compile(r"^(\s*)(- )?(" + "|".join(JD_KEYS) + r")\s*:")


def matrix_jd_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of a matrix's JD fields: the key's line and every line under it (deeper, or a `- ` item at the
    key's own indent), up to the next key at that indent or less."""
    spans, offset, cur = [], 0, None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        indent = len(body) - len(body.lstrip(" "))
        if cur is not None:
            base, start = cur
            if body.strip() and not (indent > base or (indent == base and body.lstrip().startswith("- ")
                                                         and not JD_KEY_RE.match(body))):
                spans.append((start, offset))
                cur = None
        m = JD_KEY_RE.match(body)
        if m and cur is None:
            cur = (len(m.group(1)) + (2 if m.group(2) else 0), offset)
        offset += len(line)
    if cur is not None:
        spans.append((cur[1], offset))
    return spans


def _rows(holds: Iterable[pt.Hold]) -> dict[str, tuple[str, str]]:
    """held text -> (kind, refs). A string quoted in any row is `quoted` (never a gap), whatever else holds it."""
    kinds: dict[str, str] = {}
    refs: dict[str, list[str]] = {}
    for h in holds:
        if kinds.get(h.text) != "quoted":
            kinds[h.text] = h.kind
        refs.setdefault(h.text, [])
        if h.ref not in refs[h.text]:
            refs[h.text].append(h.ref)
    return {t: (kinds[t], ", ".join(refs[t])) for t in kinds}


def mode_names(holds: Iterable[pt.Hold]) -> dict[str, tuple[str, str]]:
    """name -> (the hold text it came from, the row's refs) for every quoted hold that lists three or more
    capitalized names ("Plan, Code, Agent, Debug"), in list order."""
    out: dict[str, tuple[str, str]] = {}
    for h in holds:
        m = MODE_LIST_RE.match(h.text) if h.kind == "quoted" else None
        if not m:
            continue
        names = [n for n in MODE_SPLIT_RE.split(m.group(1)) if n]
        if len(names) >= 3:
            for n in names:
                out.setdefault(n, (h.text, h.ref))
    return out


def mode_hits(text: str, holds: Iterable[pt.Hold], skip: tuple[int, int] | None = None,
              noted: list[tuple[int, int]] | None = None) -> list[Hit]:
    """Each held mode name used as "<Name> mode" (any case) in the visible text, one hit per place. A place inside
    a span in `noted` (a matrix's JD fields: the employer's words) is a gap note, as for every other hold."""
    names = mode_names(holds)
    if not names:
        return []
    vis, vmap = pt.visible_map(text, skip)
    out: list[Hit] = []
    for name, (_, ref) in names.items():
        for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(name)}\s+mode(?![A-Za-z0-9])", vis, re.I):
            start = vmap[m.start()]
            line_start = text.rfind("\n", 0, start) + 1
            out.append(Hit(f"{name} mode", MODE_PASS, text.count("\n", 0, start) + 1, start - line_start + 1,
                           "quoted", ref, any(a <= start < b for a, b in noted or [])))
    return out


def check_text(text: str, holds: Iterable[pt.Hold] | None = None, *, markdown: bool = True,
               company: bool = False, matrix: bool = False) -> list[Hit]:
    """Every held-text occurrence in `text` (see the module docstring), ordered by position. `markdown` turns on
    the frontmatter rule and the gap register."""
    all_holds = pt.load_holds() if holds is None else list(holds)
    rows = _rows(all_holds)
    skip = frontmatter_span(text) if markdown else None
    gaps = gap_spans(text, skip[1] if skip else 0, company=company) if markdown else []
    quoted_jd = matrix_jd_spans(text) if matrix else []
    out: list[Hit] = []
    for hit in pt.held_hits(text, list(rows), skip=skip):
        kind, ref = rows[hit.held]
        line_start = text.rfind("\n", 0, hit.start) + 1
        gap = (kind == "term" and any(a <= hit.start < b for a, b in gaps)) or any(a <= hit.start < b for a, b in quoted_jd)
        out.append(Hit(hit.held, hit.pass_, text.count("\n", 0, hit.start) + 1, hit.start - line_start + 1,
                       kind, ref, gap))
    out += mode_hits(text, all_holds, skip, quoted_jd)
    return sorted(out, key=lambda h: (h.line, h.column))


def check_file(path: Path | str, holds: Iterable[pt.Hold] | None = None) -> list[Hit]:
    path = Path(path)
    return check_text(path.read_text(encoding="utf-8"), holds, markdown=path.suffix.lower() in MARKDOWN_SUFFIXES,
                      company=path.parent.name == "companies", matrix=path.name.endswith(".matrix.yaml"))


def _counts(hits: list[Hit]) -> dict[str, int]:
    counts = {p: sum(1 for h in hits if h.pass_ == p and not h.gap) for p in CHECK_PASSES}
    counts["gap"] = sum(1 for h in hits if h.gap)
    return counts


def _line(path: str, hit: Hit) -> str:
    label = "gap" if hit.gap else hit.pass_
    why = WHY["gap" if hit.gap else hit.pass_]
    note = f"{hit.kind} {hit.ref}" + (f"; {why}" if why else "")
    return f"{path}:{hit.line}: {label:<5} {pt.shown(hit.held)} ({note})"


def _shown(path: Path) -> str:
    """A path as the reader typed it or relative to the working directory, never a long absolute path."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path, help="files to check (Markdown, YAML, text)")
    ap.add_argument("--json", action="store_true", help="print one JSON report")
    ap.add_argument("--holds", type=Path, default=pt.HOLDS, help="the holds list (default: profile/PROFILE-HOLDS.md)")
    args = ap.parse_args(argv)
    holds = pt.load_holds(args.holds)
    if not holds:
        why = "missing" if not args.holds.is_file() else "has no held strings"
        print(f"error: holds list {args.holds} {why}; nothing was checked (missing is not empty)", file=sys.stderr)
        return 2
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        print(f"error: no such file: {', '.join(missing)}", file=sys.stderr)
        return 2
    held_count = len(pt.load_held(args.holds))
    results = [(f, check_file(f, holds)) for f in args.files]
    failed = any(not h.gap for _, hits in results for h in hits)
    if args.json:
        print(json.dumps({"holds": _shown(args.holds), "held_count": held_count, "ok": not failed,
                          "files": [{"path": str(f), "ok": all(h.gap for h in hits), "counts": _counts(hits),
                                     "hits": [h.as_dict() for h in hits]} for f, hits in results]}, indent=2))
        return 1 if failed else 0
    for f, hits in results:
        for hit in hits:
            print(_line(str(f), hit))
        c = _counts(hits)
        bad = c["exact"] + c["plain"] + c["loose"] + c[MODE_PASS]
        mode = f", mode {c[MODE_PASS]}" if c[MODE_PASS] else ""
        gaps = f"; {c['gap']} gap note{'s' if c['gap'] != 1 else ''}" if c["gap"] else ""
        if bad:
            print(f"FAIL {f}: {bad} held-text hit{'s' if bad != 1 else ''} (exact {c['exact']}, plain {c['plain']}, "
                  f"loose {c['loose']}{mode}){gaps}; {held_count} held strings from {_shown(args.holds)}")
        else:
            print(f"OK {f}: no held text{gaps}; {held_count} held strings from {_shown(args.holds)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
