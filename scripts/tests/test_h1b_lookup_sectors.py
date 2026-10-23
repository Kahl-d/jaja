"""h1b_lookup enrich: "Zeda" (a voice-agent startup in San Francisco) matched SU PLAZA
LANGUAGE ACADEMY LLC D B A ZEDA in Hingham, MA and wrote sponsorship 'yes'. A short or weakly matched name of a company that
is not on the watchlist, whose only matching entity files outside the posting's state, is "unknown" with evidence "name
match outside the posting's state", never "yes". Watchlist companies (curated identities) and in-state matches keep "yes"."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import h1b_lookup as hb  # noqa: E402
from test_h1b_lookup import CACHE  # noqa: E402

HEADER = "fiscal_year,employer,tax_id,city,state,zip,naics,approvals,denials,initial_approvals,continuing_approvals"
TODAY = date(2026, 9, 28)


def _cache(folder: Path, rows: list[str]) -> hb.H1bIndex:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uscis_h1b_k185.csv").write_text("\n".join([HEADER, *rows]) + "\n")
    header = (CACHE / "lca_h1b_sample.csv").read_text().splitlines()[0]
    (folder / "lca_k185.csv").write_text(header + "\n")
    return hb.load_index(folder)


ZEDA = ["2024,SU PLAZA LANGUAGE ACADEMY LLC D B A ZEDA,8812,HINGHAM,MA,02043,611630,1,0,1,0",
        "2025,SU PLAZA LANGUAGE ACADEMY LLC D B A ZEDA,8812,HINGHAM,MA,02043,611630,1,0,1,0"]


def _job(**kw):
    rec = {"id": "zeda-li-4469236433", "company": "Zeda", "company_slug": "zeda", "title": "AI Engineer, Voice Agents",
           "location": "San Francisco, CA", "sponsorship": None, "sponsorship_evidence": None}
    rec.update(kw)
    return rec


def test_a_short_name_matched_out_of_state_is_unknown(tmp_path):
    rec = _job()
    hb.enrich([rec], _cache(tmp_path / "c", ZEDA), companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"
    assert "d/b/a" in rec["sponsorship_evidence"]          # the d/b/a rule answers first
    assert "HINGHAM" in rec["sponsorship_evidence"].upper() and "MA" in rec["sponsorship_evidence"]


def test_a_dba_only_match_in_the_posting_state_is_unknown_too(tmp_path):
    """A match that rests only on a "D B A <name>" alias is never enough (was "yes" here)."""
    rec = _job(location="Boston, MA")
    hb.enrich([rec], _cache(tmp_path / "c", ZEDA), companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"


def test_a_remote_posting_with_a_dba_only_match_is_unknown(tmp_path):
    """The d/b/a rule needs no posting state (was "yes" here)."""
    rec = _job(location="Remote (United States)")
    hb.enrich([rec], _cache(tmp_path / "c", ZEDA), companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"


def test_a_watchlist_company_keeps_yes_across_states(tmp_path):
    """Curated identity: Stellar files from CA, a New York seat is still Stellar."""
    idx = _cache(tmp_path / "c", ["2025,STELLAR PLATFORMS INC,1111,OAK GLEN,CA,94025,519130,900,5,600,300"])
    rec = _job(id="stellar-1", company="Stellar", company_slug="stellar", location="New York, NY")
    hb.enrich([rec], idx, companies=[{"slug": "stellar", "name": "Stellar", "tier": "dream"}], today=TODAY)
    assert rec["sponsorship"] == "yes"


def test_a_long_strong_name_off_the_watchlist_keeps_yes_across_states(tmp_path):
    """A full legal-name match ("Quillfeather Robotics" -> QUILLFEATHER ROBOTICS INC) is not a short or weak name."""
    idx = _cache(tmp_path / "c", ["2025,QUILLFEATHER ROBOTICS INC,2222,AUSTIN,TX,78701,541512,4,0,4,0"])
    rec = _job(id="qf-1", company="Quillfeather Robotics", company_slug="quillfeather-robotics")
    hb.enrich([rec], idx, companies=[], today=TODAY)
    assert rec["sponsorship"] == "yes"


def test_posting_state_parsing():
    assert hb.posting_state("San Francisco, CA") == "CA"
    assert hb.posting_state("Boston, Massachusetts") == "MA"
    assert hb.posting_state("Remote (United States)") is None
    assert hb.posting_state("San Francisco, CA; New York, NY") is None          # two states: no single state to check


# ----------------------------------------------------------------------------- sector exceptions
def test_a_tech_or_professional_sector_match_keeps_yes_across_states(tmp_path):
    """A professional-services group (FL, NAICS 54) for a San Francisco seat of the same firm: the employer is real, filing from its home state."""
    idx = _cache(tmp_path / "c", ["2025,ZWC US GROUP LLP,3333,TAMPA,FL,33607,541211,40,1,30,10"])
    rec = _job(id="zwc-1", company="Zwc", company_slug="zwc")
    hb.enrich([rec], idx, companies=[], today=TODAY)
    assert rec["sponsorship"] == "yes"


def test_an_unrelated_sector_short_name_out_of_state_is_unknown(tmp_path):
    """Pelican Checks Corporation (MN, NAICS 56, checks and payments) for a Burbank media seat: likely another employer."""
    idx = _cache(tmp_path / "c", ["2025,PELICAN CHECKS CORPORATION,4444,MINNEAPOLIS,MN,55126,561499,12,0,10,2"])
    rec = _job(id="pelican-1", company="Pelican", company_slug="pelican", location="Burbank, CA")
    hb.enrich([rec], idx, companies=[], today=TODAY)
    assert rec["sponsorship"] == "unknown"


def test_a_multi_location_posting_has_no_single_state():
    assert hb.posting_state("South San Francisco; New York City, New York, United States; San Francisco") is None
