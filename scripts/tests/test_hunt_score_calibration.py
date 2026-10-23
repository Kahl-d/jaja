"""hunt_score role fit, recalibrated against fit-scorer outcomes: the triage once ranked research, infra and sandbox
roles high while the fit-scorer put them all at 3.0-3.4 (skip). Research, scientist, infra and sandbox titles score down
unless the title or the JD names the configured core (agents, evals, applied AI, forward deployed).
Fixture: scripts/tests/fixtures/hunt_fit_calibration.json (synthetic records)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import hunt_score as hs  # noqa: E402

CAL = json.loads((Path(__file__).parent / "fixtures" / "hunt_fit_calibration.json").read_text(encoding="utf-8"))
PLAIN_JD = "You will build distributed training systems and run large experiments at scale. Python, C++, CUDA."
CORE_JD = ("You will build agents with tool use, own the eval harness and evals, and deploy applied AI with customers. "
           "Python, LLM.")


@pytest.mark.parametrize("case", [c for c in CAL["skip"] if hs.RESEARCH_INFRA.search(c["title"])],
                         ids=lambda c: c["title"])
def test_the_research_and_scientist_skips_score_down(case):
    s, why = hs.role_fit(case["title"], case["jd"])
    assert s <= 0.5, (case["title"], why)


@pytest.mark.parametrize("case", CAL["kept"], ids=lambda c: c["title"])
def test_the_kept_roles_keep_their_role_fit(case):
    assert hs.role_fit(case["title"], case["jd"])[0] >= 0.9


@pytest.mark.parametrize("title", ["Research Engineer, Agents", "Research Engineer, Evals", "Applied AI Engineer, Research",
                                   "Forward Deployed Engineer, Infrastructure", "Founding AI Engineer, Research"])
def test_a_core_word_in_the_title_keeps_full_marks(title):
    assert hs.role_fit(title, PLAIN_JD)[0] >= 0.8


@pytest.mark.parametrize("title", ["Research Engineer, Content Understanding",
                                   "Member of Technical Staff - Shared Services", "AI Infrastructure Engineer",
                                   "Research Engineer / Performance Engineer, RL Distributed Systems"])
def test_research_or_infra_titles_without_core_drop_to_half(title):
    s, why = hs.role_fit(title, PLAIN_JD)
    assert s <= 0.5 and "research/infra" in why, (title, s, why)


def test_a_research_scientist_title_stays_low():
    """Not an AI title before the change either (0.15, outside the target families): still at or under the cap."""
    assert hs.role_fit("Research Scientist, Pre-training", PLAIN_JD)[0] <= 0.5


def test_a_sandboxing_title_with_an_ai_jd_but_no_core_drops_to_half():
    """A past miss: 'Software Engineer, Sandboxing' read 0.9 as a generic title with AI work in the JD."""
    ai_jd = "Build secure sandboxes for LLM code execution: machine learning, transformer serving, PyTorch, prompt isolation."
    assert hs.role_fit("Software Engineer, Sandboxing", ai_jd)[0] <= 0.5


def test_the_jd_can_bring_a_research_title_back_to_0_8():
    s, why = hs.role_fit("Research Engineer, Content Understanding", CORE_JD)
    assert s == 0.8 and "core" in why


def test_off_role_and_plain_ai_titles_are_unchanged():
    assert hs.role_fit("Site Reliability Engineer", CORE_JD)[0] == 0.0
    assert hs.role_fit("Machine Learning Engineer, Video Ads CX", PLAIN_JD)[0] == 1.0
    assert hs.role_fit("AI Engineer 3", PLAIN_JD)[0] == 1.0


# "AI/ML Engineer" is an AI engineer title, so a research word beside it no longer caps.
@pytest.mark.parametrize("title", ["Example AI Research Lab AI/ML Engineer (100 % remote) (m/f/d)", "AI / ML Engineer, Research",
                                   "ML/AI Engineer, Infrastructure"])
def test_an_ai_ml_engineer_title_counts_as_core(title):
    assert hs.TITLE_CORE.search(title)
    assert hs.role_fit(title, PLAIN_JD)[0] >= 0.8


@pytest.mark.parametrize("title", ["ML Research Engineer", "Research Scientist, AI", "AI Infrastructure Engineer"])
def test_the_widening_does_not_reach_plain_research_or_infra_titles(title):
    assert not hs.TITLE_CORE.search(title)
    assert hs.role_fit(title, PLAIN_JD)[0] <= 0.5
