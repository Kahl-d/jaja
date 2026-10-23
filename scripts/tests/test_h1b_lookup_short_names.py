"""h1b_lookup enrich. A startup "Zim" (San Francisco) matched FIVE
LAKES SERVICES LLC D B A ZIM in Rockville, MD, and "Aba" (Bay Area) matched ABA, INC. in St. Paul, MN, both through the
trusted-sector exception. The rules: (1) a match that rests only on a "D B A <name>" alias is never enough: unknown;
(2) a short name (four letters or fewer, or one dictionary word) needs a city or state match with the posting, whatever
the NAICS sector: else unknown, "short-name match outside the posting's location" (the domain clause is not used: the hub
files carry no domain);
(3) the watchlist's h1b_names still override."""
from __future__ import annotations

from datetime import date

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import h1b_lookup as hb  # noqa: E402
from test_h1b_lookup_sectors import _cache  # noqa: E402

TODAY = date(2026, 9, 28)
ZIM = ["2025,FIVE LAKES SERVICES LLC D B A ZIM,5555,ROCKVILLE,MD,20850,541512,4,0,4,0",
       "2024,FIVE LAKES SERVICES LLC D B A ZIM,5555,ROCKVILLE,MD,20850,541512,3,0,3,0"]
ABA = ["2025,\"ABA, INC.\",6666,ST. PAUL,MN,55101,541330,4,0,4,0", "2024,\"ABA, INC.\",6666,ST. PAUL,MN,55101,541330,3,0,3,0"]
WATCH_ZIM = {"slug": "zim", "name": "Zim", "tier": "dream", "sponsorship_stance": "sponsors"}


@pytest.fixture(autouse=True)
def _words(monkeypatch):
    monkeypatch.setattr(hb, "_dictionary", lambda: frozenset({"block", "scale", "orchard", "notion"}))


def _job(company, slug, location="San Francisco, CA", **kw):
    rec = {"id": f"{slug}-1", "company": company, "company_slug": slug, "title": "AI Engineer", "location": location,
           "sponsorship": None, "sponsorship_evidence": None}
    rec.update(kw)
    return rec


def test_a_dba_only_match_is_unknown_even_on_the_watchlist_and_in_a_trusted_sector(tmp_path):
    rec = _job("Zim", "zim", location="San Francisco")
    hb.enrich([rec], _cache(tmp_path / "c", ZIM), companies=[WATCH_ZIM], today=TODAY)
    assert rec["sponsorship"] == "unknown"
    assert "d/b/a" in rec["sponsorship_evidence"] and "FIVE LAKES" in rec["sponsorship_evidence"].upper()


def test_a_dba_only_match_is_unknown_even_in_the_posting_state(tmp_path):
    rec = _job("Zim", "zim", location="Rockville, MD")
    hb.enrich([rec], _cache(tmp_path / "c", ZIM), companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown" and "d/b/a" in rec["sponsorship_evidence"]


def test_a_short_name_outside_the_posting_location_is_unknown_whatever_the_sector(tmp_path):
    rec = _job("Aba", "aba", location="San Francisco Bay Area, CA")
    hb.enrich([rec], _cache(tmp_path / "c", ABA), companies=[], today=TODAY)       # NAICS 54: trusted before
    assert rec["sponsorship"] == "unknown"
    assert "short-name match outside the posting's location" in rec["sponsorship_evidence"]
    assert "ST. PAUL" in rec["sponsorship_evidence"].upper()


def test_a_short_name_with_no_posting_location_is_unknown(tmp_path):
    rec = _job("Aba", "aba", location="Remote (United States)")
    hb.enrich([rec], _cache(tmp_path / "c", ABA), companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"


@pytest.mark.parametrize("location", ["St. Paul, MN", "Minneapolis, MN", "St. Paul"])
def test_a_short_name_matched_in_the_posting_city_or_state_stays_yes(tmp_path, location):
    rec = _job("Aba", "aba", location=location)
    hb.enrich([rec], _cache(tmp_path / "c", ABA), companies=[], today=TODAY)
    assert rec["sponsorship"] == "yes"


def test_a_dictionary_word_name_needs_a_location_match(tmp_path):
    idx = _cache(tmp_path / "c", ["2025,SCALE INC,7777,DENVER,CO,80202,541512,6,0,6,0"])
    rec = _job("Scale", "scale")
    hb.enrich([rec], idx, companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"


def test_a_company_domain_does_not_confirm_a_short_name(tmp_path):
    """The hub carries no domains: aba.co says nothing about which ABA files (h1b_names is the override)."""
    rec = _job("Aba", "aba")
    hb.enrich([rec], _cache(tmp_path / "c", ABA), companies=[{"slug": "aba", "name": "Aba", "website": "https://aba.co"}],
              today=TODAY)
    assert rec["sponsorship"] == "unknown"


def test_h1b_names_on_the_watchlist_still_override(tmp_path):
    rec = _job("Zim", "zim", location="San Francisco")
    hb.enrich([rec], _cache(tmp_path / "c", ZIM),
              companies=[{**WATCH_ZIM, "h1b_names": ["FIVE LAKES SERVICES LLC D B A ZIM"]}], today=TODAY)
    assert rec["sponsorship"] == "yes"


def test_a_long_full_legal_name_is_untouched(tmp_path):
    idx = _cache(tmp_path / "c", ["2025,QUILLFEATHER ROBOTICS INC,2222,AUSTIN,TX,78701,541512,4,0,4,0"])
    rec = _job("Quillfeather Robotics", "quillfeather-robotics")
    hb.enrich([rec], idx, companies=[], today=TODAY)
    assert rec["sponsorship"] == "yes"
