"""job_alerts.py: alert mail that did not become job cards.

What the alert mail looks like (state/harvest/*.classified.jsonl):
- ZipRecruiter (alerts@ziprecruiter.com, body only): every mail was "partial" because two links behind ZipRecruiter's
  opaque tracking tokens wrap the app badge ("Pat / Your Career Advisor / Get Hired Faster / Download the free iOS or
  Android app ...") and the footer ("ZipRecruiter, Inc. (c) All Rights Reserved Worldwide", the street address, the pay
  disclaimer). Those are not jobs. Two real card shapes also failed: a company named with a dash ("<Name> - Direct
  Hire * City, ST * In-person") and a state alone as the place ("<Name> * OR * On-site +1").
- LinkedIn (link cards): a company named like a greeting ("Hey <Name> . City, ST (On-site)"), a title padded with
  " | " tags past the field length, a long company name with parentheses, and a company named with a dash.
- Wellfound (hello@hi.wellfound.com, body): an internship card whose meta line ends in "Internship", read as a second title.
- Welcome to the Jungle, Wellfound and Indeed mail in review were the boards' own notices, not alerts:
  Indeed's "Your job alert for <query> is now active", WTTJ's "Your profile has been enhanced!" and
  "Welcome! You're in", Wellfound's password reset and change; plus LinkedIn's first-time job seeker note.
  They hold no job card; they are ledgered `notice` and kept off the review list. A notice subject on mail that does
  carry a job card is parsed as usual.

Fixtures are SYNTHETIC: the real shape with invented companies, places, job ids and tracking tokens. The harness blocks
the network."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import fetch_ats as fa  # noqa: E402
import job_alerts as ja  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

PLATFORMS = {
    "ziprecruiter": {"id": "ziprecruiter", "name": "ZipRecruiter", "alert_senders": ["ziprecruiter.com"]},
    "linkedin": {"id": "linkedin", "name": "LinkedIn",
                 "alert_senders": ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com"]},
    "wellfound": {"id": "wellfound", "name": "Wellfound", "alert_senders": ["wellfound.com", "angel.co"]},
    "wttj": {"id": "wttj", "name": "Welcome to the Jungle", "alert_senders": ["welcometothejungle.com", "otta.com"]},
    "indeed": {"id": "indeed", "name": "Indeed", "alert_senders": ["indeed.com"]},
}
ANY_TITLE = fa.TitleFilter([re.compile(".")], [])
NOW = datetime(2026, 10, 5, 22, 0, tzinfo=timezone.utc)


def row(rid: str, sender: str, subject: str, body: str = "", links: list | None = None) -> dict:
    r = {"id": rid, "from": sender, "subject": subject, "body": body, "internal_date": "2026-10-05T18:00:00Z",
         "body_truncated": False, "classification": {"label": "job_alert"}}
    if links is not None:
        r["links"] = links
    return r


def parse(r: dict) -> ja.Parsed:
    return ja.parse_alert(r, PLATFORMS, NOW, ANY_TITLE)


def by_title(parsed: ja.Parsed) -> dict:
    return {rec["title"]: rec for rec in parsed.records}


# ============================================================================== ZipRecruiter (body)
def zr(token: str) -> str:
    return f"https://www.ziprecruiter.com/km/SYNTH{token}AAAAbbbbCCCCddddEEEEffff?tsid=111000999&contact_id=0000beef"


def ekm(token: str) -> str:
    return f"https://www.ziprecruiter.com/ekm/SYNTH{token}ZZZZyyyyXXXXwwww?tsid=111000999"


def zr_card(n: int, title: str, company_line: str, pay: str, button: str = "1-Click Apply") -> str:
    return f" {title}  <{zr(f'{n}a')}>\n\n{company_line}\n\n{pay}\n\n {button}  <{zr(f'{n}b')}>\n"


ZR_BODY = (f"<{zr('logo')}>\n\nHi Alex,\n Here are today's jobs recommended for you:\n"
           + zr_card(1, "Machine Learning Engineer", "Lanternfield AI • Port Marlow, CA • On-site",
                     "$180K - $240K/yr")
           + zr_card(2, "Senior Applications Engineer",
                     "Brightwell Partners - Direct Hire • Cedar Hollow, CA • In-person",
                     "$130K - $160K / yr")
           + zr_card(3, "Data Scientist - AI Trainer", "Quartzline • OR • On-site +1", "$75 - $150/hr",
                     button="View Details")
           + f"\n View More Jobs  <{ekm('more')}>\n\n"
           "Phil\nYour Career Advisor\n\nGet Hired Faster\n"
           "Download the free iOS or Android app and search millions of jobs from anywhere.\n"
           f" <{ekm('ios')}>  <{ekm('android')}>\n\n"
           "ZipRecruiter, Inc. © All Rights Reserved Worldwide\n"
           " 1 Synthetic Way, Suite 0, Nowhere, ZZ 00000, USA\n"
           "ZipRecruiter provides pay estimates when companies do not include pay in the job post. ZipRecruiter "
           "estimates are based on pay from similar jobs (in similar areas), may change over time, and may not be "
           "indicative of pay for the position.\n"
           f"Privacy Policy <{ekm('privacy')}> | Unsubscribe <{ekm('unsub')}>\n")


def test_zr_app_badge_and_footer_are_bare_links_not_unparsed_cards():
    parsed = parse(row("syn-zr-1", "ZipRecruiter <alerts@ziprecruiter.com>", "Alex, Lanternfield AI has an open "
                       "position", ZR_BODY))
    assert parsed.status == "parsed", (parsed.status, parsed.unparsed)
    assert parsed.unparsed == []
    assert parsed.cards == 3 and len(parsed.records) == 3
    assert parsed.bare >= 2                                  # the app badge and the footer block


def test_zr_company_named_with_a_dash_keeps_the_whole_name():
    rec = by_title(parse(row("syn-zr-2", "alerts@ziprecruiter.com", "s", ZR_BODY)))["Senior Applications Engineer"]
    assert rec["company"] == "Brightwell Partners - Direct Hire"
    assert "Cedar Hollow, CA" in rec["location"]


def test_zr_a_state_alone_is_the_place():
    rec = by_title(parse(row("syn-zr-3", "alerts@ziprecruiter.com", "s", ZR_BODY)))["Data Scientist - AI Trainer"]
    assert rec["company"] == "Quartzline"
    assert "OR" in rec["location"].split(" | ")
    assert ja.is_location("OR") and ja.is_location("CA")
    assert not ja.is_location("Or") and not ja.is_location("AI")


def test_a_footer_mark_never_hides_a_card_with_a_role_line():
    assert ja.footer_card(["Phil", "Your Career Advisor", "Get Hired Faster"])
    assert ja.footer_card(["ZipRecruiter, Inc. © All Rights Reserved Worldwide", "1 Synthetic Way, Nowhere, ZZ"])
    assert not ja.footer_card(["Machine Learning Engineer", "Lanternfield AI • Port Marlow, CA",
                               "© Lanternfield AI"])
    assert not ja.footer_card(["Lanternfield AI", "Port Marlow, CA"])        # no footer mark: an ordinary card


# ============================================================================== LinkedIn (link cards)
def li_links(*cards: str) -> list[dict]:
    out = []
    for i, card in enumerate(cards, 1):
        href = f"https://www.linkedin.com/comm/jobs/view/44000000{i:02d}/?trackingId=SYNTH{i}%3D%3D&refId=SYNTHref{i}"
        out += [{"card": card, "href": href, "text": ""}, {"href": href, "text": card.splitlines()[0]}]
    return out


def test_li_company_named_like_a_greeting_is_the_company():
    card = "Member of Technical Staff, Agents\nHey Wren · Port Marlow, CA (On-site)\nApplied on October 3, 2026"
    parsed = parse(row("syn-li-1", "LinkedIn <jobs-noreply@linkedin.com>", "Alex, your application was sent to "
                       "Hey Wren", links=li_links(card)))
    assert parsed.status == "parsed", parsed.unparsed
    assert parsed.records[0]["company"] == "Hey Wren"
    assert parsed.records[0]["title"] == "Member of Technical Staff, Agents"


def test_a_greeting_line_is_still_noise():
    assert ja._noise("Hi Alex,") and ja._noise("Hey there!")
    assert not ja._noise("Hey Wren · Port Marlow, CA (On-site)")
    assert not ja._noise("Hey Wren", greeting=False)


def test_li_title_padded_with_tags_keeps_its_first_piece():
    title = ("Founding AI Engineer (Python, TypeScript, Node.js) | LLM Agents and AI-Native Platform | Port Marlow Bay | "
             "Up to USD $300K + Founder Level Equity")
    assert len(title) > 140                                   # past the field length, as in the real card
    card = title + "\nTidewater Forge · Port Marlow Bay Area (On-site)"
    parsed = parse(row("syn-li-2", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "AI Engineer at Synth",
                       links=li_links(card)))
    assert parsed.status == "parsed", parsed.unparsed
    rec = parsed.records[0]
    assert rec["title"] == "Founding AI Engineer (Python, TypeScript, Node.js)"
    assert rec["company"] == "Tidewater Forge"


def test_li_long_company_name_with_a_place_is_split():
    company = "OBRX (Oakfield Outfitters, Brambleton Group, Rookwood People & Vale)"
    line = f"{company} · Lindenfordshire, PA (Hybrid)"
    assert len(line) > 90                                     # past the field length, as in the real card
    parsed = parse(row("syn-li-3", "jobalerts-noreply@linkedin.com", "s", links=li_links("Staff Software Engineer\n"
                                                                                       + line)))
    assert parsed.status == "parsed", parsed.unparsed
    assert parsed.records[0]["company"] == company
    assert parsed.records[0]["location"] == "Lindenfordshire, PA (Hybrid)"


def test_li_company_named_with_a_dash_keeps_the_whole_name():
    cards = ["AI/ML Software Engineer\nFernhollow Defense - Fernhollow Technology Inc. · Atlanta, GA (Remote)",
             "AI / LLM Engineer\nOrcabright - Meet Your Digital Coworker · Atlanta, GA (Hybrid)"]
    parsed = parse(row("syn-li-4", "jobalerts-noreply@linkedin.com", "s", links=li_links(*cards)))
    assert parsed.status == "parsed", parsed.unparsed
    assert {r["company"] for r in parsed.records} == {"Fernhollow Defense - Fernhollow Technology Inc.",
                                                     "Orcabright - Meet Your Digital Coworker"}


def test_a_long_prose_line_is_still_dropped():
    kept, _ = ja.card_lines(["Machine Learning Engineer", "Lanternfield AI",
                             "We are hiring engineers who love to build careful systems for people every day, "
                             "and we hope you will help us build the next one with the whole team | Port Marlow, CA",
                             "Our team builds careful tools for growers and their families across the whole valley | Port Marlow, CA"])
    assert kept == ["Machine Learning Engineer", "Lanternfield AI"]


# ============================================================================== Wellfound (body)
WF_BODY = ("<https://angel.co>\n\nHi Alex! I've found 2 new jobs that might interest you!\n\n"
           " Ready to Interview  Open to offers  Closed to Offers\n\n"
           "Partner Engineering Intern (Summer 2026)\n\nQuillmark Analytics / 51-200 Employees\n\n"
           " | Port Marlow, CA | years of exp | Internship\n\nActively Hiring Growing fast\n\n"
           "Learn More <https://wellfound.com/jobs?job_listing_slug=4100001-partner-engineering-intern>\n\n"
           "Staff Engineer\n\nArbor Health / 11-50 Employees\n\n $230k to 290k | Lindenford, NY | years of exp | Full-time\n\n"
           "Actively Hiring\n\n"
           "Learn More <https://wellfound.com/jobs?job_listing_slug=4100002-staff-engineer>\n")


def test_wf_internship_meta_line_is_not_a_second_title():
    parsed = parse(row("syn-wf-1", "Wellfound <hello@hi.wellfound.com>", "New jobs: Staff Engineer at Arbor Health "
                       "and 1 more jobs", WF_BODY))
    assert parsed.status == "parsed", parsed.unparsed
    rec = by_title(parsed)["Partner Engineering Intern"]
    assert rec["company"] == "Quillmark Analytics"
    assert rec["employment_type"] == fa.norm_employment("Internship")


def test_a_role_word_beyond_the_job_type_keeps_the_line_whole():
    assert not ja._role_piece("Port Marlow | years of exp | Internship")
    assert ja._role_piece("Engineering Intern | Port Marlow")


# ============================================================================== the boards' own notices
INDEED_ACTIVE = ("Your job alert is active\n\nYou'll receive your first daily job alert for title:(\"agent engineer\") "
                 "in Remote when jobs become available.\n\nIn the meantime, you can browse new jobs on Indeed any time "
                 "by copying and pasting the link below\n\nhttps://engage.indeed.com/f/a/SYNTHsearch~~/AAAA~/SYNTHblob\n\n"
                 "© 2026 Indeed, Inc.\nManage job alerts: https://engage.indeed.com/f/a/SYNTHmanage~~/AAAA~/X\n")
NOTICES = [
    ("Indeed <donotreply@jobalert.indeed.com>", "Your job alert for title:(\"agent engineer\") -intern jobs in Remote is "
     "now active", INDEED_ACTIVE),
    ("Welcome to the Jungle <no-reply@welcometothejungle.com>", "Your profile has been enhanced!",
     "Good news, Alex!\n\nWe've completed the Work experience section of your profile based on your resume.\n\n"
     "Review my profile ( https://u0000000.ct.sendgrid.net/ls/click?upn=SYNTHprofile )\n"),
    ("Welcome to the Jungle <help@welcometothejungle.com>", "Welcome! You're in \U0001f389",
     "Hi Alex,\nStep 1: Skip, save or apply\nStart swiping\nStep 3: Turn on alerts\nSet up alerts\n"),
    ("Wellfound <hello@wellfound.com>", "Resetting your Wellfound password",
     "Alex,\nSomeone has requested a link to change your password.\nChange my password\n"),
    ("Wellfound <hello@wellfound.com>", "Your Wellfound password was changed",
     "Alex,\nThis is a confirmation that your Wellfound password has just been changed.\n"),
    ("LinkedIn <jobs-noreply@linkedin.com>", "Alex, looking for a new job?",
     "Find the right job for you on LinkedIn\n\nSearch for jobs: https://www.linkedin.com/comm/jobs/search-results/"
     "?origin=SYNTH\n\nUpdate your job preferences\n"),
]


@pytest.mark.parametrize("sender,subject,body", NOTICES)
def test_a_board_notice_is_a_notice_not_a_review_item(sender, subject, body):
    parsed = parse(row("syn-notice", sender, subject, body))
    assert parsed.status == "notice", (parsed.status, parsed.reason)
    assert parsed.records == [] and parsed.unparsed == []


def test_a_notice_subject_on_mail_with_a_job_card_is_parsed():
    body = ("Welcome! Here is a job you may like\n\nAgent Platform Engineer\nLanternfield AI\nPort Marlow, CA\n"
            "https://www.welcometothejungle.com/en/companies/lanternfield-ai/jobs/agent-platform-engineer_port-sable\n")
    parsed = parse(row("syn-wttj-job", "help@welcometothejungle.com", "Welcome! You're in", body))
    assert parsed.status == "parsed", (parsed.status, parsed.unparsed)
    assert parsed.records[0]["company"] == "Lanternfield AI"


def test_an_unknown_empty_mail_still_goes_to_review():
    parsed = parse(row("syn-empty", "Welcome to the Jungle <no-reply@welcometothejungle.com>", "Jobs we think you'll "
                       "like", "Open the app to see them.\n"))
    assert parsed.status == "review"


def test_notices_are_ledgered_and_never_written_to_review(root):
    harvest = root / "state" / "harvest"
    harvest.mkdir(parents=True)
    rows = [row(f"syn-n{i}", s, subj, b) for i, (s, subj, b) in enumerate(NOTICES)]
    (harvest / "gmail-2026-10-05.classified.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                                               encoding="utf-8")
    run = ja.collect(root, NOW, platforms=PLATFORMS, title_filter=ANY_TITLE, ignored={})
    assert run.notices == len(NOTICES) and run.counts()["notices"] == len(NOTICES)
    assert run.review == []
    assert {r["status"] for r in run.ledger_rows} == {"notice"}
    written = ja.write_outputs(root, run, NOW)
    assert written["review"] is None
    again = ja.collect(root, NOW, platforms=PLATFORMS, title_filter=ANY_TITLE, ignored={})
    assert again.skipped == len(NOTICES) and again.notices == 0          # a notice closes the message


# ============================================================================== sender mapping (real registry)
@pytest.mark.parametrize("sender,platform", [
    ("help@welcometothejungle.com", "wttj"), ("no-reply@welcometothejungle.com", "wttj"),
    ("hello@wellfound.com", "wellfound"), ("hello@hi.wellfound.com", "wellfound"),
    ("donotreply@jobalert.indeed.com", "indeed"), ("alerts@ziprecruiter.com", "ziprecruiter"),
    ("jobalerts-noreply@linkedin.com", "linkedin"), ("jobs-noreply@linkedin.com", "linkedin"),
])
@pytest.mark.real_records
def test_the_registry_maps_every_sender_of_the_real_mail(sender, platform):
    platforms = ja.load_platforms(JS_ROOT)
    assert ja.platform_for({"from": sender}, platforms) == platform
