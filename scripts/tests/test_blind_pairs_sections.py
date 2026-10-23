"""Sections and one-sided numbering. blind_pairs paired only Experience and Skills, so dropping a whole Projects section
(with the page's only award) produced no pair. Every other section's entries now pair (a changed entry blind, a dropped
or added one ONE-SIDED), and a removed or added section is one ONE-SIDED pair as a whole. One-sided pairs count with
letters (a, b, ...) so "ONE-SIDED Acme Labs 1" never shares its number with "LINE Acme Labs 1"."""
from __future__ import annotations

import json
import re

from test_blind_pairs import A1, A2, A3, bp, write  # noqa: F401

PROJ = {"name": "Example Project", "date": "2025", "summary": "Best Example Hack, Example Hacks 2025: a guardrail layer. <!-- ev:ev-ss -->"}
PUB = "**Thesis:** Example Study, M.S., Example University (2026). <!-- ev:ev-aw -->"


def page(lines=(A1, A2, A3), projects=(PROJ,), pubs=(PUB,), awards=None):
    sec = {"Experience": [{"company": "Acme Labs", "position": "Founding Engineer", "highlights": list(lines)}]}
    if projects is not None:
        sec["Projects"] = [dict(p) for p in projects]
    if pubs is not None:
        sec["Publications"] = list(pubs)
    if awards is not None:
        sec["Awards"] = list(awards)
    return {"cv": {"sections": sec}}


def _make(tmp_path, base, var, seed=3):
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(write(tmp_path, "var", var), write(tmp_path, "base", base), out, key, seed=seed)
    return out.read_text(encoding="utf-8"), json.loads(key.read_text(encoding="utf-8"))


def test_a_removed_section_is_one_one_sided_pair(tmp_path):
    text, key = _make(tmp_path, page(), page(projects=None))
    solo = [p for p in key["pairs"] if p.startswith("ONE-SIDED SECTION")]
    assert solo == ["ONE-SIDED SECTION Projects"] and key["kinds"][solo[0]] == "removed"
    sec = text.split(f"## {solo[0]}\n", 1)[1]
    assert "Example Project" in sec and "Best Example Hack" in sec and "(no section here)" in sec and "<!--" not in sec


def test_entries_of_other_sections_pair(tmp_path):
    changed = dict(PROJ, summary="Best Example Hack, Example Hacks 2025: a guardrail layer for GenAI apps. <!-- ev:ev-ss -->")
    _, key = _make(tmp_path, page(), page(projects=(changed,)))
    assert set(key["pairs"]["Projects 1"].values()) == {"base", "variant"}
    _, key = _make(tmp_path, page(), page(pubs=(PUB, "**Paper:** a second one. <!-- ev:ev-p2 -->")))
    solo = [p for p in key["pairs"] if p.startswith("ONE-SIDED Publications")]
    assert len(solo) == 1 and key["kinds"][solo[0]] == "added"
    _, key = _make(tmp_path, page(awards=["Best Example Hack, Example Hacks 2025"]), page())
    assert key["kinds"]["ONE-SIDED SECTION Awards"] == "removed"


def test_identical_sections_add_nothing(tmp_path):
    _, key = _make(tmp_path, page(), page())
    assert key["pairs"] == {}


def test_one_sided_ids_count_with_letters_not_the_line_numbers(tmp_path):
    new = "Shipped a pricing service. <!-- ev:ev-z -->"
    _, key = _make(tmp_path, page(), page(lines=(A1, "Built the sandboxed harness agents run in. <!-- ev:ev-b -->", new)))
    ids = list(key["pairs"])
    assert "LINE Acme Labs 1" in ids
    solo = [p for p in ids if p.startswith("ONE-SIDED Acme Labs")]
    assert solo and all(re.fullmatch(r"ONE-SIDED Acme Labs [a-z]", p) for p in solo)
