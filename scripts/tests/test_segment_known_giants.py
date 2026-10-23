"""The KNOWN table (policy.segments.known_names) admits only companies whose size is a public fact (a public company,
a public company's subsidiary, or a well-known large private one), each with a one-line reason; every other company
keeps an honest "not set" (no tier fallback). The table is the synthetic persona's (persona.d/09-segments.yaml)."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import hunt_score as hs  # noqa: E402
import segment_companies as sc  # noqa: E402


def test_every_known_entry_has_a_segment_and_a_reason():
    assert set(sc.KNOWN) == set(sc.KNOWN_REASONS)
    for name, (segment, reason) in sc.KNOWN_REASONS.items():
        assert segment in ("big-tech", "ai-lab", "enterprise") and len(reason) >= 15 and "\u2014" not in reason, name


@pytest.mark.parametrize("name,segment", [("Bigsearch", "big-tech"), ("BigSearch Cloud", "big-tech"),
                                          ("Northwind AI", "ai-lab"), ("Roadrunner", "big-tech")])
def test_the_giants_resolve_with_their_reason(tmp_path, name, segment):
    seg, src = sc.Evidence(tmp_path).decide(name, "")
    assert seg == segment and src.startswith(sc.KNOWN_SOURCE) and sc.KNOWN_REASONS[sc._key(name)][1] in src
    assert hs.clock_segment({"name": name, "segment": seg})[0] == "giant"


@pytest.mark.parametrize("name", ["Acme Labs", "Initech", "Contoso", "Fabrikam", "Globex Systems", "Hooli"])
def test_startups_and_unsure_names_stay_not_set(tmp_path, name):
    assert sc.Evidence(tmp_path).decide(name, "") == (None, None)
