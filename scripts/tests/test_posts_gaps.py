"""posts.py gaps, from a LinkedIn hiring-posts pass (state/posts/linkedin-<date>.jsonl):
1. where_call: a post saying "a small team out of Oxford ... London, 6 days in person, with potential relocation to San
   Francisco" must not be called in band. A US place named only as a hedged later move is not a US option.
2. company: LinkedIn posts lost it. A row can carry its job as an attached card ("[Attached job: Applied AI Engineer,
   Example Health, Palo Alto, CA (Verified job)]") and must not be made with no company and no job; "Example Co (YC S26)
   is hiring!" opens with the company; "we're hiring at <Company>" names it ahead of any earlier "at <Company>".
Fixtures: fixtures/posts/linkedin-2026-10-05.jsonl is a synthetic pass file (2 rows: an investor's intro, and a hiring post
with an attached job card). EXAMPLE_CO below is a third, invented row. Nothing touches the network."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import fetch_ats as fa
import js_common as jc
import posts

TODAY = date(2026, 10, 5)
FIX = Path(__file__).resolve().parent / "fixtures" / "posts"
LI_ROWS = [json.loads(line) for line in (FIX / "linkedin-2026-10-05.jsonl").read_text(encoding="utf-8").splitlines() if line]
PIA = next(r for r in LI_ROWS if r["author_name"].startswith("Priya"))
INVESTOR = next(r for r in LI_ROWS if r["author_name"] == "Gil Berman")
EXAMPLE_CO = {"url": "https://www.linkedin.com/posts/conrad-moe_example-co-hiring-activity-7500000000000000900-TeSt",
            "author_name": "Conrad Moe",
            "author_title": "Co-Founder @ Example Co (YC S26) | Underwriting compute markets", "date": "2026-10-05",
            "text": ("Example Co (YC S26) is hiring! We are looking for Founding Engineers ... We're a small team out of "
                     "Oxford, backed by Y Combinator. ... London, 6 days in person, with potential relocation to San "
                     "Francisco.")}


def _rec(row, **kw):
    return posts.structure(posts.normalize_row(dict(row, **kw), "linkedin"))


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "os" / "area" / "group" / "projects" / "job-search"
    for d in ("targets", "prospects", "jobs", "state/posts", "state/x", "outbox", "touches", "profile"):
        (root / d).mkdir(parents=True)
    shutil.copy(FIX / "x-deny.yaml", root / "targets" / "x-deny.yaml")
    (root / "targets" / "platforms.yaml").write_text("platforms:\n  - {id: linkedin, name: LinkedIn, alert_senders: []}\n")
    (root / "targets" / "companies.yaml").write_text("companies: []\n")
    (root / "targets" / "people-watch.yaml").write_text("people: []\n")
    path = root / "state" / "posts" / "linkedin-2026-10-05.jsonl"
    path.write_text((FIX / "linkedin-2026-10-05.jsonl").read_text(encoding="utf-8") + json.dumps(EXAMPLE_CO) + "\n",
                    encoding="utf-8")
    return root


# ----------------------------------------------------------------------------- gap 1: where_call
def test_the_example_co_london_post_with_a_potential_move_to_sf_is_out_of_band():
    rec = _rec(EXAMPLE_CO)
    assert rec["hiring"] and rec["role_title"] == "Founding Engineer"
    assert not rec["in_band"] and rec["where_call"].startswith("out of band: placed outside the US")
    assert "later move" in rec["where_call"] and "relocation to San Francisco" in rec["where_call"]


def test_a_place_only_abroad_in_person_is_out_of_band_whatever_the_hedged_move():
    for text in ("Founding engineers, Oxford, in person.",
                 "Berlin, onsite five days. Possible relocation to NYC later.",
                 "Bangalore office, in office. Relocation to San Francisco is an option down the line.",
                 "Toronto, in person, with the opportunity to relocate to Seattle."):
        assert posts.where_call(text).startswith("out of band"), text


def test_guard_a_us_option_stays_in_band():
    for text in ("SF or London, agents and evals",                                   # the existing rule
                 "London or San Francisco, 5 days in person.",
                 "London, NYC or Remote US. Potential relocation to San Francisco later.",
                 "Our founders met in London. The role is in SF, in person.",
                 "London office. Relocation to San Francisco provided.",          # not hedged: the role is in SF
                 "San Francisco, in person, with potential relocation to London later.",
                 "We're hiring a Founding AI Engineer at Tiny AI. San Francisco, in person."):
        assert posts.where_call(text) == "in band", text
    pia = _rec(PIA)
    assert pia["where_call"] == "in band" and pia["in_band"]
    assert posts.where_call(INVESTOR["text"]) == "in band"


def test_guard_the_existing_paris_india_and_must_be_based_rules_hold():
    assert "work permit" in posts.where_call("Paris office, EU work permit required. AI engineers.")
    assert "India" in posts.where_call("We're hiring AI engineers in India. Onsite.")
    assert posts.where_call("Must be based in London. Python, agents.").startswith("out of band")


# ----------------------------------------------------------------------------- gap 2: company
def test_the_pia_row_takes_company_and_role_from_its_attached_job_card():
    rec = _rec(PIA)
    assert (rec["company"], rec["role_title"]) == ("Example Health", "Applied AI Engineer")
    assert rec["hiring"] and rec["in_band"] and rec["location"] == ["Palo Alto"]


def test_attached_job_cards_in_both_forms():
    card = posts.attached_job
    assert card(PIA["text"]) == {"title": "Applied AI Engineer", "company": "Example Health",
                                   "location": "Palo Alto, CA"}
    assert card("[Attached job: Software Engineer, Agents, Acme, New York, NY]") == {
        "title": "Software Engineer, Agents", "company": "Acme", "location": "New York, NY"}
    assert card("[Attached job: AI Engineer, Acme Dynamics, USA (Remote)]")["company"] == "Acme Dynamics"
    assert card("[Attached job: AI Engineer, Acme, San Francisco Bay Area]")["location"] == "San Francisco Bay Area"
    assert card("[Attached job: AI Engineer, Acme, Wilmington, DE (Hybrid)]")["location"] == "Wilmington, DE (Hybrid)"
    assert card("[Attached job: AI Engineer, Acme, Austin, Texas, USA]")["company"] == "Acme"
    assert card("[Attached job: AI Engineer, Acme]") == {"title": "AI Engineer", "company": "Acme", "location": None}
    plain = "We're hiring!\nApplied AI Engineer / Example Health / Palo Alto, CA\nDM me."
    assert card(plain) == {"title": "Applied AI Engineer", "company": "Example Health", "location": "Palo Alto, CA"}
    assert card("AI / ML / LLM\nagents / evals / RAG") is None                       # no role named: not a card
    assert card("We're hiring AI engineers.") is None


def test_the_example_co_post_takes_its_company_from_the_opening():
    assert _rec(EXAMPLE_CO)["company"] == "Example Co"
    assert posts.opening_company("Example Co (YC S26) is hiring!") == "Example Co"
    assert posts.opening_company("Acme Robotics is hiring founding engineers.") == "Acme Robotics"
    assert posts.opening_company("🚀 Tiny AI is now hiring AI engineers") == "Tiny AI"
    for text in ("Everyone is hiring AI engineers", "My team is hiring", "AI is hiring?", "We are hiring",
                 "Big news! Acme is hiring", "Hiring is hard"):
        assert posts.opening_company(text) is None, text


def test_were_hiring_at_names_the_company_before_an_earlier_at():
    text = "Spent 5 years at Initech. Now we're hiring at Acme Robotics: founding engineers, in person in SF."
    rec = _rec(EXAMPLE_CO, text=text, author_title="Engineer")
    assert rec["company"] == "Acme Robotics" and rec["hiring"] and rec["in_band"]
    assert posts._company("We’re hiring engineers at Example Health", {}, None) == "Example Health"


def test_guard_companies_found_before_stay_the_same():
    assert _rec(INVESTOR)["company"] == "Lumera" and not _rec(INVESTOR)["hiring"]               # an investor's intro, as before
    assert posts._company("We're hiring a Founding AI Engineer at Tiny AI.", {}, None) == "Tiny AI"
    assert posts._company("Looking for an agent engineer.", {"bio": "Head of AI at Scribe"}, None) == "Scribe"
    assert posts._company("We're hiring.", {}, {"company": "inkwell"}) == "inkwell"


# ----------------------------------------------------------------------------- ingest
def test_a_dry_ingest_of_the_pass_file_would_make_the_pia_job_and_not_example_co(js):
    before = sorted(p.relative_to(js) for p in js.rglob("*"))
    rep = posts.ingest(js, today=TODAY, write=False, os_root=js.parents[3])
    assert sorted(p.relative_to(js) for p in js.rglob("*")) == before                # a dry run writes nothing
    assert (rep["read"], rep["new"], rep["hiring"], rep["in_band"]) == (3, 3, 2, 1)
    assert rep["would_make"] == [{"post": PIA["url"], "role": "Applied AI Engineer", "company": "Example Health",
                                  "years": "in band", "prospect": True, "job": True}]
    out = {o["post"]: o for o in rep["out_of_band"]}
    assert EXAMPLE_CO["url"] in out and out[EXAMPLE_CO["url"]]["where"].startswith("out of band")


def test_a_write_makes_the_example_health_job_from_the_card(js):
    rep = posts.ingest(js, today=TODAY, write=True, os_root=js.parents[3])
    assert rep["errors"] == [] and rep["jobs"] == 1
    (job,) = fa.load_jobs(js).values()
    assert (job["company"], job["title"], job["platform"]) == ("Example Health", "Applied AI Engineer", "linkedin")
    assert job["hiring_signal"] == {"url": PIA["url"], "date": "2026-10-05", "by": "Priya (Pia) Shah"}
    assert job["prospect"] and len(list((js / "prospects").glob("*.md"))) == 1      # Pia only: EXAMPLE_CO is out of band
