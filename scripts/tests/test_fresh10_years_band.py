"""The years band: a role asking up to the cold band (policy.fit_gates.cold_max_years) goes cold; more goes only through a
person (a stretch); the reject bar (policy.fit_gates.reject_years) or more stays the fit gate. fresh10.years_rule is the
one cold band; next_up.py and posts.py read it."""
from __future__ import annotations

import json
from pathlib import Path

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import fresh10
import posts

FIX = Path(__file__).resolve().parent / "fixtures" / "posts" / "x-2026-10-05.jsonl"


def test_three_years_is_cold_four_or_more_only_through_a_person():
    assert fresh10.COLD_MAX_YEARS == 3
    assert [fresh10.years_rule(y) for y in (None, 1, 2, 3, 4, 5, 7, 8)] == \
        [None, None, None, None, "OUT", "OUT", "OUT", "OUT"]
    assert [fresh10.years_rule(y, warm=True) for y in (3, 4, 5, 7, 8)] == [None, "STRETCH", "STRETCH", "STRETCH", "OUT"]


def test_a_founders_post_asking_3_years_is_in_band_and_5_is_a_stretch():
    founder = next(json.loads(line) for line in FIX.read_text(encoding="utf-8").splitlines()
                   if '"mira_builds"' in line)                           # the run's first-person founder post
    three = posts.structure(posts.normalize_row(dict(founder, text=founder["text"] + " 3+ years of experience."), "x"))
    five = posts.structure(posts.normalize_row(dict(founder, text=founder["text"] + " 5+ years of experience."), "x"))
    assert (three["years"], three["years_call"], three["in_band"]) == (3, "in band", True)
    assert five["years"] == 5 and five["years_call"].startswith("stretch") and five["in_band"]
