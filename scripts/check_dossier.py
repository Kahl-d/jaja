#!/usr/bin/env python3
"""check_dossier.py - the company-researcher's citation audit of a dossier, as a script.

    python3 scripts/check_dossier.py companies/<slug>.md [...] [--root JS]

A dossier can cite a count to output that carries none, count items by eye, name facts from no source, end lines with
no citation, or be reported "all citations match" without anyone checking. This script runs the checks the
company-researcher card asks for, so a reply can paste a result instead:

  citation  every [n] (also [n, m] and [n][m]) outside ## Sources resolves to a numbered entry there
            ("1. ..." or "[1] ..."); no ## Sources section means every citation dangles
  source    every entry has a URL or a local path, and an access date (YYYY-MM-DD); a local path that does
            not exist under --root is reported; an entry number used twice is reported
  uncited   every item in a factual section ends with a citation: a list item, a table row or a paragraph,
            wrapped lines joined, the citation on its last line (trailing punctuation or a closing | allowed);
            and inside a list item or paragraph every SENTENCE ends with its own citation, so an uncited
            claim cannot hide in a cited paragraph. Abbreviations (Inc., U.S., Ph.D., e.g., Sep.) and initials
            do not end a sentence, and a citation after the full stop ("... level III. [3]") stays with it.
            Table rows are one claim each and are checked as a whole.
            Exempt: headings, blank lines, HTML comments, table headers and separators, fenced code (pasted
            script output: the line that introduces it carries the citation), an item containing
            "(to confirm)", and the sections ## Why this company for <you> (policy.dossier.angle_section; the angles cite evidence ids),
            ## Questions to ask them, ## Sources and ## Superseded. Every other section, the card's named
            ones (Snapshot, Product, Team, Stack, Recent news, Hiring signals, H-1B and LCA, E-Verify,
            Interview process, People to reach, Board fields) and any unnamed one, is factual.
  quote     on an item whose citations all point at local files, every double-quoted phrase ("..." or
            curly quotes; inline `code` ignored) appears in one of those files, case-sensitive, whitespace
            runs made one space. A script (.py) named in an entry is how the data was read, not the data:
            it is never searched. Items citing a URL-only source are not checked (nothing local to read).

A local path in an entry is read relative to --root (default: the department root when the dossier sits
in companies/, else the dossier's folder). Output: one line per problem, `path:line: kind: message`, then
`check_dossier: N problem(s)`. Exit 0 clean, 1 on any problem, 2 on a usage error (a missing file).
Deterministic, read-only, no network. It checks that citations resolve and quotes exist; whether a source
states a number is still the self-audit's job (grep the source), and counts come from pasted script output.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402

EXEMPT_SECTIONS = (str(cfg.get("policy.dossier.angle_section", "why this company for you")).lower(), "questions to ask them",
                   "sources", "superseded")
CITE_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
CITE_END_RE = re.compile(r"\[\d+(?:\s*[,;]\s*\d+)*\](?:\s*\[\d+(?:\s*[,;]\s*\d+)*\])*[\s.,;:|)]*$")
ENTRY_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\[(\d+)\]|(\d+)[.)])\s+")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
URL_RE = re.compile(r"https?://[^\s)>\]`]+|\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\."
                    r"(?:com|org|net|gov|edu|io|ai|co|dev|us|app|example)(?:/[^\s)>\]`]*)?", re.I)
PATH_RE = re.compile(r"(?<![\w@.:/-])((?:\.{1,2}/|/)?[\w@.-]+(?:/[\w@.-]+)+/?)")
SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")
QUOTE_RE = re.compile(r'"([^"]+)"|“([^”]+)”')
CODE_SPAN_RE = re.compile(r"`[^`]*`")
TO_CONFIRM_RE = re.compile(r"\(to confirm\)|\bunknown\s*[-–:,]\s*to confirm\b", re.I)   # both forms the cards use
# a sentence ends at . ! or ? followed by space and a capital, digit, quote or bracket-free start; not after an
# abbreviation or an initial (every claim carries its own citation, not only the paragraph's last one)
ABBREV = {"inc", "ltd", "co", "corp", "llc", "jr", "sr", "dr", "mr", "ms", "mrs", "st", "vs", "etc", "no", "approx",
          "e.g", "i.e", "u.s", "u.k", "ph.d", "m.s", "b.s", "b.a", "m.a", "b.tech", "m.tech", "fig", "al", "ave",
          "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
LETTERED = {"series", "round", "plan", "type", "class", "phase", "part", "section", "grade", "tier", "option",
            "model", "vitamin", "exhibit", "appendix", "schedule", "form", "level", "stage", "group"}
SENTENCE_END_RE = re.compile(r"[.!?][\"'”’)]*\s+(?=[A-Z0-9\"“])")   # a "(" after a stop continues the claim


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    kind: str         # citation | source | uncited | quote
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.message}"


@dataclass
class Item:
    """One logical line: a list item, a table row or a paragraph, with its wrapped lines joined."""
    line: int
    section: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Entry:
    number: int
    line: int
    text: str
    urls: list[str]
    paths: list[str]          # every local path named, as written
    files: list[Path]         # the ones that exist and can hold a quote (not scripts)


def sentences(text: str) -> list[str]:
    """Split one item's text into sentences, keeping a trailing [n] with the sentence before it."""
    out, start = [], 0
    for m in SENTENCE_END_RE.finditer(text):
        before = text[start:m.start() + 1]
        word = re.search(r"(?:(\S+)\s+)?([A-Za-z][A-Za-z.]*)\.$", before.rstrip("\"'”’)"))
        if word and word.group(2).lower() in ABBREV:
            continue
        if word and re.fullmatch(r"[A-Z]", word.group(2)) and (word.group(1) or "").lower().strip("(") not in LETTERED:
            continue                                               # an initial (J. Smith), not "Series B."
        if re.fullmatch(r"[\s\-*+\"“(]*\d{1,2}\.", before):          # an enumerator: "1." or - "3."
            continue
        upto = text[:m.end()].rstrip()              # through any closing quote after the stop
        if upto.count('"') % 2 or upto.count("“") > upto.count("”"):   # inside a quotation: one claim
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _excerpt(text: str, width: int = 100) -> str:
    s = _normalise(text)
    return s if len(s) <= width else s[:width] + " ..."


def parse_items(text: str) -> list[Item]:
    """Every logical line of the body (frontmatter, headings, fences, comments and table headers dropped)."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        start = close + 1 if close is not None else 0
    items: list[Item] = []
    current: Item | None = None
    section, fenced = "", False
    for i in range(start, len(lines)):
        line, n = lines[i], i + 1
        if FENCE_RE.match(line):
            fenced, current = not fenced, None
            continue
        if fenced:
            continue
        stripped = line.strip()
        heading = HEADING_RE.match(line)
        if not stripped or heading or stripped.startswith("<!--"):
            current = None
            if heading and len(heading.group(1)) == 2:
                section = heading.group(2).strip()
            continue
        if stripped.startswith("|"):
            current = None
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if SEPARATOR_RE.match(stripped) or SEPARATOR_RE.match(nxt.strip()):
                continue                                  # a separator, or the header row above one
            items.append(Item(n, section, [line]))
            continue
        if LIST_RE.match(line) or current is None:
            current = Item(n, section, [line])
            items.append(current)
        else:
            current.lines.append(line)
    return items


def _entry(number: int, item: Item, root: Path) -> Entry:
    body = ENTRY_RE.sub("", item.text, count=1).replace("`", " ")
    urls = URL_RE.findall(body)
    rest = URL_RE.sub(" ", body)
    paths, files = [], []
    for raw in PATH_RE.findall(rest):
        p = raw.rstrip(".,;:")
        target = Path(p) if p.startswith("/") else root / p
        if not (SUFFIX_RE.search(p.rstrip("/")) or p.endswith("/") or target.exists()):
            continue                                      # "H-1B/LCA", "FY2024/FY2025": not a path
        paths.append(p)
        if target.is_file() and target.suffix != ".py":
            files.append(target)
    return Entry(number, item.line, item.text, urls, paths, files)


def check_text(text: str, root: Path, shown: str = "dossier") -> list[Problem]:
    """Every problem in one dossier's text; local paths resolve under `root`."""
    items = parse_items(text)
    problems: list[Problem] = []
    entries: dict[int, Entry] = {}
    has_sources = any(it.section.lower() == "sources" for it in items) or \
        re.search(r"(?m)^##\s+Sources\s*$", text) is not None
    for it in items:
        if it.section.lower() != "sources":
            continue
        m = ENTRY_RE.match(it.lines[0])
        if not m:
            continue
        number = int(m.group(1) or m.group(2))
        e = _entry(number, it, root)
        if number in entries:
            problems.append(Problem(shown, it.line, "source", f"entry {number} is numbered twice "
                                    f"(first at line {entries[number].line})"))
            continue
        entries[number] = e
        if not e.urls and not e.paths:
            problems.append(Problem(shown, it.line, "source", f"entry {number} needs a URL or a local path: "
                                    f"{_excerpt(e.text)}"))
        for p in e.paths:
            target = Path(p) if p.startswith("/") else root / p
            if not target.exists():
                problems.append(Problem(shown, it.line, "source", f"entry {number}: local path {p} does not exist "
                                        f"under {root}"))
        if not any(_is_date(d) for d in DATE_RE.findall(e.text)):
            problems.append(Problem(shown, it.line, "source", f"entry {number} has no access date (YYYY-MM-DD): "
                                    f"{_excerpt(e.text)}"))
    for it in items:
        if it.section.lower() == "sources":
            continue
        cited: list[int] = []
        for k, line in enumerate(it.lines):
            for group in CITE_RE.findall(line):
                for num in (int(x) for x in re.split(r"\s*[,;]\s*", group)):
                    cited.append(num)
                    if num not in entries:
                        why = "has no entry in ## Sources" if has_sources else "dangles: the dossier has no ## Sources section"
                        problems.append(Problem(shown, it.line + k, "citation", f"[{num}] {why}"))
        exempt = it.section.lower() in EXEMPT_SECTIONS or TO_CONFIRM_RE.search(it.text)
        where = f"## {it.section}" if it.section else "the preamble"
        if not exempt and not CITE_END_RE.search(it.lines[-1]):
            problems.append(Problem(shown, it.line, "uncited", f"a factual line in {where} ends without a [n] "
                                    f"citation: {_excerpt(it.text)}"))
        elif not exempt and not it.lines[0].lstrip().startswith("|"):
            bare = next((s for s in sentences(it.text)[:-1] if not CITE_END_RE.search(s)), None)
            if bare:
                offset = it.text[:max(0, it.text.find(bare))].count("\n")
                problems.append(Problem(shown, it.line + offset, "uncited", f"a sentence in {where} ends without "
                                        f"a [n] citation: {_excerpt(bare)}"))
        sources = [entries.get(n) for n in dict.fromkeys(cited)]
        if not sources or any(s is None or not s.files for s in sources):
            continue                                      # nothing local to hold a quote against
        files = [f for s in sources for f in s.files]
        haystack = [_normalise(f.read_text(encoding="utf-8", errors="replace")) for f in files]
        for m in QUOTE_RE.finditer(CODE_SPAN_RE.sub(" ", it.text)):
            phrase = _normalise(m.group(1) or m.group(2) or "")
            if phrase and not any(phrase in h for h in haystack):
                names = ", ".join(sorted({f.name for f in files}))
                problems.append(Problem(shown, it.line, "quote", f'"{phrase}" is not in the cited file(s) {names}'))
    problems.sort(key=lambda p: p.line)
    return problems


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def default_root(path: Path) -> Path:
    path = Path(path).resolve()
    return path.parent.parent if path.parent.name == "companies" else path.parent


def check_file(path: Path | str, root: Path | str | None = None, shown: str | None = None) -> list[Problem]:
    path = Path(path)
    return check_text(path.read_text(encoding="utf-8"), Path(root) if root else default_root(path),
                      shown or str(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path, help="dossiers (companies/<slug>.md)")
    ap.add_argument("--root", type=Path, default=None,
                    help="where local source paths resolve (default: the department root above companies/)")
    args = ap.parse_args(argv)
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        print(f"error: no such file: {', '.join(missing)}", file=sys.stderr)
        return 2
    problems = [p for f in args.files for p in check_file(f, args.root)]
    for p in problems:
        print(p)
    print(f"check_dossier: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
