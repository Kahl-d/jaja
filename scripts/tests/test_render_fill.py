"""Fill-to-page: a one-page resume whose bottom is empty while the body is packed looks wrong.
The renderer now fills a one-page resume: it measures the gap under the last text line from the PDF and
spreads the leftover height over the vertical spacing only (space above section titles, between entries, between
bullets, line spacing), within bounds, so the last line sits near the bottom margin and the page stays one page.
Widths never change, so no line rewraps and the ATS text is the same.

Unit tests use pure functions and a fake measure; the acceptance render uses the FICTIONAL Riley Moreno fixture.
"""
from __future__ import annotations

import copy
import shutil

import pytest
import yaml

from test_render_resume import EVIDENCE, GOOD, needs_rendercv, rr  # noqa: F401

needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler pdftotext not installed")


def _classic() -> dict:
    path = rr.resolve_template("classic")
    return rr.apply_density(rr.load_template(path), "one-page")


def test_fill_overlay_at_zero_keeps_line_spacing_and_section_gaps_at_least_the_template():
    tpl = _classic()
    out = rr.fill_overlay(tpl, 0.0)
    for path in rr.FILL_KEYS:
        assert rr._to_points(rr._get(out, *path)) >= rr._to_points(rr._get(tpl, *path)) - 1e-6 or path == (
            "design", "sections", "space_between_text_based_entries")


def test_fill_overlay_grows_every_vertical_key_monotonically_and_stays_bounded():
    tpl = _classic()
    prev = rr.fill_overlay(tpl, 0.0)
    for t in (0.25, 0.5, 1.0):
        cur = rr.fill_overlay(tpl, t)
        for path in rr.FILL_KEYS:
            assert rr._to_points(rr._get(cur, *path)) >= rr._to_points(rr._get(prev, *path)) - 1e-6
            assert rr._to_points(rr._get(cur, *path)) <= rr._to_points(rr.FILL_MAX[path]) + 1e-6
        prev = cur


def test_fill_overlay_never_touches_widths_margins_or_font_size():
    tpl = _classic()
    out = rr.fill_overlay(tpl, 1.0)
    for path in (("design", "page", "left_margin"), ("design", "page", "right_margin"),
                 ("design", "page", "top_margin"), ("design", "page", "bottom_margin"),
                 ("design", "typography", "font_size", "body")):
        assert rr._get(out, *path) == rr._get(tpl, *path)
    assert rr.template_problems(out) == []
    assert tpl == _classic()  # the input is not mutated


def test_fill_search_picks_the_largest_one_page_setting_under_the_target_gap():
    # fake page: the gap shrinks as t grows; past t=0.6 the page spills to two
    def measure(t):
        return (1, 0.12 - 0.2 * t) if t <= 0.6 else (2, 0.0)
    t, (pages, gap) = rr.fill_search(measure, target=0.03, t_max=1.5, steps=10)
    assert pages == 1
    assert gap <= 0.03
    assert 0.44 <= t <= 0.6


def test_fill_search_keeps_zero_when_the_page_is_already_full():
    t, (pages, gap) = rr.fill_search(lambda t: (1, 0.005) if t == 0 else (2, 0.0), target=0.03, t_max=1.5)
    assert t == 0 and pages == 1


@needs_rendercv
@needs_pdftotext
def test_a_short_one_page_resume_is_filled_to_the_bottom(tmp_path):
    data = yaml.safe_load(GOOD.read_text(encoding="utf-8"))
    short = copy.deepcopy(data)
    src = tmp_path / "short.yaml"
    src.write_text(yaml.safe_dump(short, sort_keys=False, allow_unicode=True), encoding="utf-8")
    plain = rr.render(src, tmp_path / "plain", evidence_path=EVIDENCE, template_spec="classic", density="one-page",
                      fill=False, log=lambda *_: None)
    filled = rr.render(src, tmp_path / "filled", evidence_path=EVIDENCE, template_spec="classic",
                       density="one-page", fill=True, log=lambda *_: None)
    assert plain.ok and filled.ok and filled.pages == 1
    gap_plain = rr.bottom_gap_fraction(plain.pdf)
    gap_filled = rr.bottom_gap_fraction(filled.pdf)
    assert gap_filled < gap_plain
    assert gap_filled <= 0.03 or filled.fill_t >= rr.FILL_T_MAX - 1e-6  # bounded: never stretch past FILL_MAX
    assert rr.pdf_text(plain.pdf).split() == rr.pdf_text(filled.pdf).split()  # same words, same order
