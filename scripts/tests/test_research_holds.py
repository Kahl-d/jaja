"""Held text in the research the resume roles read: research/lenses.md and research/profile-requirements.md.

Both files guide what resume-tailor and profile-editor treat as usable evidence. A held string (every exact
string `profile_titles.load_held` reads from profile/PROFILE-HOLDS.md) may appear in them only on a line
marked held: the line itself, or the paragraph, list item or table row it belongs to, contains the word
"held" (for example "(C25 open; held, see PROFILE-HOLDS.md)"). Anywhere else it reads as evidence, and the
check fails with file, line and the held string.

Run it alone for a report:  python3 scripts/tests/test_research_holds.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import profile_titles as pt  # noqa: E402
import held_safe as hs  # noqa: E402  (tests/: compare real holds without printing a private one)

from layout import JS  # noqa: E402
RESEARCH = ("research/lenses.md", "research/profile-requirements.md")
MARK = re.compile(r"\bheld\b", re.I)
ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])\s")
HEADING = re.compile(r"^#{1,6}\s")


def blocks(text: str) -> list[tuple[int, list[str]]]:
    """(first line number, lines) for every Markdown block: a table row, a heading, a list item with its
    continuation lines, or a paragraph. Blank lines end a block; frontmatter is one block."""
    lines = text.splitlines()
    out: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    start = 0
    i = 0
    if lines and lines[0].strip() == "---":
        end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), len(lines) - 1)
        out.append((1, lines[:end + 1]))
        i = end + 1

    def flush() -> None:
        nonlocal cur
        if cur:
            out.append((start, cur))
        cur = []

    for n in range(i, len(lines)):
        line = lines[n]
        if not line.strip():
            flush()
        elif line.lstrip().startswith("|") or HEADING.match(line):
            flush()
            out.append((n + 1, [line]))
        elif ITEM.match(line) or not cur:
            flush()
            start, cur = n + 1, [line]
        else:
            cur.append(line)
    flush()
    return out


def unmarked(text: str, held: list[str]) -> list[tuple[int, str, str]]:
    """(line number, held string, line) for every held string on a line whose block is not marked held."""
    found = []
    for first, block in blocks(text):
        if MARK.search("\n".join(block)):
            continue
        for k, line in enumerate(block):
            found += [(first + k, h, line.strip()) for h in held if h in line]
    return found


def held_strings() -> list[str]:
    return pt.load_held(JS / "profile" / "PROFILE-HOLDS.md")


# ----------------------------------------------------------------------------- the rule
HELD = ["60 fps", "LoRA/PEFT", "forked by two teammates"]


def test_a_held_string_passes_only_where_its_line_or_block_is_marked_held():
    text = """---
title: fixture
---

## Lens

**Must-show.** Streams code at 60 fps.

**Do not claim without evidence (held; see PROFILE-HOLDS.md).** LoRA/PEFT; the streaming at
60 fps; forked by two teammates.

1. Fine-tuning pipelines with LoRA/PEFT
   and more text on the next line.
2. Fine-tuning pipelines (the method is held, C03) with LoRA/PEFT.
- A withheld claim: forked by two teammates

| Claim | Verdict |
|---|---|
| 60 fps streaming | Keep |
| 60 fps streaming | **Held** (C25) |
"""
    hits = unmarked(text, HELD)
    assert [(n, h) for n, h, _ in hits] == [(7, "60 fps"), (12, "LoRA/PEFT"), (15, "forked by two teammates"),
                                            (19, "60 fps")]


def test_blocks_split_on_items_rows_headings_and_blank_lines():
    text = "# T\npara one\npara two\n- item\n  cont\n| a | b |\n\nlast\n"
    assert blocks(text) == [(1, ["# T"]), (2, ["para one", "para two"]), (4, ["- item", "  cont"]),
                            (6, ["| a | b |"]), (8, ["last"])]


# ----------------------------------------------------------------------------- the real research files
@pytest.mark.real_records       # the owner's list: its size and two of its strings are asserted
def test_the_holds_list_is_read():
    held = held_strings()
    count = len(held)                         # never `len(held)` in the assert: pytest would print the real list
    assert count > 50, f"{count} held strings"                                             # missing is not empty
    missing = [s for s in ("forked by two teammates", "QLoRA") if s not in held]
    assert not missing, missing


def test_the_holds_list_is_not_empty():
    held = held_strings()
    count = len(held)                         # missing is not empty: a data folder with no holds list reads as 0
    assert count > 0, f"{count} held strings"


def test_research_files_name_held_text_only_on_lines_marked_held():
    held = held_strings()
    report = []
    for rel in RESEARCH:
        path = JS / rel
        assert path.is_file(), f"{rel} missing"
        report += [f"{rel}:{n}: held {hs.shown(h)}: {hs.scrub(line)[:120]}"
                   for n, h, line in unmarked(path.read_text(encoding="utf-8"), held)]
    ok = not report
    assert ok, "held text used as evidence (mark the line held, or remove it):\n" + "\n".join(report)


if __name__ == "__main__":
    held = held_strings()
    bad = 0
    for rel in RESEARCH:
        text = (JS / rel).read_text(encoding="utf-8")
        hits = unmarked(text, held)
        marked = sum(1 for _, block in blocks(text) if MARK.search("\n".join(block))
                     and any(h in "\n".join(block) for h in held))
        bad += len(hits)
        print(f"{rel}: {len(hits)} unmarked held string(s); {marked} block(s) name held text and are marked held")
        for n, h, line in hits:
            print(f"  {rel}:{n}: {hs.shown(h)}: {hs.scrub(line)[:120]}")
    print(f"research held check: {'clean' if not bad else f'{bad} finding(s)'} ({len(held)} held strings from "
          "profile/PROFILE-HOLDS.md via profile_titles.load_held)")
    sys.exit(1 if bad else 0)
