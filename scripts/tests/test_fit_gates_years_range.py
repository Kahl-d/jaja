"""fit_gates.required_years on a years RANGE: the lower bound is the requirement, whatever the separator. A JD wrote
"2–10 years" with an en dash; the range part of _YEARS took only a hyphen, so the match began at "10" and the gate
failed the role as "asks 10+ years"."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fit_gates as fg  # noqa: E402

RANGE_JD = ("- 2–10 years of recent, demonstrated experience in one or more of: synthetic/LLM-generated data, "
             "post-training and model-evaluation work, privacy engineering, or data anonymization/de-identification "
             "at scale.")


@pytest.mark.parametrize("jd,years", [
    (RANGE_JD, 2),                                                          # en dash
    ("3\u20145 years of experience with LLMs.", 3),                          # em dash
    ("2 – 6 years of industry experience.", 2),                         # spaced en dash
    ("4 to 8 years of experience shipping ML systems.", 4),                  # "to"
    ("8-10 years of experience in machine learning.", 8),                    # hyphen, unchanged
    ("5+ years of experience building ML systems.", 5),                      # unchanged
])
def test_a_range_requires_its_lower_bound(jd, years):
    assert fg.required_years(jd) == years


def test_the_range_line_no_longer_fails_the_gate():
    out = fg.gate_seniority({"title": "Member of Technical Staff, AI Engineering"}, RANGE_JD, fg.default_config())
    assert not [f for f in out if f[0] == "fail"], out


def _rec(**kw):
    base = {"id": "hs-1", "company": "Example AI", "company_slug": "example-ai",
            "title": "Member of Technical Staff, AI Engineering", "location": "San Francisco, CA", "remote": "onsite",
            "employment_type": "full-time", "sponsorship": None, "sponsorship_evidence": None, "_jd_text": RANGE_JD}
    base.update(kw)
    return base


def test_a_job_the_gate_skipped_reopens_when_the_gate_no_longer_fails():
    rec = _rec(gate="fail", gate_reasons=["seniority: asks 10+ years of experience"], status="skipped",
               fit_verdict="skip")
    fg.apply_gates([rec], fg.default_config())
    assert rec["gate"] == "pass" and rec["status"] == "discovered" and rec["fit_verdict"] is None


def test_a_skip_the_gate_did_not_make_stays():
    rec = _rec(gate="pass", gate_reasons=[], status="skipped", fit_verdict="skip")      # the fit-scorer's skip
    fg.apply_gates([rec], fg.default_config())
    assert rec["status"] == "skipped" and rec["fit_verdict"] == "skip"
    applied = _rec(gate="fail", gate_reasons=["x"], status="applied", fit_verdict="apply")  # never touched
    fg.apply_gates([applied], fg.default_config())
    assert applied["status"] == "applied" and applied["fit_verdict"] == "apply"
