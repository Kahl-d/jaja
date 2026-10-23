"""A marked years figure led by "with" counts as an ask: "..., with 8+ years in system software" was once read as no
years asked, so a stretch could look open through a person. Only a figure with a years marker counts."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fit_gates  # noqa: E402


@pytest.mark.parametrize("jd, years", [
    ("- Bachelor’s degree in Computer Science or related field, or equivalent experience, with 8+ years in relevant "
     "industry or research roles.", 8),
    ("- Bachelor's degree (or equivalent experience) with 12+ years in security research or software engineering.", 12),
    ("Equivalent practical experience with at least 5 years building and shipping production software.", 5),
    ("We are a company with 25+ years in business.", None),                 # over 20: never an ask
    ("Founded in 2001, the team has grown with 3 years of steady hiring.", None),   # unmarked: not an ask
])
def test_a_marked_figure_led_by_with_is_an_ask(jd, years):
    assert fit_gates.required_years(jd) == years
