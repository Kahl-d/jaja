"""job_alerts.py against the alert layouts seen in the first real Gmail backlog (lane J21, 2026-09-26).

Fixtures are SYNTHETIC (fixtures/alerts/layout_rows.yaml): the structure of each real layout (line order,
separators, badges, link shapes, which links carry an HTML card) with invented companies, titles, tokens
and names. Every card either becomes a record with the right fields or goes to review; a link with no card
text (logo, app badge, footer) is counted as a bare link, never guessed into a job. Links are only read:
the harness blocks the network and the parser imports no HTTP client."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FIXTURES, NOW

import fetch_ats as fa  # noqa: E402
import job_alerts as ja  # noqa: E402
from js_common import short_hash  # noqa: E402

ALERTS = FIXTURES.parent / "alerts"
ROWS = yaml.safe_load((ALERTS / "layout_rows.yaml").read_text(encoding="utf-8"))
PLATFORMS = ja.load_platforms_file(ALERTS / "platforms.yaml")
ANY_TITLE = fa.TitleFilter([re.compile(".")], [])             # parsing is under test here, not the title filter


def row(rid: str) -> dict:
    r = dict(next(x for x in ROWS if x["id"] == rid))
    r["classification"] = {"label": r.pop("label"), "confidence": 0.95, "level": "L0", "evidence": r["subject"]}
    return r


def parse(rid: str):
    return ja.parse_alert(row(rid), PLATFORMS, NOW, ANY_TITLE)


def fields(parsed) -> list[tuple[str, str]]:
    return sorted((r["company"], r["title"]) for r in parsed.records)


def by_company(parsed) -> dict:
    return {r["company"]: r for r in parsed.records}


def zr(token: str) -> str:
    return f"https://www.ziprecruiter.com/{token}"


# ----------------------------------------------------------------------------- ZipRecruiter
def test_ziprecruiter_digest_title_link_card_then_button():
    p = parse("zr-digest-01")
    assert p.platform == "ziprecruiter" and p.read_from == "body"
    assert fields(p) == [("Helio Systems LLC", "Machine Learning Engineer, Level 3"),
                         ("Ledgerleaf", "Data Engineer"),
                         ("Northwind Robotics, Inc.", "Research Scientist, Applied ML"),
                         ("Quillstack", "Applied AI Engineer - Agent Platform")]
    recs = by_company(p)
    q = recs["Quillstack"]
    assert q["source_url"] == zr("ekm/AAZZjobTokenAAA1") and q["location"].startswith("San Jose, CA")
    h = recs["Helio Systems LLC"]
    assert h["source_url"] == zr("km/AAZZjobTokenBBB2")          # auth_token, contact_id, tsid dropped
    assert (h["comp_min"], h["comp_max"], h["location"]) == (155000, 207000, "San Francisco, CA")
    n = recs["Northwind Robotics, Inc."]                         # the 1-Click Apply token closes the same card
    assert n["source_url"] == zr("km/AAZZjobTokenCCC3") and n["location"].startswith("Newark, CA")
    assert recs["Ledgerleaf"]["location"] == "Remote"
    assert p.bare == 2                                           # the logo and the "right area?" Edit link
    (lines, reason), = p.unparsed                                # the card the 20,000-character cap cut
    assert "Senior Data Scientist" in lines and "harvest cap" in reason
    assert p.status == "partial" and p.cards == 5


def test_ziprecruiter_single_job_mail_counts_footer_tokens_as_bare_links():
    p = parse("zr-single-01")
    assert p.read_from == "links" and p.status == "parsed" and not p.unparsed
    (rec,) = p.records
    assert (rec["company"], rec["title"], rec["location"], rec["employment_type"]) == \
        ("Orbit Analytics Group", "Senior Machine Learning Engineer - Ranking", "Berkeley, CA", "full-time")
    assert rec["source_url"] == zr("ekm/AAZZjobTokenEEE1")
    assert p.bare == 6 and p.cards == 1                        # logo, View More Jobs, two app badges, footer x2


# ----------------------------------------------------------------------------- Jobright
def test_jobright_company_first_cards():
    p = parse("jr-01")
    assert p.platform == "jobright" and p.read_from == "links" and p.status == "parsed" and not p.unparsed
    assert fields(p) == [("Berkeley Lattice Institute", "Data Platform Engineer"),
                         ("Quillstack", "Applied AI Engineer"),
                         ("Tessellate Solutions, Inc.", "Applied ML (Agents)")]
    q = by_company(p)["Quillstack"]
    assert q["source_url"] == "https://jobright.ai/jobs/info/6a01b2c3d4e5f60718293a4b"
    assert (q["location"], q["comp_min"], q["comp_max"]) == ("San Francisco Bay Area", 150000, 210000)
    assert q["posted_at"] == "2026-09-24"                        # "18 hours ago" counts as the alert day
    assert by_company(p)["Berkeley Lattice Institute"]["location"] == "Oakland, CA"


# ----------------------------------------------------------------------------- Wellfound
def test_wellfound_listing_slug_links_and_digest_cards():
    link = ja.match_job_url("https://wellfound.com/jobs?job_listing_slug=3100001-founding-ml-engineer", "wellfound")
    assert (link.canonical, link.key) == ("https://wellfound.com/jobs/3100001-founding-ml-engineer", "3100001")
    p = parse("wf-01")
    assert p.platform == "wellfound" and p.read_from == "body" and p.status == "parsed" and not p.unparsed
    assert fields(p) == [("Mosaic Robotics", "Research Operations Lead"),
                         ("Parsec Labs", "Founding ML Engineer, Agents"),
                         ("Verdant Health", "Staff Software Engineer")]
    recs = by_company(p)
    parsec = recs["Parsec Labs"]
    assert (parsec["location"], parsec["employment_type"]) == ("San Francisco", "full-time")
    assert parsec["id"] == "parsec-labs-wf-3100001"
    assert "Our Take" not in parsec["_body"].split("## Alert card")[0]
    m = recs["Mosaic Robotics"]
    assert (m["comp_min"], m["comp_max"], m["location"]) == (140000, 170000, "In office, New York City")
    assert recs["Verdant Health"]["remote"] == "remote"


# ----------------------------------------------------------------------------- LinkedIn cards from the HTML
def test_linkedin_company_and_place_on_one_line():
    p = parse("li-cards-01")
    assert p.read_from == "links" and p.status == "parsed" and not p.unparsed
    assert fields(p) == [("Helio Systems", "Lead, Applied AI Engineering"),
                         ("Ledgerleaf Recruiting", "AI Engineer - $58.00 - $70.50 per hour"),
                         ("Northwind Consultants, Inc.", "AI Engineer"),
                         ("Orbit Analytics", "Machine Learning Engineer, Agents"),
                         ("Parsec Labs", "Software Development Engineer, Agents"),
                         ("Quillstack", "Data Scientist - Supply Chain")]
    recs = by_company(p)
    assert (recs["Quillstack"]["location"], recs["Quillstack"]["remote"]) == ("Bellevue, WA (On-site)", "onsite")
    assert recs["Ledgerleaf Recruiting"]["comp_min"] is None       # hourly pay is not read as an annual range
    assert recs["Parsec Labs"]["location"] == "United States (Remote)"


# ----------------------------------------------------------------------------- Glassdoor cards from the HTML
def test_glassdoor_company_first_with_place_like_names_and_badges():
    p = parse("gd-cards-01")
    assert p.read_from == "links" and p.status == "parsed" and not p.unparsed
    assert fields(p) == [("Berkeley Lattice Institute", "Seismic Data Software Engineer"),
                         ("Mosaic Robotics", "Applied AI Engineer"),
                         ("Quillstack", "Research Operations, Agents")]
    assert by_company(p)["Berkeley Lattice Institute"]["location"] == "Berkeley, CA"
    assert by_company(p)["Quillstack"]["posted_at"] == "2026-09-24"


# ----------------------------------------------------------------------------- never guessed, never fetched
def test_a_card_with_two_role_lines_and_no_place_anchor_still_goes_to_review():
    r = row("li-cards-01")
    r["links"] = [{"href": "https://www.linkedin.com/comm/jobs/view/4100000099/", "text": "AI Engineer",
                   "card": "AI Engineer\nStaff Engineer\nQuillstack"}]
    p = ja.parse_alert(r, PLATFORMS, NOW, ANY_TITLE)
    assert not p.records and p.status == "review" and "2 title lines" in p.unparsed[0][1]


def test_an_alert_with_only_bare_links_goes_to_review():
    r = row("zr-digest-01")
    r["body"], r["body_truncated"] = ("<https://www.ziprecruiter.com/km/AAZZlogoToken0001>\n\nHi Jordan,\n"
                                      " View More Jobs  <https://www.ziprecruiter.com/km/AAZZmoreToken0006>\n"), False
    p = ja.parse_alert(r, PLATFORMS, NOW, ANY_TITLE)
    assert p.status == "review" and not p.records and p.bare == 2 and "no job cards" in p.reason


def test_the_parser_never_fetches_a_link():
    source = Path(ja.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+(?:requests|httpx|urllib\.request|http\.client|aiohttp)\b", source, re.M)
    assert "urlopen" not in source
