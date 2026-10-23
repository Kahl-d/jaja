"""Page gates (charter): rubric_check B22 (no base bullet dropped), B23 (a focused page aligns
a bullet, not only Skills), B24 (the package's lead and order applied or why not), and the cards that use them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
from layout import JS  # noqa: E402
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import rubric_check as rc  # noqa: E402
import test_rubric_check as trc  # noqa: E402
from test_rubric_check import B1, B2, B3, matrix, resume  # noqa: E402

files = trc.files   # the pytest fixture

NEW = "Added per-user memory to the agent so each user's sessions resume. <!-- ev:ev-c-001 -->"
REWORD = "Made agent quality measurable: an LLM judge scored every run against an RF rubric. <!-- ev:ev-a-003 -->"


def pred(rep, pid):
    return next((p for p in rep["predicates"] if p["id"] == pid), None)


def run(paths, tier=None):
    return rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier=tier)


def notes(paths, text):
    paths["resume"].with_name("NOTES.md").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- B22
def test_b22_fails_a_base_bullet_whose_text_and_ids_are_gone(files):
    rep = run(files(resume(bullets_a=(B1, NEW, B3))))
    p = pred(rep, "B22")
    assert p["status"] == "FAIL" and "B22" in rep["failed"]
    assert len(p["findings"]) == 1 and p["findings"][0]["line"].startswith("Made agent quality measurable")
    assert "Acme / Founding Engineer #2" in p["findings"][0]["detail"]


def test_b22_passes_a_reworded_bullet_that_keeps_its_ev_ids(files):
    rep = run(files(resume(bullets_a=(REWORD, B1, B3))))
    assert pred(rep, "B22")["status"] == "PASS"


def test_b22_passes_the_base_and_an_added_bullet(files):
    assert pred(run(files(resume())), "B22")["status"] == "PASS"
    assert pred(run(files(resume(bullets_a=(B1, B2, B3, NEW)))), "B22")["status"] == "PASS"


def test_b22_runs_on_every_tier(files):
    paths = files(resume(bullets_a=(B1, B3)))
    for tier in (None, "mass", "focused", "dream"):
        assert pred(run(paths, tier), "B22")["status"] == "FAIL"


# --------------------------------------------------------------------------- B23
def test_b23_fails_a_skills_only_diff_on_a_focused_page(files):
    skills = [{"label": "Agent systems", "details": "agent harness, LLM judge, React, LangGraph <!-- ev:ev-a-001 -->"}]
    rep = run(files(resume(skills=skills)), "focused")
    p = pred(rep, "B23")
    assert p["status"] == "FAIL" and "Skills-only" in p["findings"][0]["detail"] and "B23" in rep["failed"]


def test_b23_passes_a_focused_page_with_an_aligned_bullet(files):
    assert pred(run(files(resume(bullets_a=(REWORD, B1, B3))), "dream"), "B23")["status"] == "PASS"


def test_b23_is_a_warning_with_the_owners_words(files):
    mat = {**matrix(), "skills_only_ok": "owner 2030-01-15: 'Skills only for this one'"}
    p = pred(run(files(resume(), mat=mat), "focused"), "B23")
    assert p["status"] == "WARN" and "owner 2030-01-15" in p["notes"][0]


def test_b23_and_b24_are_absent_for_mass_and_no_tier(files):
    for tier in ("mass", None):
        rep = run(files(resume()), tier)
        assert pred(rep, "B23") is None and pred(rep, "B24") is None


# --------------------------------------------------------------------------- B24
def test_b24_fails_without_notes_or_without_the_section(files):
    paths = files(resume(bullets_a=(REWORD, B1, B3)))
    p = pred(run(paths, "focused"), "B24")
    assert p["status"] == "FAIL" and "no NOTES.md" in p["findings"][0]["detail"]
    notes(paths, "## Target\nAcme\n")
    assert "no `## Lead and order`" in pred(run(paths, "focused"), "B24")["findings"][0]["detail"]


def test_b24_passes_applied_or_not_applied_with_a_reason(files):
    paths = files(resume(bullets_a=(REWORD, B1, B3)))
    notes(paths, "## Lead and order\n- applied: lead = the eval bench (R2, rank 3); order kept\n\n## Handoff\nx\n")
    assert pred(run(paths, "focused"), "B24")["status"] == "PASS"
    notes(paths, "## Lead and order\nnot applied: the package's lead needs a fact the owner has not confirmed\n")
    assert pred(run(paths, "dream"), "B24")["status"] == "PASS"


def test_b24_fails_an_empty_line_or_a_line_in_another_section(files):
    paths = files(resume(bullets_a=(REWORD, B1, B3)))
    notes(paths, "## Lead and order\nnot applied:\n\n## Plan\napplied: something\n")
    p = pred(run(paths, "focused"), "B24")
    assert p["status"] == "FAIL" and "applied: <the lead bullet" in p["findings"][0]["detail"]


# --------------------------------------------------------------------------- cards
@pytest.mark.real_records
def test_cards_carry_the_gates():
    rec = (JS / "roles" / "technical-recruiter-reviewer.md").read_text(encoding="utf-8")
    assert "only Skills" in rec and "B23" in rec and "B22" in rec and "REVISE" in rec
    tailor = (JS / "roles" / "resume-tailor.md").read_text(encoding="utf-8")
    assert "## Lead and order" in tailor and "not applied: <why>" in tailor and "B24" in tailor


def test_names_cover_the_new_predicates():
    assert {"B22", "B23", "B24"} <= set(rc.NAMES)
