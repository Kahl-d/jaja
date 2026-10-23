"""hunt_score reads a company's size from ONE source, companies.yaml `segment` (set by segment_companies.py set),
never from its `tier`: a job record once said "segment giant (dream tier)" and "stage: big company" while
segment_companies.segment_for gave the company no segment. Tier is how much the owner wants a company, not how big it is."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import hunt_score as hs  # noqa: E402

DREAMCO = {"name": "Dreamco", "slug": "dreamco", "tier": "dream", "ats_vendor": "greenhouse", "h1b_approvals_recent": None}


def test_a_dream_company_with_no_segment_is_not_a_giant():
    seg, why = hs.clock_segment(DREAMCO)
    assert seg == "mid" and "not set" in why and "dream" not in why


@pytest.mark.parametrize("segment,expected", [("startup", "startup"), ("ai-lab", "giant"), ("big-tech", "giant"),
                                              ("enterprise", "giant")])
def test_the_segment_decides_whatever_the_tier(segment, expected):
    assert hs.clock_segment({**DREAMCO, "segment": segment})[0] == expected
    assert hs.clock_segment({**DREAMCO, "tier": "good", "segment": segment})[0] == expected


def test_stage_big_company_needs_a_big_segment_or_h1b_volume_not_a_dream_tier():
    neutral = "We build agents."
    assert hs._stage("AI Engineer", neutral, "dreamco", DREAMCO, {})[1] != "stage: big company"
    assert hs._stage("AI Engineer", neutral, "dreamco", {**DREAMCO, "segment": "ai-lab"}, {})[1] == "stage: big company"
    big_filer = {**DREAMCO, "tier": "good", "h1b_approvals_recent": hs.BIG_H1B}
    assert hs._stage("AI Engineer", neutral, "acme", big_filer, {})[1] == "stage: big company"


def test_segment_for_answers_from_the_watchlist_entry_first(tmp_path):
    import segment_companies as sc
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Tinyco, slug: tinyco, tier: good, notes: \"added by yc_boards.py\", segment: startup}\n"
        "  - {name: Dreamco, slug: dreamco, tier: dream}\n"
        "  - {name: Quirk Works, slug: quirk, tier: good, notes: \"YC W26\"}\n", encoding="utf-8")
    assert sc.segment_for(tmp_path, "Tinyco", "tinyco")[0] == "startup"           # its own segment, the one source
    assert sc.segment_for(tmp_path, "Quirk Works", "quirk")[0] == "startup"      # the entry's YC note is evidence
    assert sc.segment_for(tmp_path, "Dreamco", "dreamco") == (None, None)            # nothing set, nothing known
    for name, slug in (("Tinyco", "tinyco"), ("Dreamco", "dreamco")):                  # hunt_score agrees with it
        entry = {"name": name, "slug": slug, "segment": sc.segment_for(tmp_path, name, slug)[0]}
        assert hs.clock_segment(entry)[0] == {"startup": "startup", None: "mid"}[entry["segment"]]


def test_growth_reads_as_mid_by_name():
    """A growth company (private, late-stage, mid-sized) takes the mid clock, named as growth."""
    seg, why = hs.clock_segment({**DREAMCO, "segment": "growth"})
    assert seg == "mid" and why == "segment growth (mid)"
