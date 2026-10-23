"""The layout gate is run by the procedures that make and review pages, and every reader looks at the page.

Text gates read words; a page can still look ill-arranged or ill-spaced. So the procedures that render or hand in a
page run scripts/page_layout_check.py, and the skills and role cards of the readers require Reading the rendered
page-1.png with a falsifiable `layout:` verdict line that blocks APPROVE on FAIL.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from layout import JS  # noqa: E402
pytestmark = pytest.mark.real_records     # reads the data folder's skills and role cards
RUN = r"python3 (?:-B )?scripts/page_layout_check\.py"

RUNNERS = ["skills/build-resume-pdf/SKILL.md", "skills/tailor-resume/SKILL.md",
           "skills/page-review/SKILL.md", "skills/editorial-review/SKILL.md"]
READERS = ["skills/page-review/SKILL.md", "skills/editorial-review/SKILL.md",
           "roles/technical-recruiter-reviewer.md", "roles/hiring-manager-persona.md", "roles/resume-editor.md"]
SKILLS = [p for p in RUNNERS]


def read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("procedure", RUNNERS, ids=[Path(p).parent.name for p in RUNNERS])
def test_the_procedure_runs_the_layout_gate(procedure):
    assert re.search(RUN, read(procedure)), f"{procedure} never runs scripts/page_layout_check.py"
    assert (JS / "scripts" / "page_layout_check.py").is_file()


@pytest.mark.parametrize("reader", READERS, ids=[Path(p).stem if p.startswith("roles") else Path(p).parent.name
                                                 for p in READERS])
def test_every_reader_reads_the_page_image_and_signs_a_layout_line(reader):
    text = read(reader)
    if reader == "roles/resume-editor.md":            # the card binds its reference as if inline
        assert "role-references/resume-editor-layout-pass.md" in text
        text += read("role-references/resume-editor-layout-pass.md")
    assert "page-1.png" in text, f"{reader} never Reads the rendered page"
    assert re.search(r"`layout: PASS", text) and re.search(r"`layout: FAIL", text), f"{reader}: no layout verdict line"
    assert re.search(r"layout FAIL[^.\n]*(never|blocks|not)[^.\n]*(APPROVE|approved|Call)", text, re.I), \
        f"{reader}: a layout FAIL does not block the approval"
    for word in ("balance", "whitespace", "clutter"):
        assert word in text.lower(), f"{reader}: never judges {word}"


def test_page_review_carries_the_page_design_section():
    text = read("skills/page-review/SKILL.md")
    m = re.search(r"^## Page design.*?(?=^## |\Z)", text, re.M | re.S)
    assert m, "page-review has no '## Page design' section"
    body = m.group(0).lower()
    for need in ("scan", "bottom", "header", "margin", "one page"):
        assert need in body, f"Page design never names {need}"


@pytest.mark.parametrize("skill", SKILLS, ids=[Path(p).parent.name for p in SKILLS])
def test_skill_descriptions_stay_under_300_chars(skill):
    front = read(skill).split("---")[1]
    desc = yaml.safe_load(front)["description"]
    assert len(desc) <= 300, f"{skill}: description {len(desc)} chars"


def test_the_gate_version_line_names_the_layout_check():
    import sys
    sys.path.insert(0, str(JS / "scripts"))
    import gate_versions as gv
    assert "page_layout_check" in gv.GATES
