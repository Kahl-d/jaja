"""job_alerts.py: Indeed and Welcome to the Jungle alert mail whose job links sit behind opaque click trackers


What real mail looks like (read for shape only): Indeed's alert mail links through
engage.indeed.com/f/a/<id>~~/<id>~/<encrypted payload> (not base64 of the URL), and Welcome to the Jungle wraps every
link in SendGrid click tracking (u<N>.ct.sendgrid.net/ls/click?upn=<encrypted token>); WTTJ's plain text prints each
link as "Label ( url )" and sometimes the link's own address without a scheme beside it. Neither tracker can be read
without fetching it, and links are never fetched. Such mail would otherwise give 0 cards and sit on the review
list, so an opaque link is a job only when the text around it reads as exactly one card, keyed on the board's search
for that card's own words; a profile, welcome or set-up note stays a notice and never a job.

Fixtures are SYNTHETIC (invented companies, tokens and addresses): the real structure, none of the real text. The job
card layout of a WTTJ or Indeed daily alert is UNVERIFIED until a real one arrives (check it with --dry-run --reparse)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import fetch_ats as fa  # noqa: E402
import job_alerts as ja  # noqa: E402

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
PLATFORMS = {"indeed": {"id": "indeed", "name": "Indeed", "alert_senders": ["indeed.com"]},
             "wttj": {"id": "wttj", "name": "Welcome to the Jungle", "alert_senders": ["welcometothejungle.com"]}}
ANY_TITLE = fa.TitleFilter([re.compile(".")], [])


def sg(token: str) -> str:
    return f"https://u9000001.ct.sendgrid.net/ls/click?upn=u001.SYNTH{token}-2Fsynth-3D_SYNTHtail{token}"


def engage(token: str) -> str:
    return f"https://engage.indeed.com/f/a/SYNTH{token}~~/AAAAAQA~/SYNTHpayload{token}~~"


def row(platform: str, subject: str, body: str, mid: str = "m1") -> dict:
    sender = {"indeed": "Indeed <donotreply@jobalert.indeed.com>",
              "wttj": "Welcome to the Jungle <no-reply@welcometothejungle.com>"}[platform]
    return {"id": mid, "from": sender, "from_email": sender.split("<")[1].rstrip(">"), "subject": subject,
            "body": body, "internal_date": "2026-10-06T14:00:00Z", "links": [],
            "classification": {"label": "job_alert"}}


def parse(r: dict) -> ja.Parsed:
    return ja.parse_alert(r, PLATFORMS, NOW, ANY_TITLE)


WTTJ_ALERT = "\n".join([
    "Hi there,",
    "",
    "New jobs for your alert: Machine Learning Engineer",
    "",
    " Orbit Synthetics",
    f" Machine Learning Engineer ( {sg('A1')} )",
    " San Francisco, CA",
    f" See the job ( {sg('A2')} )",
    "",
    " Lumen Example Labs",
    f" Applied AI Engineer ( {sg('B1')} )",
    " Remote, US",
    f" See the job ( {sg('B2')} )",
    "",
    f"Change subscription preferences ( {sg('F1')} ) | Unsubscribe ( {sg('F2')} )",
])

INDEED_ALERT = "\n".join([
    "Machine Learning Engineer",
    "Orbit Synthetics - San Francisco, CA",
    "$150,000 - $190,000 a year",
    engage("J1"),
    "",
    "Applied AI Engineer",
    "Lumen Example Labs - Remote",
    engage("J2"),
    "",
    "See all jobs",
    engage("F1"),
])


def test_opaque_rules_apply_only_to_the_body_reader_and_only_for_their_board():
    assert ja.match_job_url(sg("A1"), "wttj") is None                      # HTML card links and the unlabeled check
    assert ja.match_job_url(engage("J1"), "indeed") is None
    assert ja.match_job_url(sg("A1"), "wttj", opaque=True).key == ""
    assert ja.match_job_url(engage("J1"), "indeed", opaque=True).key == ""
    assert ja.match_job_url(sg("A1"), "linkedin", opaque=True) is None      # SendGrid elsewhere is not a WTTJ job
    assert ja.match_job_url(engage("J1"), "glassdoor", opaque=True) is None


def test_a_wttj_alert_behind_sendgrid_gives_one_record_per_card():
    out = parse(row("wttj", "New jobs for you", WTTJ_ALERT))
    got = sorted((r["company"], r["title"]) for r in out.records)
    assert got == [("Lumen Example Labs", "Applied AI Engineer"), ("Orbit Synthetics", "Machine Learning Engineer")]
    assert out.status == "parsed" and not out.unparsed
    for r in out.records:
        assert r["source_url"].startswith("https://www.welcometothejungle.com/en/jobs?query=")
        assert "sendgrid" not in r["source_url"] and "SYNTH" not in r["source_url"]


def test_an_indeed_alert_behind_engage_links_gives_one_record_per_card():
    out = parse(row("indeed", "Machine Learning Engineer jobs in San Francisco Bay Area, CA", INDEED_ALERT))
    got = sorted((r["company"], r["title"]) for r in out.records)
    assert got == [("Lumen Example Labs", "Applied AI Engineer"), ("Orbit Synthetics", "Machine Learning Engineer")]
    assert all(r["source_url"].startswith("https://www.indeed.com/jobs?q=") for r in out.records)
    assert out.status == "parsed"


def test_the_same_alert_twice_keys_the_same_records():
    a = parse(row("wttj", "New jobs for you", WTTJ_ALERT, mid="m1"))
    b = parse(row("wttj", "New jobs for you", WTTJ_ALERT.replace("SYNTH", "OTHER"), mid="m2"))
    assert sorted(r["source_url"] for r in a.records) == sorted(r["source_url"] for r in b.records)


def test_a_schemeless_wttj_job_address_is_a_keyed_job():
    body = "\n".join([" Orbit Synthetics",
                      " ( www.welcometothejungle.com/en/companies/orbit-synthetics/jobs/ml-engineer_san-francisco )"
                      f"   Machine Learning Engineer ( {sg('A1')} )",
                      " San Francisco, CA"])
    out = parse(row("wttj", "New jobs for you", body))
    assert [r["source_url"] for r in out.records] == [
        "https://www.welcometothejungle.com/en/companies/orbit-synthetics/jobs/ml-engineer_san-francisco"]


@pytest.mark.parametrize("platform,subject,body", [
    ("wttj", "Your profile has been enhanced!",
     "Good news!\n\nWe've completed the Work experience, Skills and Education sections of your profile.\n\n"
     f"   ( www.welcometothejungle.com/en/me/profile )   Review my profile ( {sg('P1')} )\n\n"
     f"To know more, see our Privacy Policy. ( {sg('P2')} )"),
    ("wttj", "Welcome! You're in", "We know job hunting is hard.\nStep 1: Skip, save or apply\n"
     f"Start swiping ( {sg('W1')} )\nStep 3: Turn on alerts\nSet up alerts ( {sg('W2')} )"),
    ("indeed", "Your job alert for machine learning engineer jobs in Remote is now active",
     "Your job alert is active\n\nYou'll receive your first daily job alert when jobs become available.\n\n"
     f"{engage('S1')}\n\nTo unsubscribe, copy the link below\n\n{engage('S2')}"),
])
def test_notes_behind_trackers_stay_notices_with_no_job(platform, subject, body):
    out = parse(row(platform, subject, body))
    assert out.status == "notice" and not out.records and not out.unparsed
