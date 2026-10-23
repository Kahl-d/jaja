"""Line estimate: lint once estimated a 390-character bullet at 3 lines (greedy wrap at 126 characters) but it printed
on 4: Source Sans 3 is proportional, so a line of wide glyphs holds fewer characters.
Lint now takes the larger of the character estimate and a glyph-width estimate (the font's real advance widths,
bold runs in bold, against the layout's line width), so it never under-counts the measured renders."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from test_render_resume import JS, rr  # noqa: F401

SAMPLES = yaml.safe_load((Path(__file__).parent / "fixtures" / "resume" / "line-samples.yaml").read_text())["samples"]
needs_fonts = pytest.mark.skipif(rr.glyph_widths("Source Sans 3") is None, reason="rendercv_fonts/fontTools missing")


def _template():
    return rr.load_checked_template("classic", "one-page")[1]                     # the variants' layout


@needs_fonts
def test_the_wide_glyph_case_is_four_lines():
    t = _template()
    case = SAMPLES[-1]
    assert rr.estimate_lines(case["text"], rr.estimate_chars_per_line(t)) == 3      # the old estimate
    assert rr.line_estimate(t)(case["text"]) == case["lines"] == 4


@needs_fonts
def test_lint_never_under_counts_the_measured_samples():
    model = rr.line_estimate(_template())
    for s in SAMPLES:
        assert model(s["text"]) >= s["lines"], (s["lines"], s["text"][:60])
        assert model(s["text"]) <= s["lines"] + 1                                   # and stays close


@needs_fonts
def test_lint_flags_the_case_as_too_long():
    t = _template()
    cv = {"cv": {"name": "A B", "email": "a@example.org", "sections": {"Experience": [
        {"company": "X", "position": "Y", "start_date": "2026-02", "end_date": "present",
         "highlights": [SAMPLES[-1]["text"]]}]}}}
    old = rr.lint_resume(cv, chars_per_line=rr.estimate_chars_per_line(t))
    new = rr.lint_resume(cv, chars_per_line=rr.estimate_chars_per_line(t), line_model=rr.line_estimate(t))
    assert [i for i in old.errors if i.code == "bullet-too-long" and "about 3 lines" in i.message]   # under-counted
    assert [i for i in new.errors if i.code == "bullet-too-long" and "about 4 lines" in i.message]


def test_without_the_fonts_the_character_estimate_still_runs(monkeypatch):
    monkeypatch.setattr(rr, "glyph_widths", lambda family: None)
    t = _template()
    assert rr.line_estimate(t)("short line") == 1


@needs_fonts
def test_a_link_with_bold_text_counts_only_its_visible_words():
    text = ("[**Best Robotics Hack**, Example University Emerging Technology track, Hack Week 2025]"
            "(https://example.org/software/secure-sense-a-safety-net-for-genai): a guardrail layer for GenAI apps.")
    assert rr.line_estimate(_template())(text) == 1
