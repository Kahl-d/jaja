"""prospects.py: sponsorship is a weighed risk, never a gate. A plain "no
sponsorship" lowers the fit by a weight and flags it; only citizenship, a security clearance, or an employer refusing
E-Verify or the I-983 fails (fit 1.0, P3), as in fit_gates and hunt_score. A manual fit clears the stale sponsorship
line; a hard work-authorization line stays visible."""
from __future__ import annotations

from datetime import date

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import prospects as pr

TODAY = date(2026, 10, 1)
TARGETS = {"high": ["ai", "agent"], "mid": ["software"], "low": ["sales"]}


def founder(**kw):
    meta = {"role": "founder", "role_hint": "AI Engineer, Agents", "company_type": "startup", "title": "CTO",
            "last_inbound_at": "2026-09-29T10:00:00Z", "personalization": 2.0, "label": "outreach",
            "sponsorship_hint": "unknown", "sponsorship_evidence": None, "status": "new"}
    meta.update(kw)
    return meta


def test_no_sponsorship_with_a_strong_fit_is_weighed_and_stays_in_the_reply_batch():
    s = pr.score_prospect(founder(sponsorship_hint="no",
                                  sponsorship_evidence="We are unable to sponsor visas for this role."), TODAY, TARGETS)
    assert s["fit_score"] == 4.0                                   # 4.5 less the 0.5 weight, never 1.0
    assert s["priority"] in ("P0", "P1")                           # select() takes P0 and P1: it stays queued
    assert any(r.startswith("sponsorship: no") and "weighed risk" in r for r in s["score_reasons"])
    assert "sponsorship: no (gate)" not in s["score_reasons"]
    assert any(r == "top target role" for r in s["score_reasons"])      # the role still scores


def test_citizenship_clearance_and_e_verify_refusal_still_fail():
    for ev in ("Applicants must be a U.S. citizen.", "Active TS/SCI security clearance required.",
               "We do not participate in E-Verify.", "The company will not sign the I-983 training plan."):
        s = pr.score_prospect(founder(sponsorship_hint="no", sponsorship_evidence=ev), TODAY, TARGETS)
        assert s["fit_score"] == 1.0 and s["priority"] == "P3", ev
        assert any(r.startswith("work authorization:") and "hard gate" in r for r in s["score_reasons"]), ev


def test_a_manual_fit_clears_the_stale_sponsorship_line_but_keeps_a_hard_one():
    soft = founder(sponsorship_hint="no", sponsorship_evidence="No visa sponsorship.", fit_score=4.5,
                   locked_fields=["fit_score"], locked_on={"fit_score": "2026-10-01"})
    reasons = pr.score_prospect(soft, TODAY, TARGETS)["score_reasons"]
    assert not any(r.startswith("sponsorship: no") for r in reasons)
    assert "fit_score (set manually 2026-10-01: 4.5)" in reasons
    hard = founder(sponsorship_hint="no", sponsorship_evidence="U.S. citizens only.", fit_score=4.5,
                   locked_fields=["fit_score"], locked_on={"fit_score": "2026-10-01"})
    reasons = pr.score_prospect(hard, TODAY, TARGETS)["score_reasons"]
    assert any(r.startswith("work authorization:") for r in reasons)


def test_unknown_and_yes_are_unchanged():
    assert pr.score_prospect(founder(), TODAY, TARGETS)["fit_score"] == 4.5
    assert pr.score_prospect(founder(sponsorship_hint="yes"), TODAY, TARGETS)["fit_score"] == 4.5
