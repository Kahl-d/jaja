"""prospects.py: a board box that relays many recruiters is keyed by the person, never the shared address .

A job board's seekerteam@ziprecruiter.com box carries each recruiter's invite ("I'm interested in you for my <Title>
position at <Company>"). A harvest can hold several invites from different recruiters. Keyed by the address, the first would make a
record and the others would land on it, under the company ZipRecruiter. Like the LinkedIn relays, each recruiter is
now a person: no address on the record, the name from the display name or the invite, the company from the subject.
Fixtures are SYNTHETIC (the shape of such invites, invented names, companies and tokens). Temp tree; never the
real prospects/."""
from __future__ import annotations

import json
from datetime import date

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import mail_classify as mc
import prospects as pr

TODAY = date(2026, 10, 5)
OWNER = "alex@example.test"
RELAY = "seekerteam@ziprecruiter.com"


def _roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _offline(request):
    return mc.Decision(mc.REVIEW, 0.0, "", "offline", pending=True)


def _classified(row: dict) -> dict:
    """The row as state/harvest/gmail-*.classified.jsonl holds it: the classifier's own output attached."""
    row = dict(row)
    row["classification"] = mc.classify(row, decider=_offline, account_emails={OWNER}).to_dict()
    return row


def _invite(mid: str, display: str, first: str, company: str, title: str, at: str) -> dict:
    body = (f"Hi Alex,\n I'm {first} and I've been using ZipRecruiter to find candidates for my  {title} "
            f"<https://www.ziprecruiter.com/km/SYNTH{mid}?tsid=000000000&auth_token=SYNTHauth> "
            f"position at {company} in "
            "Fairview, Oregon. I'm interested in candidates with your qualifications and would love to invite you to "
            "apply. If you're interested in learning more about the position, click \"I'm Interested\" to move forward "
            "and submit an application.\nI'm Interested <https://www.ziprecruiter.com/km/SYNTHclick>\n Regards,\n "
            f"{display}\n\n ZipRecruiter, Inc.\n 100 Example Way, Suite 1, Fairview, OR\n 00000, USA\n"
            "Privacy Policy <https://www.ziprecruiter.com/SYNTHprivacy> | Unsubscribe "
            "<https://www.ziprecruiter.com/SYNTHunsub>\n")
    sender = f'"{display}" <{RELAY}>' if "," in display else f"{display} <{RELAY}>"
    return _classified({
        "id": mid, "from": sender, "to": OWNER, "cc": "", "reply_to": "", "direction": "in",
        "subject": f"I'm interested in you for my {title} position at {company}", "body": body, "snippet": body[:120],
        "date": at, "internal_date": at, "thread_id": mid, "message_id": f"<{mid}@example.test>", "references": None,
        "label_ids": ["CATEGORY_PROMOTIONS", "UNREAD", "INBOX"], "portal": None,
        "list_unsubscribe": "<mailto:unsubscribe@unsub.test-mta.test?subject=SYNTH>",
        "list_id": "<syn-jobs.ziprecruiter.com>"})


# The 5 real shapes: an all-caps staffing firm, a short company name, a quoted display with ", LLC", a long company
# name, and a recruiter whose first name is the first word of the company ("Certified ... at Certified ...").
INVITES = [
    ("syn-zr-1", "Nadia Ferreira at QUILLFIELD STAFFING INC", "Nadia", "QUILLFIELD STAFFING INC",
     "Bioinformatics Analyst (Contract Role)", "2026-09-20T17:00:00Z"),
    ("syn-zr-2", "Okon Adeyemi at HARROWMERE DATA INC", "Okon", "HARROWMERE DATA INC", "Data Scientist (MMM)",
     "2026-09-21T17:00:00Z"),
    ("syn-zr-3", "Tomas Brenner at Harrowgate Robotics, LLC", "Tomas", "Harrowgate Robotics, LLC",
     "Autonomy Software Engineer", "2026-09-22T17:00:00Z"),
    ("syn-zr-4", "Priya Raman at Select Lattice International", "Priya", "Select Lattice International",
     "AI-Ops Engineer", "2026-09-23T17:00:00Z"),
    ("syn-zr-5", "Summit Alvarez at Summit Employment Group", "Summit", "Summit Employment Group",
     "Packaging Test Technician (Prototype Lab experience)", "2026-09-24T17:00:00Z"),
]
EXPECTED = {"Nadia Ferreira": "QUILLFIELD STAFFING INC", "Okon Adeyemi": "HARROWMERE DATA INC",
            "Tomas Brenner": "Harrowgate Robotics, LLC", "Priya Raman": "Select Lattice International",
            "Summit Alvarez": "Summit Employment Group"}


def _ingest(roots, rows):
    src = roots.harvest / "gmail-2026-09-28.classified.jsonl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return pr.ingest(roots, gmail_files=[src], today=TODAY, account_emails={OWNER})


def _records(roots) -> list[dict]:
    return [jc.read_record(p)[0] for p in sorted(roots.prospects.glob("*.md"))] if roots.prospects.is_dir() else []


def test_five_invites_from_one_relay_address_make_five_people(tmp_path):
    roots = _roots(tmp_path)
    _ingest(roots, [_invite(*spec) for spec in INVITES])
    records = _records(roots)
    assert {m["name"]: m.get("company") for m in records} == EXPECTED
    for meta in records:
        assert meta.get("emails") == [], (meta["name"], meta.get("emails"))
        assert RELAY not in json.dumps(meta, default=str)        # the shared address is on no record
        assert meta.get("label") == "recruiter_outreach" and meta.get("channels") == ["email"]


def test_one_recruiter_writing_twice_is_one_record(tmp_path):
    roots = _roots(tmp_path)
    first = _invite(*INVITES[0])
    again = _invite("syn-zr-6", INVITES[0][1], "Nadia", "QUILLFIELD STAFFING INC", "Applied AI Engineer (Contract)",
                    "2026-09-27T17:00:00Z")
    _ingest(roots, [first, again])
    records = _records(roots)
    assert [m["name"] for m in records] == ["Nadia Ferreira"]
    assert records[0]["inbound_count"] == 2 and records[0]["emails"] == []


def test_a_company_only_display_takes_the_name_the_invite_gives(tmp_path):
    roots = _roots(tmp_path)
    row = _invite("syn-zr-7", "Quillfield Staffing Inc", "Nadia", "Quillfield Staffing Inc", "ML Engineer",
                  "2026-09-25T17:00:00Z")
    _ingest(roots, [row])
    assert [(m["name"], m["company"], m["emails"]) for m in _records(roots)] == [
        ("Nadia", "Quillfield Staffing Inc", [])]


def test_the_relay_is_one_named_constant():
    assert RELAY in mc.BOARD_PERSON_RELAYS
    assert mc.ZIPRECRUITER_INVITE_SENDERS <= mc.BOARD_PERSON_RELAYS


def test_reconcile_keys_a_relayed_recruiter_by_name_never_the_shared_address():
    keys = pr.sender_keys([_invite(*spec) for spec in INVITES], {OWNER})
    assert RELAY not in keys
    assert {f"name:{cu.normalize_person_name(n)}" for n in EXPECTED} <= keys


# ------------------------------------------------------------------------------------- the other paths are unchanged
LINKEDIN = {"id": "syn-li-1", "from": "Rae Okafor <messages-noreply@linkedin.com>", "to": OWNER, "cc": "",
            "reply_to": "", "direction": "in", "subject": "Rae Okafor sent you a message",
            "body": "Rae Okafor\nFounder at Lattice Robotics\n\nHi Alex, we're hiring a founding AI engineer for our "
                    "agent platform. Would you be open to a call this week?\n\n"
                    "https://www.linkedin.com/in/rae-okafor-synth\n",
            "date": "2026-09-26T17:00:00Z", "internal_date": "2026-09-26T17:00:00Z", "thread_id": "syn-li-1",
            "message_id": "<syn-li-1@example.test>", "references": None, "portal": None, "list_unsubscribe": "",
            "list_id": "", "classification": {"label": "founder_outreach", "confidence": 0.9,
                                              "identity": {"company": "Lattice Robotics", "role": "founder",
                                                           "title": "Founder"}}}
ORDINARY = {**LINKEDIN, "id": "syn-direct-1", "thread_id": "syn-direct-1", "message_id": "<syn-direct-1@example.test>",
            "from": "Mara Velloso <mara@quillfield-staffing.test>",
            "subject": "Applied AI Engineer role at Quillfield Staffing",
            "body": "Hi Alex, I'm Mara, a technical recruiter at Quillfield Staffing. I'm hiring for an Applied AI "
                    "Engineer role. Would you be open to a quick call this week?\n",
            "classification": {"label": "recruiter_outreach", "confidence": 0.9,
                               "identity": {"company": "Quillfield Staffing", "role": "recruiter",
                                            "title": "Technical Recruiter"}}}


def test_a_linkedin_relay_is_unchanged():
    (ev,) = pr.gmail_events([LINKEDIN], {OWNER})
    assert ev["channel"] == "linkedin-message" and ev["create"] is True
    assert ev["person"] == {"name": "Rae Okafor", "emails": [], "linkedin_url": ev["person"]["linkedin_url"],
                            "company": "Lattice Robotics", "title": "Founder"}
    assert "rae-okafor-synth" in ev["person"]["linkedin_url"]


def test_an_ordinary_sender_is_unchanged(tmp_path):
    (ev,) = pr.gmail_events([ORDINARY], {OWNER})
    assert ev["channel"] == "email" and ev["create"] is True
    assert ev["person"]["emails"] == ["mara@quillfield-staffing.test"]
    assert ev["person"]["name"] == "Mara Velloso" and ev["person"]["domain"] == "quillfield-staffing.test"
    roots = _roots(tmp_path)
    _ingest(roots, [ORDINARY])
    assert [(m["name"], m["emails"]) for m in _records(roots)] == [
        ("Mara Velloso", ["mara@quillfield-staffing.test"])]


@pytest.mark.parametrize("display,body,company,name", [
    ("Nadia Ferreira at QUILLFIELD STAFFING INC", "", "QUILLFIELD STAFFING INC", "Nadia Ferreira"),
    ("Summit Alvarez at Summit Employment Group", "", "Summit Employment Group", "Summit Alvarez"),
    ("QUILLFIELD STAFFING INC", "Hi Alex,\n I'm Nadia and I've been using ZipRecruiter", "QUILLFIELD STAFFING INC",
     "Nadia"),
])
def test_board_relay_name(display, body, company, name):
    assert pr.board_relay_name(display, body, company) == name
