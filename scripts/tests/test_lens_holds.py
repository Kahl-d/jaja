"""Held text in the role lenses (profile/lenses/*.md): a held string may stand only in a block marked held or in a
"never appear" / traps list that quotes it on purpose (README, decided 2026-10-02). check_holds stays a page gate."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_research_holds as rh  # noqa: E402  (blocks, MARK, held_strings)

from layout import JS  # noqa: E402
NEVER = re.compile(r"never appear|not true of his work|never on (?:the|a) page|\btraps?\b", re.I)


def unmarked(text: str, held: list[str]) -> list[tuple[int, str]]:
    out = []
    for first, block in rh.blocks(text):
        joined = "\n".join(block)
        if rh.MARK.search(joined) or NEVER.search(joined):
            continue
        out += [(first + k, h) for k, line in enumerate(block) for h in held if h in line]
    return out


def test_a_held_string_outside_a_marked_or_never_block_is_found():
    held = ["60 fps", "Forge Studio"]
    text = ("## 2\n\nTerms that are NOT true of his work and never appear: 60 fps.\n\n"
            "- Product line: built the Forge Studio canvas.\n")
    assert unmarked(text, held) == [(5, "Forge Studio")]


def test_every_role_lens_names_held_text_only_where_marked():
    held = rh.held_strings()
    bad = {p.name: unmarked(p.read_text(encoding="utf-8"), held)
           for p in sorted((JS / "profile" / "lenses").glob("*.md"))}
    assert not {k: v for k, v in bad.items() if v}, bad
