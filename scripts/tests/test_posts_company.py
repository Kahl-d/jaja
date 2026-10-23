"""posts.py company: a founder's past employer must not be read as the hiring company.

A post that opens "Example Systems is building AI agents ..., and we're hiring a Founding Engineer" and later says "I
began my career at Initech Technologies before joining Globex" must name Example Systems. Reading only
"<Company> is hiring" skips the opening and takes the first "at <Company>": Initech Technologies, so a dry ingest would
make an Initech job. A past employer named as a past job would be misfiled the same way. Nothing touches the network."""
from __future__ import annotations

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import posts

EXAMPLE_SYS = {"url": ("https://www.linkedin.com/posts/jotran_example-systems-is-building-ai-agents-to-handle-share-"
                "7500000000000000500-9qJZ/"),
        "author_name": "Jo Tran", "author_url": "https://www.linkedin.com/in/jotran/",
        "author_title": "CEO, Example Systems | Automating Property Accounting with AI", "date": "2026-09-30",
        "text": ("Example Systems is building AI agents to handle every step of property accounting, and we're hiring a "
                 "Founding Engineer. My co-founder Brad K. and I have spent our careers at high-growth technology "
                 "companies, primarily in B2B SaaS. I began my career at Initech Technologies before joining Globex "
                 "and co-founding Hooli, which was acquired at the beginning of this year. ... As our first hire, "
                 "you'll work primarily with Brian to design, deploy, and refine our AI agents.")}


def _rec(row, **kw):
    return posts.structure(posts.normalize_row(dict(row, **kw), "linkedin"))


def test_the_example_post_names_the_company_not_the_founders_first_employer():
    rec = _rec(EXAMPLE_SYS)
    assert rec["company"] == "Example Systems"
    assert rec["role_title"] == "Founding Engineer" and rec["hiring"]


def test_a_company_that_opens_building_and_says_were_hiring_is_the_opener():
    assert posts.opening_company("Acme Robotics is building warehouse agents, and we're hiring.") == "Acme Robotics"
    assert posts.opening_company("Tiny AI (YC S26) is automating audits and we’re now hiring engineers") == "Tiny AI"
    for text in ("Acme is building agents. We're hiring.",          # another sentence: not the opener's claim
                 "We are building agents and we're hiring",          # no company opens the post
                 "Acme is building agents for lenders",              # no hiring clause
                 "Everyone is building agents and we're hiring"):
        assert posts.opening_company(text) is None, text


def test_a_past_employer_in_the_text_is_never_the_hiring_company():
    for past in ("I began my career at Initech", "I worked at Globex for six years", "Previously at Hooli",
                 "Before joining Vandelay I was at Umbrella", "Spent 5 years at Soylent",
                 "For six years I was at Umbrella"):
        text = f"{past}. We're hiring a founding AI engineer in SF."
        assert posts._company(text, {}, None) is None, text
        assert posts._company(text, {"bio": "Co-Founder & CEO @ Acme Robotics | agents"}, None) == "Acme Robotics"


def test_the_bio_lead_names_the_founders_company_after_the_text():
    assert posts._company("We're hiring.", {"bio": "CEO, Example Systems | Automating Property Accounting"},
                          None) == "Example Systems"
    assert posts._company("Join us.", {"bio": "Founding Engineer @ Scoreboard.io | Example State CS"}, None) == "Scoreboard.io"
    assert posts._company("We're hiring.", {"bio": "Software engineer, ex-Initech"}, None) is None
    assert posts._company("We're hiring.", {"bio": "Founding Partner, North Fund"}, None) is None   # an investor


def test_guard_a_present_was_building_at_stays_the_company():
    assert posts._company("Understanding what he was building at Lumera took us longer.", {}, None) == "Lumera"


def test_guard_text_companies_still_win_over_the_bio():
    assert posts._company("We're hiring engineers at Example Health", {"bio": "CEO, Acme"}, None) == "Example Health"
    assert posts._company("Come join Acme Robotics in SF.", {"bio": "Founder, Other Co"}, None) == "Acme Robotics"
    assert posts._company("We're hiring.", {"bio": "Head of AI at Scribe"}, None) == "Scribe"
