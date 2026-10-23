"""W4 fix 6: effort.py, the one-line effort level of a role (charter §7: effort follows importance)."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import effort as ef  # noqa: E402


@pytest.mark.parametrize("kw,level", [
    (dict(fit=4.6, tier="dream"), "E5"), (dict(fit=3.2, tier="dream", warm=True), "E5"),
    (dict(fit=3.5, tier="dream"), "E4"), (dict(fit=3.0, warm=True), "E4"), (dict(fit=4.5), "E4"),
    (dict(fit=4.2), "E3"), (dict(fit=4.0), "E3"), (dict(fit=3.6, big=True), "E3"), (dict(fit=3.6, heavy_alignment=True), "E3"),
    (dict(fit=3.9), "E2"), (dict(fit=None), "E2"), (dict(fit=4.8, gate="fail"), "E1")])
def test_the_rule_top_down(kw, level):
    fit = kw.pop("fit")
    got, why = ef.effort(fit, **kw)
    assert got == level and why


def test_effort_for_reads_the_record_and_the_company():
    rec = {"fit_score": "3.7", "gate": "pass"}
    assert ef.effort_for(rec, {"tier": "good", "segment": "big-tech"})[0] == "E3"
    assert ef.effort_for(rec, {"tier": "good", "h1b_approvals_recent": 250})[0] == "E3"
    assert ef.effort_for(rec, {"tier": "good"})[0] == "E2"
    assert ef.effort_for(rec, {"tier": "dream"})[0] == "E4"


def test_the_big_company_rule_matches_hunt_score():
    import hunt_score as hs
    assert ef.BIG_SEGMENTS == hs.BIG_SEGMENTS and ef.BIG_H1B == hs.BIG_H1B


def test_cli_prints_one_line(capsys):
    assert ef.main(["--fit", "4.6", "--tier", "dream"]) == 0
    assert capsys.readouterr().out.startswith("E5 ")
