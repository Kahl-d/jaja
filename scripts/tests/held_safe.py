"""Tests that read the real holds list without printing a private hold (I-025).

profile/private-holds.txt (gitignored) holds figures too private for PROFILE-HOLDS.md, and load_held on the real
list returns them with the rest. A failing assertion that shows a held list, a hit or a finding line would print
them into test output, so tests that read the real holds:
  - compare lists with same_without_values (counts, sha256 of the sorted lists, first differing index);
  - assert on a precomputed bool (`ok = not hits; assert ok, message`) so pytest has no list to show;
  - name a held string with shown() and a line of text with scrub(), which mask the private ones.
conftest.py scrubs every private hold from every test report as a backstop. It catches the exact, repr and JSON
forms only (a variant found by a plain or loose pass, or a value cut by pytest's truncation, is not caught), so the
explicit forms above come first.
"""
from __future__ import annotations

import functools
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import profile_titles as pt  # noqa: E402

MASK = "<private hold>"


@functools.lru_cache(maxsize=None)
def private_held() -> frozenset[str]:
    """The real private holds, read the way load_holds reads them; empty when the file is absent."""
    return frozenset(h.text for h in pt._private_holds(pt.HOLDS.parent / pt.PRIVATE_HOLDS_NAME))


@functools.lru_cache(maxsize=None)
def _forms() -> tuple[str, ...]:
    forms = {f for s in private_held() for f in (s, repr(s)[1:-1], json.dumps(s)[1:-1]) if f}
    return tuple(sorted(forms, key=len, reverse=True))


def scrub(text: object) -> str:
    """`text` with every private hold (exact, repr and JSON forms) replaced by MASK."""
    out = str(text)
    for form in _forms():
        out = out.replace(form, MASK)
    return out


def shown(held: str) -> str:
    """A held string as a test message may name it: repr for a PROFILE-HOLDS.md string, MASK for a private one."""
    return MASK if held in private_held() else repr(held)


def same_without_values(ours: Iterable[str], theirs: Iterable[str], names: tuple[str, str] = ("left", "right")) -> str:
    """'' when the two sequences are equal, else a message naming only their counts, the sha256 of each sorted
    list and the first differing index: never a held string."""
    a, b = list(ours), list(theirs)
    if a == b:
        return ""

    def digest(xs: list[str]) -> str:
        return hashlib.sha256("\n".join(sorted(xs)).encode("utf-8")).hexdigest()[:12]

    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return (f"{names[0]}: {len(a)} strings (sorted sha256 {digest(a)}); {names[1]}: {len(b)} strings "
            f"(sorted sha256 {digest(b)}); first difference at index {first}")
