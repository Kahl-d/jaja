"""The department's gates are run by the procedures that need them.

A gate no card or skill runs is a gate nothing passes through: prep_focus.py (the first pack for any screen) can sit
unrun while a role reaches screen, and answers_check.py (the gate on profile/answers/) must be run by every procedure
that drafts from that bank. apply-assist never drafts form prose from the bank (its card's Gate: pack rows,
apply_pack answers rows or the owner's own words), so it must not name the bank at all: a new free-text source there
needs the card changed and the gate added first.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
pytestmark = pytest.mark.real_records     # reads the data folder's role cards, lane cards and skills
RUN = r"python3 (?:-B )?scripts/{gate}"

WIRING = [
    ("skills/prep-pack/SKILL.md", "prep_focus.py"),
    ("lanes/prep/AGENT.md", "prep_focus.py"),
    ("skills/letters/SKILL.md", "answers_check.py"),
    ("skills/network-daily/SKILL.md", "answers_check.py"),
    ("lanes/recruiter/AGENT.md", "answers_check.py"),
]
BANK_DRAWERS = [p for p, g in WIRING if g == "answers_check.py"]


def read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("procedure, gate", WIRING, ids=[f"{Path(p).parent.name}-{g}" for p, g in WIRING])
def test_the_procedure_runs_the_gate(procedure, gate):
    assert re.search(RUN.format(gate=re.escape(gate)), read(procedure)), f"{procedure} never runs scripts/{gate}"
    assert (JS / "scripts" / gate).is_file(), f"scripts/{gate} is gone; {procedure} would run nothing"


@pytest.mark.parametrize("procedure", BANK_DRAWERS)
def test_the_bank_is_gated_before_it_is_drawn_from_and_every_exit_is_handled(procedure):
    text = read(procedure)
    gate = re.search(RUN.format(gate=r"answers_check\.py") + r"[^\n`]*", text)
    assert gate, f"{procedure} never runs the gate"
    rest = text[:gate.start()] + " " * (gate.end() - gate.start()) + text[gate.end():]   # the gate's own argument
    bank = rest.find("profile/answers")
    assert bank == -1 or gate.start() < bank, f"{procedure} names profile/answers/ before it runs the gate"
    after = text[gate.end():gate.end() + 400]
    for code in ("Exit 0", "Exit 1", "Exit 2"):
        assert code in after, f"{procedure}: the gate's {code} is not handled right after it"


def test_apply_assist_does_not_draw_form_prose_from_the_bank():
    for rel in ("skills/apply-assist/SKILL.md", "roles/apply-assist.md"):
        assert "profile/answers" not in read(rel), f"{rel} names profile/answers/: add the gate and change the card first"


def test_every_procedure_that_names_the_bank_is_gated():
    """The next ungated reader fails here by name: any role card, lane card or skill that names profile/answers/
    must be listed in BANK_DRAWERS (and so pass the gate tests above)."""
    patterns = ("roles/*.md", "lanes/*/AGENT.md", "skills/*/SKILL.md")
    readers = sorted(str(f.relative_to(JS)) for pattern in patterns for f in JS.glob(pattern)
                     if "profile/answers" in f.read_text(encoding="utf-8"))
    assert readers, "no procedure names profile/answers/: the glob or the bank moved"
    assert set(readers) <= set(BANK_DRAWERS), f"ungated readers of the bank: {sorted(set(readers) - set(BANK_DRAWERS))}"
