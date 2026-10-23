"""A senior title with no stated years is not an open door: a "Senior", "Sr." or "Lead" title whose JD states no years
implies a years ask (Senior or Lead about 5, Staff or Principal 8), so the role is not offered as a cold application
above the candidate's engineer months."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fresh10  # noqa: E402


@pytest.mark.parametrize("title, years", [
    ("Senior Machine Learning Engineer", 5), ("Sr. ML Engineer, Speech LLM Evaluation", 5), ("Sr Applied Scientist", 5),
    ("Founding Engineer - Tech Lead", 5), ("Staff Software Engineer", 8), ("Principal AI Engineer", 8),
    ("Member of Technical Staff, Evals", None), ("Software Engineer, Applied AI", None), ("Forward Deployed Engineer", None),
    ("Software Engineer and Senior Software Engineer", None), ("Senior Analyst", None), ("Sr. Associate, AI", None),
    ("Senior AI/ML Engineer", 5), ("Sr. AI/ML Platform Engineer", 5), ("Senior Software Engineer (Go/Rust)", 5),
    ("Staff Engineer, Data and ML", 8), ("Senior Engineer, Products and Brands", 5), ("Software Engineer II/Senior", None),
    ("Senior Applied Scientist / Engineer", 5), ("Senior Machine Learning Engineer I", 5),
    ("Senior Data Engineer / Data Architect", 5), ("Senior Account Engineer, Mid-Market", 5),
    ("Mid or Senior Software Engineer", None),
])
def test_a_title_implies_years_only_when_it_names_a_level(title, years):
    assert fresh10.implied_years(title, None) == (years, years is not None)


def test_a_stated_ask_always_wins_over_the_title():
    assert fresh10.implied_years("Senior AI Engineer", 2) == (2, False)


def test_an_implied_senior_ask_is_out_cold_and_a_stretch_through_a_person():
    years, _ = fresh10.implied_years("Senior AI Engineer", None)
    assert fresh10.years_rule(years, False) == "OUT" and fresh10.years_rule(years, True) == "STRETCH"


def test_a_jd_that_says_early_career_implies_nothing():
    assert fresh10.implied_years("Senior Applied Scientist", None, "We hire early-career scientists ...") == (None, False)
    assert fresh10.implied_years("Senior Applied Scientist", None, "Five years building ML systems.") == (5, True)
