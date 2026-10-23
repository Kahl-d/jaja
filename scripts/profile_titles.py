#!/usr/bin/env python3
"""profile_titles.py - the render-safe title of a profile/facts.yaml entry .

`title` in facts.yaml is the record: it may carry held variants and conflict notes
("Data Analyst (LinkedIn and the CV say 'Data Associate' - C15)"). `title_render` is the
conservative form PROFILE.md uses. Anything that prints a title for output goes through
render_title() or render_view(); nothing prints `title` verbatim.

  render_title(entry)   title_render, else a clean title; raises UnrenderableTitle otherwise
  render_view(node)     a copy of any facts subtree with `title` swapped for the render form
                        (the record kept as `title_record`; a refused one flagged as `title_flag`)
  title_problems(text)  held strings (PROFILE-HOLDS.md) and conflict markers in a render title
  load_held(path)       every exact held string in PROFILE-HOLDS.md (quoted text and held terms), plus the
                        optional gitignored private-holds.txt beside it
  load_holds(path)      the same strings as Hold rows: kind (quoted or term), row reference, line
  shown(held)           a held string as output may name it: repr for a PROFILE-HOLDS.md string,
                        `<private hold #N>` for a private one (never its text)
  held_hits(text, held) every occurrence of a held string by the strictest pass that finds it: exact,
                        plain (Markdown emphasis, links, comments and line breaks removed) or loose (12+
                        characters; case, whitespace and typographic punctuation normalized). The one
                        matcher behind title_problems, render_resume.py lint and scripts/check_holds.py.
  iter_titled(facts)    (path, entry) for every mapping with a `title`
  iter_corpus_links     (path, value) for every `corpus:` link

Deterministic, no network, standard library plus PyYAML only through the callers.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
HOLDS = JS_ROOT / "profile" / "PROFILE-HOLDS.md"
# Held strings too private to print in a tracked file (a compensation figure): one per line, gitignored,
# read from beside the holds list; blank lines and '#' comments ignored. Optional: absent changes nothing.
# Output never prints one: shown() names it `<private hold #N>`, N its entry number in the file.
PRIVATE_HOLDS_NAME = "private-holds.txt"

# A conflict id, a status or sourcing note, or an offered alternative: none belongs in a rendered title.
MARKER_RE = re.compile(r"\bC\d{2}\b|\bproposed\b|\bpresumed\b|\bpossibly\b|\bsee\b|\bor\b|\bsays?\b|"
                       r"\bLinkedIn\b|\bto confirm\b|\bunknown\b", re.I)
QUOTED_RE = re.compile(r'"([^"]+)"')
HELD_COLUMNS = ("held text", "term")          # table headers whose cells hold the must-not-add text


class UnrenderableTitle(ValueError):
    """The entry has no render-safe title: fix facts.yaml (add `title_render`), never print `title`."""


@dataclass(frozen=True)
class Hold:
    """One held string: `quoted` when a "Held text" cell quotes it (disputed text, never copied anywhere),
    `term` when a "Term" cell is it (a skill held off resumes, never claimed). `ref` is the row's first cell
    (conflict id, evidence id or the term, or private-holds.txt); `line` is its line in that file. `entry` is a
    private hold's entry number (the Nth held string in private-holds.txt, comments and blank lines not
    counted), the N of its `<private hold #N>` label; 0 for a PROFILE-HOLDS.md row."""
    text: str
    kind: str
    ref: str
    line: int
    entry: int = 0


def load_holds(path: Path | str = HOLDS) -> list[Hold]:
    """Every held string in every table of PROFILE-HOLDS.md, in file order: the double-quoted text in each
    "Held text" cell and every "Term" cell (whole, and split on '/'), then each line of the private list
    beside it (kind quoted, ref private-holds.txt). A missing PROFILE-HOLDS.md yields [] even when the
    private list exists: missing is not empty."""
    path = Path(path)
    if not path.is_file():
        return []
    holds: list[Hold] = []
    col: int | None = None
    whole = False                             # a "Term" cell is the held text; a "Held text" cell quotes it
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|"):
            col = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if col is None:
            col = next((i for i, c in enumerate(cells) if c.lower().startswith(HELD_COLUMNS)), -1)
            whole = col >= 0 and cells[col].lower().startswith("term")
            continue
        if col < 0 or col >= len(cells) or set(cells[col]) <= set("-: "):
            continue
        cell = cells[col]
        found = [cell, *cell.split("/")] if whole else QUOTED_RE.findall(cell)
        row: list[str] = []
        for s in (f.strip() for f in found):
            if len(s) >= 2 and s not in row:
                row.append(s)
                holds.append(Hold(s, "term" if whole else "quoted", cells[0], n))
    return holds + _private_holds(path.parent / PRIVATE_HOLDS_NAME)


def _private_holds(path: Path) -> list[Hold]:
    """One Hold per line of the private list: quoted (never a gap), ref the file name, never its text. Every
    string read is registered so shown() masks it wherever it is printed later."""
    if not path.is_file():
        return []
    out: list[Hold] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if len(s) >= 2 and not s.startswith("#"):
            out.append(Hold(s, "quoted", PRIVATE_HOLDS_NAME, n, len(out) + 1))
    for h in reversed(out):                                  # a string listed twice keeps its first entry number
        _PRIVATE_ENTRIES[h.text] = h.entry
    return out


# Private hold text -> entry number, from every private list read in this process (the default one is read on
# first use by private_entry, so a caller that passes its own held list still gets the mask).
_PRIVATE_ENTRIES: dict[str, int] = {}
_DEFAULT_PRIVATE_READ = False


def private_entry(held: str) -> int | None:
    """The entry number of a private hold (its N in `<private hold #N>`), else None."""
    global _DEFAULT_PRIVATE_READ
    if held not in _PRIVATE_ENTRIES and not _DEFAULT_PRIVATE_READ:
        _DEFAULT_PRIVATE_READ = True
        _private_holds(HOLDS.parent / PRIVATE_HOLDS_NAME)
    return _PRIVATE_ENTRIES.get(held)


def shown(held: str) -> str:
    """A held string as any output (a report line, a JSON field, an error message) may name it: repr for a
    PROFILE-HOLDS.md string, `<private hold #N>` for a private one. A private hold's text is never printed."""
    n = private_entry(held)
    return repr(held) if n is None else f"<private hold #{n}>"


def held_sources(held: Iterable[str]) -> str:
    """The file(s) the given held strings come from, for a message: profile/PROFILE-HOLDS.md,
    profile/private-holds.txt or both."""
    held = list(held)
    private = [h for h in held if private_entry(h) is not None]
    names = (["profile/PROFILE-HOLDS.md"] if len(private) < len(held) else []) + \
        ([f"profile/{PRIVATE_HOLDS_NAME}"] if private else [])
    return " and ".join(names) or "profile/PROFILE-HOLDS.md"


def load_held(path: Path | str = HOLDS) -> list[str]:
    """Exact held strings from every table of PROFILE-HOLDS.md and the private list (load_holds), each once,
    in file order."""
    return list(dict.fromkeys(h.text for h in load_holds(path)))


# --------------------------------------------------------------------------- held-text matching
# A plain substring check is blind to Markdown: "**+2.1 Macro-F1** over BERT-base" hides a held string.
LOOSE_MIN = 12            # case folding turns short holds into noise ("ALMA" is held, "Alma" is right)
PASSES = ("exact", "plain", "loose")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_LINK_RE = re.compile(r"!?\[([^\]\n]*)\](?:\([^)\n]*\)|\[[^\]\n]*\])")    # [text](url), ![alt](src), [text][ref]
_WIKILINK_RE = re.compile(r"\[\[([^\]|\n]*)(?:\|([^\]\n]*))?\]\]")           # [[slug]], [[slug|text]]
_EMPHASIS_RE = re.compile(r"\*+|`+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")     # *, **, backticks, _ and __ at word edges
_QUOTE_MARK_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.M)                    # blockquote markers
_PUNCT = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
          "\u2212": "-", "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u201c": '"',
          "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2026": "...", "\u00d7": "x"}


@dataclass(frozen=True)
class HeldHit:
    held: str
    pass_: str        # exact | plain | loose
    start: int        # index in the checked text of the first matched character
    end: int          # index one past the last matched character


def visible_map(text: str, skip: tuple[int, int] | None = None) -> tuple[str, list[int]]:
    """The text a reader sees and, for each of its characters, the index it came from: HTML comments dropped,
    links and images reduced to their text, emphasis markers (*, **, backticks, _ and __ at word edges) and
    blockquote markers removed, every whitespace run (line breaks included) one space. `skip` (a span such
    as frontmatter) is dropped whole."""
    drop = bytearray(len(text))

    def mark(a: int, b: int) -> None:
        drop[a:b] = b"\x01" * (b - a)

    if skip:
        mark(*skip)
    for m in _COMMENT_RE.finditer(text):
        mark(m.start(), m.end())
    for m in _LINK_RE.finditer(text):
        mark(m.start(), m.start(1))
        mark(m.end(1), m.end())
    for m in _WIKILINK_RE.finditer(text):
        g = 2 if m.group(2) is not None else 1
        mark(m.start(), m.start(g))
        mark(m.end(g), m.end())
    for rx in (_EMPHASIS_RE, _QUOTE_MARK_RE):
        for m in rx.finditer(text):
            mark(m.start(), m.end())
    out: list[str] = []
    idx: list[int] = []
    space = False
    for i, ch in enumerate(text):
        if drop[i]:
            continue
        if ch.isspace():
            if out and not space:
                out.append(" ")
                idx.append(i)
            space = True
            continue
        out.append(ch)
        idx.append(i)
        space = False
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def visible_text(text: str) -> str:
    """visible_map without the index."""
    return visible_map(str(text or ""))[0]


def loose_map(visible: str, fold: bool = True) -> tuple[str, list[int]]:
    """Typographic dashes, quotes and ellipses as ASCII, whitespace removed, case folded when `fold`; with the
    source index of every character."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(visible):
        if ch.isspace():
            continue
        mapped = _PUNCT.get(ch, ch)
        for c in (mapped.casefold() if fold else mapped):
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _word_breaks(visible: str, fold: bool) -> tuple[str, list[int]]:
    """loose_map of a held string plus the key positions where a space separated two letters: the loose pass
    forgives spacing next to digits and punctuation ("5-30s" for "5-30 s") but never fuses two words into one
    ("jordansmith" is not "Jordan Smith")."""
    key, idx = loose_map(visible, fold)
    breaks = [k for k in range(1, len(key))
              if key[k - 1].isalpha() and key[k].isalpha() and idx[k] - idx[k - 1] > 1]
    return key, breaks


@functools.lru_cache(maxsize=8192)
def _held_forms(held: str, loose_min: int) -> tuple[str, bool, str, tuple[int, ...]]:
    """A held string's visible form, whether its loose form folds case, the loose form and its word breaks
    (computed once per string: the render and pack lints call held_hits for every field or line)."""
    visible = visible_text(held)
    fold = len(held) >= loose_min
    key, breaks = _word_breaks(visible, fold)
    return visible, fold, key, tuple(breaks)


def _find_all(hay: str, needle: str) -> Iterator[int]:
    pos = hay.find(needle)
    while needle and pos >= 0:
        yield pos
        pos = hay.find(needle, pos + len(needle))


def _overlaps(a: int, b: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < e and s < b for s, e in spans)


# A figure held as digits ("12500", "12,500", "$12.5K", "0.642") names one number, not a digit run inside a longer
# one: a URL slug "4125000" is not the held "12500" (a private hold is the typical case).
_NUMERIC_HOLD_RE = re.compile(r"[$€£+~-]?\d[\d,.]*[%kKmMbB+]?")
_SEPARATORS = ",."


def _digit_bounded(hay: str, start: int, end: int, held: str) -> bool:
    """Whether hay[start:end] (a match of `held`) sits on digit boundaries: a held string that starts with a digit
    is not preceded by one, and one that ends with a digit is not followed by one. A numeric hold (digits with
    separators, an optional currency sign and unit) also refuses a separator that continues the number
    ("12,500" in "112,500" or "12,500,000"; "0.642" in "0.6425" is already refused)."""
    numeric = bool(_NUMERIC_HOLD_RE.fullmatch(held))
    if held[:1].isdigit() and start > 0:
        before = hay[start - 1]
        if before.isdigit() or (numeric and before in _SEPARATORS and start > 1 and hay[start - 2].isdigit()):
            return False
    if held[-1:].isdigit() and end < len(hay):
        after = hay[end]
        if after.isdigit() or (numeric and after in _SEPARATORS and end + 1 < len(hay) and hay[end + 1].isdigit()):
            return False
    return True


def _find_bounded(hay: str, needle: str) -> Iterator[int]:
    """_find_all keeping only matches on digit boundaries; after a refused match the search moves on one
    character, so a refused digit run never hides a real occurrence that overlaps it."""
    pos = hay.find(needle) if needle else -1
    while pos >= 0:
        if _digit_bounded(hay, pos, pos + len(needle), needle):
            yield pos
            pos = hay.find(needle, pos + len(needle))
        else:
            pos = hay.find(needle, pos + 1)


def held_hits(text: Any, held: Iterable[str], *, skip: tuple[int, int] | None = None,
              loose_min: int = LOOSE_MIN) -> list[HeldHit]:
    """Every occurrence of every held string in `text`, each reported once by the strictest pass that finds it:
      exact  a case-sensitive substring of the raw text (the old check);
      plain  a case-sensitive substring of the visible text (visible_map), so emphasis, links, comments and
             line breaks cannot split it;
      loose  the visible text with whitespace removed and typographic punctuation mapped to ASCII, starting
             and ending on word boundaries and never fusing two held words into one; case is folded only
             for held strings of `loose_min`+ characters (a short hold is exact text: "ALMA" vs "Alma").
    `skip` hides a span (frontmatter) from the plain and loose passes; the exact pass reads everything.
    Every pass matches on digit boundaries (_digit_bounded): a held figure is never a digit run inside a longer
    number. Ordered by position."""
    raw = str(text or "")
    vis, vmap = visible_map(raw, skip)
    maps = {fold: loose_map(vis, fold) for fold in (True, False)}
    out: list[HeldHit] = []
    for h in dict.fromkeys(str(x) for x in held if str(x)):
        found: list[tuple[int, int]] = []
        for i in _find_bounded(raw, h):                   # never a digit run inside a longer number ("4125000")
            found.append((i, i + len(h)))
            out.append(HeldHit(h, "exact", i, i + len(h)))
        hv, fold, hl, breaks = _held_forms(h, loose_min)
        for i in _find_bounded(vis, hv):
            a, b = vmap[i], vmap[i + len(hv) - 1] + 1
            if not _overlaps(a, b, found):
                found.append((a, b))
                out.append(HeldHit(h, "plain", a, b))
        loose, lmap = maps[fold]
        for i in _find_all(loose, hl):
            vs, ve = lmap[i], lmap[i + len(hl) - 1]
            if (hl[0].isalnum() and vs > 0 and vis[vs - 1].isalnum()) or \
               (hl[-1].isalnum() and ve + 1 < len(vis) and vis[ve + 1].isalnum()):
                continue                                  # inside another word ("metadata associated")
            if any(lmap[i + k] - lmap[i + k - 1] <= 1 for k in breaks):
                continue                                  # two held words fused into one ("jordansmith")
            if not _digit_bounded(vis, vs, ve + 1, hv):
                continue                                  # a held figure continued by a separator ("12,500,000")
            a, b = vmap[vs], vmap[ve] + 1
            if not _overlaps(a, b, found):
                found.append((a, b))
                out.append(HeldHit(h, "loose", a, b))
    # a case variant of another hold ("JORDAN SMITH" and "Jordan Smith") never reports a place twice
    def key(x: HeldHit) -> tuple[int, int, str]:
        return x.start, x.end, loose_map(_held_forms(x.held, loose_min)[0])[0]

    strict = {key(x) for x in out if x.pass_ != "loose"}
    out = [x for x in out if x.pass_ != "loose" or key(x) not in strict]
    return sorted(out, key=lambda x: (x.start, x.end, x.held))


def _strictest(hits: Iterable[HeldHit], held: Iterable[str]) -> list[tuple[str, str]]:
    """(held string, its strictest pass) once per string, in `held` order."""
    best: dict[str, str] = {}
    for hit in hits:
        if hit.held not in best or PASSES.index(hit.pass_) < PASSES.index(best[hit.held]):
            best[hit.held] = hit.pass_
    return [(h, best[h]) for h in dict.fromkeys(held) if h in best]


def held_in(text: Any, held: Iterable[str]) -> list[str]:
    """The held strings found in `text` by any pass (held_hits), each once, in `held` order."""
    held = list(held)
    return [h for h, _ in _strictest(held_hits(text, held), held)]


def held_found(text: Any, held: Iterable[str]) -> list[tuple[str, str]]:
    """(held string, its strictest pass) for each held string found in `text`, in `held` order. For logic only:
    print a string through shown() or held_note(), never as is (it may be a private hold)."""
    held = list(held)
    return _strictest(held_hits(text, held), held)


def held_note(held: str, how: str) -> str:
    """One found held string as a short label: shown(held), with the pass appended unless it is exact."""
    return shown(held) if how == "exact" else f"{shown(held)} ({how})"


def held_notes(text: Any, held: Iterable[str]) -> list[str]:
    """Held strings found in `text` as short labels: 'Data Associate' for an exact hit, with the pass
    appended for the others ('+2.1 Macro-F1 over BERT-base' (plain)). A private hold is `<private hold #N>`."""
    return [held_note(h, how) for h, how in held_found(text, held)]


def title_problems(text: Any, held: Iterable[str] | None = None) -> list[str]:
    """Why `text` cannot be a rendered title: conflict markers and held strings (held_hits: exact, then with
    emphasis and spacing removed, then loose for long holds). Empty list = clean. A descriptive parenthesis
    is allowed."""
    held = load_held() if held is None else list(held)
    s = str(text or "").strip()
    if not s:
        return ["empty title"]
    out = [f"conflict marker {m.group(0)!r}" for m in MARKER_RE.finditer(visible_text(s))]
    out += [f"held text {shown(h)}" if how == "exact" else f"held text {shown(h)} ({how} match: emphasis, spacing "
            "or punctuation split it)" for h, how in _strictest(held_hits(s, held), held)]
    return out


def raw_title_problems(text: Any, held: Iterable[str] | None = None) -> list[str]:
    """A raw `title` used as a fallback is stricter: any parenthesis may be an alternative or a note."""
    out = title_problems(text, held)
    if "(" in str(text or ""):
        out.append("parenthesis in a record title (alternatives or notes)")
    return out


def render_title(entry: dict[str, Any], held: Iterable[str] | None = None) -> str:
    """The title to print for a facts entry: `title_render`, else `title` when it is clean."""
    held = load_held() if held is None else list(held)
    if "title_render" in entry:
        problems = title_problems(entry["title_render"], held)
        field = "title_render"
    else:
        problems = raw_title_problems(entry.get("title"), held)
        field = "title (no title_render)"
    if problems:
        raise UnrenderableTitle(f"{entry.get('id') or 'entry'}: {field} is not renderable: " + "; ".join(problems))
    return str(entry.get("title_render", entry.get("title"))).strip()


def render_view(node: Any, held: Iterable[str] | None = None) -> Any:
    """A copy of a facts subtree safe to hand to a renderer or an agent: in every mapping with a
    `title`, `title` becomes the render form and the record moves to `title_record`; when no
    render form exists, `title` is None and `title_flag` says why. The input is not modified."""
    held = load_held() if held is None else list(held)
    if isinstance(node, list):
        return [render_view(v, held) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: render_view(v, held) for k, v in node.items() if k != "title_render"}
    if "title" in node:
        try:
            safe = render_title(node, held)
        except UnrenderableTitle as exc:
            out["title"], out["title_record"], out["title_flag"] = None, node["title"], str(exc)
        else:
            if safe != node["title"]:
                out["title"], out["title_record"] = safe, node["title"]
    return out


def _walk(node: Any, path: str) -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def iter_titled(facts: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every mapping with a `title` (employment, the thesis, presentations), skipping `conflicts`."""
    for path, node in _walk(facts, ""):
        if isinstance(node, dict) and "title" in node and not path.startswith("conflicts"):
            yield path, node


def iter_corpus_links(facts: Any) -> Iterator[tuple[str, str]]:
    """Every `corpus:` value (a path relative to profile/), with where it sits."""
    for path, node in _walk(facts, ""):
        if isinstance(node, dict) and node.get("corpus") is not None:
            yield f"{path}.corpus", str(node["corpus"])
