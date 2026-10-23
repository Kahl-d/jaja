"""Location tie-break: hunt_score's location part is a tie-break by place on a gate pass (the owner prefers Bay Area
first, California next, then other roles, but nothing is closed): Bay Area or remote in the US 1.0,
the rest of California 0.9, the rest of the US 0.8; a gate review stays 0.6."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import hunt_score as hs  # noqa: E402


@pytest.mark.parametrize("loc,remote,part", [
    ("San Francisco, CA", None, 1.0), ("Mountain View, California", None, 1.0), ("Remote - US", "remote", 1.0),
    ("United States", "remote", 1.0), ("Los Angeles, CA", None, 0.9), ("San Diego, California", None, 0.9),
    ("Irvine, CA", None, 0.9), ("Austin, TX", None, 0.8), ("New York, NY", None, 0.8), ("United States", None, 0.8),
    ("New York, NY | San Jose, CA", None, 1.0), ("Seattle, WA; Los Angeles, CA", None, 0.9)])
def test_a_gate_pass_scores_by_place(loc, remote, part):
    assert hs.location_part({"gate": "pass", "location": loc, "remote": remote})[0] == part


def test_a_gate_review_stays_under_every_pass():
    value, why = hs.location_part({"gate": "review", "location": "San Francisco, CA"})
    assert value == 0.6 < min(hs.LOCATION_PART.values()) and "review" in why


def test_the_score_orders_bay_then_california_then_the_rest_of_the_us():
    def total(loc):
        rec = {"gate": "pass", "location": loc, "title": "AI Engineer", "company_slug": "x", "_jd_text": ""}
        return hs.location_part(rec)[0] * hs.DEFAULTS["weights"]["location"]
    assert total("San Jose, CA") > total("Los Angeles, CA") > total("Austin, TX")
