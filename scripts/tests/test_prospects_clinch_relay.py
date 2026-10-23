"""prospects.py: an employer's Clinch career-site CRM relays many recruiters from one address per candidate 

Clinch mails from user-<hash>@<employer>.mail.clinchtalent.com. The hash names the recipient, not the writer:
every row for one employer in a classified harvest shares one address. Keyed by that address, every recruiter at the
employer would fold into one record. Like ZipRecruiter's seekerteam@ (test_prospects_board_relay.py), a host under
mc.BOARD_PERSON_RELAY_DOMAINS is keyed by the person: no address on the record, the company from the employer the host
names (never Clinch), else the identity. A team display name is not a person: the note's "My name is <Name>" names the
writer, and with no name the team stays one record per employer.
A Clinch digest is classified job_alert; a first-person note is recruiter_outreach, so the person fixtures carry that label as
a fixed classification. Fixtures are SYNTHETIC (the shape of such mails, invented names, hashes and tokens). Temp tree; never the real prospects/."""
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
CLINCH = "user-0f1e2d3c4b5a69788796a5b4c3d2e1f0@acme.mail.clinchtalent.com"
OUTREACH = {"label": "recruiter_outreach", "confidence": 0.85, "level": "L2",
            "identity": {"company": None, "role": "recruiter", "title": None}}


def _roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _clinch(mid: str, display: str, body: str, at: str, address: str = CLINCH,
            classification: dict | None = None) -> dict:
    """A row as state/harvest/gmail-*.classified.jsonl holds a Clinch mail: one per-candidate address, reply_to the
    same, the message id on mail.clinchtalent.com, no list headers."""
    sender = f"{display} <{address}>"
    return {"id": mid, "from": sender, "from_email": address, "from_name": display, "to": f"Alex Example <{OWNER}>",
            "cc": "", "reply_to": sender, "direction": "in", "subject": "Your background and our Perception team",
            "body": body, "snippet": body[:200], "date": at, "internal_date": at, "thread_id": mid,
            "message_id": f"<{mid}@mail.clinchtalent.com>", "references": "", "in_reply_to": "",
            "label_ids": ["CATEGORY_UPDATES", "INBOX"], "portal": None, "list_unsubscribe": "", "list_id": "",
            "classification": classification or OUTREACH}


def _note(first: str) -> str:
    return (f"Hi Alex,\n\nI came across your profile and wanted to reach out personally. I'm {first}, a technical "
            "recruiter on the Perception team. We are hiring a Machine Learning Engineer for evaluation tooling and "
            "your agent evaluation work looks close to what the team needs. Would you be open to a short call this "
            f"week?\n\nThanks,\n{first}\n\nUnsubscribe ( https://api.clinchtalent.com/v1/public/SYNTHunsub )\n")


JANE = _clinch("syn-cl-1", "Jane Okafor at Acme", _note("Jane"), "2026-10-01T16:00:00Z")
OMAR = _clinch("syn-cl-2", "Omar Lindqvist at Acme", _note("Omar"), "2026-10-02T16:00:00Z")
TEAM = "The Talent Acquisition team at Acme"
TEAM_NAMED = ("Hi Alex,\n\nMy name is Dana Whitfield and I lead recruiting for our Simulation team. I came across "
              "your profile and would love to chat about a Senior ML Engineer role. Would you be open to a call?\n")
TEAM_UNNAMED = ("Hi Alex,\n\nWe came across your profile and would love to chat about a Senior ML Engineer role on "
                "our Simulation team. Would you be open to a call?\n\nThe Talent Acquisition team\n")
DIGEST = ("Face, Head, Person\n\nHi Alex,\n\nHere are some jobs that best fit your preferences!\n\n"
          "Software Engineer, Onboard Evaluation\nMountain View, California\n\nRead More\n"
          "( https://api.clinchtalent.com/v1/public/messages/SYNTH/events/clicked?url=https%3A%2F%2Fcareers.test )\n")


def _ingest(roots, rows):
    src = roots.harvest / "gmail-2026-10-05.classified.jsonl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return pr.ingest(roots, gmail_files=[src], today=TODAY, account_emails={OWNER})


def _records(roots) -> list[dict]:
    return [jc.read_record(p)[0] for p in sorted(roots.prospects.glob("*.md"))] if roots.prospects.is_dir() else []


def test_two_recruiters_through_one_clinch_address_make_two_people(tmp_path):
    roots = _roots(tmp_path)
    _ingest(roots, [JANE, OMAR])
    records = _records(roots)
    assert sorted((m["name"], m["company"], tuple(m["emails"])) for m in records) == [
        ("Jane Okafor", "Acme", ()), ("Omar Lindqvist", "Acme", ())]
    for meta in records:
        assert meta.get("label") == "recruiter_outreach" and meta.get("channels") == ["email"]


def test_one_recruiter_writing_twice_is_one_record(tmp_path):
    roots = _roots(tmp_path)
    again = _clinch("syn-cl-3", "Jane Okafor at Acme", _note("Jane"), "2026-10-04T16:00:00Z")
    _ingest(roots, [JANE, again])
    records = _records(roots)
    assert [(m["name"], m["emails"], m["inbound_count"]) for m in records] == [("Jane Okafor", [], 2)]


def test_a_team_display_name_takes_the_name_the_note_gives(tmp_path):
    (ev,) = pr.gmail_events([_clinch("syn-cl-4", TEAM, TEAM_NAMED, "2026-10-03T16:00:00Z")], {OWNER})
    assert (ev["person"]["name"], ev["person"]["company"], ev["person"]["emails"]) == ("Dana Whitfield", "Acme", [])
    hiring = _clinch("syn-cl-5", "The Hiring Team at Acme", TEAM_NAMED, "2026-10-03T17:00:00Z")
    assert pr.gmail_events([hiring], {OWNER})[0]["person"]["name"] == "Dana Whitfield"


def test_a_team_with_no_name_in_the_note_is_one_team_record_per_employer(tmp_path):
    roots = _roots(tmp_path)
    rows = [_clinch("syn-cl-6", TEAM, TEAM_UNNAMED, "2026-10-03T16:00:00Z"),
            _clinch("syn-cl-7", TEAM, TEAM_UNNAMED, "2026-10-04T16:00:00Z")]
    _ingest(roots, rows)
    assert [(m["name"], m["company"], m["emails"], m["inbound_count"]) for m in _records(roots)] == [
        ("The Talent Acquisition team", "Acme", [], 2)]


@pytest.mark.parametrize("display,body,company,name", [
    (TEAM, TEAM_NAMED, "Acme", "Dana Whitfield"),
    ("The Hiring Team at Acme", TEAM_NAMED, "Acme", "Dana Whitfield"),
    ("Acme Careers", "Hi Alex,\nMy name is Lee Park.\n", "Acme", "Lee Park"),
    (TEAM, TEAM_UNNAMED, "Acme", "The Talent Acquisition team"),
])
def test_board_relay_name_for_team_displays(display, body, company, name):
    assert pr.board_relay_name(display, body, company) == name


def test_the_company_comes_from_the_employer_subdomain_never_clinch():
    assert pr.board_relay_employer(CLINCH) == "Acme"
    assert pr.board_relay_employer("user-00ff@acme-robotics.mail.clinchtalent.com") == "Acme Robotics"
    assert pr.board_relay_employer("seekerteam@ziprecruiter.com") is None
    (ev,) = pr.gmail_events([JANE], {OWNER})
    assert ev["person"]["company"] == "Acme" and "clinch" not in json.dumps(ev["person"]).lower()
    # The host names the employer; an identity read from the text can be a job title.
    titled = {**OUTREACH, "identity": {"company": "Multimodal Perception", "role": "recruiter", "title": None}}
    (ev,) = pr.gmail_events([_clinch("syn-cl-8", "Jane Okafor at Acme", _note("Jane"), "2026-10-01T16:00:00Z",
                                     classification=titled)], {OWNER})
    assert ev["person"]["company"] == "Acme"


def test_the_relay_is_matched_by_host_and_one_named_constant():
    assert "mail.clinchtalent.com" in mc.BOARD_PERSON_RELAY_DOMAINS
    assert pr.board_person_relay(CLINCH)
    assert pr.board_person_relay("user-1234abcd@globex.mail.clinchtalent.com")
    assert not pr.board_person_relay("jane@clinchtalent.com")               # Clinch's own staff: an ordinary sender
    assert not pr.board_person_relay("user-1@notmail.clinchtalent.com")
    assert not pr.board_person_relay("user-1@acme.mail.clinchtalent.com.test.test")


def _from_only(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "from_email"}


def test_reconcile_keys_clinch_recruiters_by_name_never_the_shared_address():
    keys = pr.sender_keys([_from_only(JANE), _from_only(OMAR)], {OWNER})
    assert keys == {"name:jane okafor", "name:omar lindqvist"}
    # A harvest row's from_email carries no display name (pre-existing in sender_keys): the shared address is still
    # never a key, and a note that names its writer is keyed by that name.
    named = _clinch("syn-cl-10", TEAM, TEAM_NAMED, "2026-10-03T16:00:00Z")
    assert pr.sender_keys([JANE, OMAR, named], {OWNER}) == {"name:dana whitfield"}


def test_a_clinch_digest_is_still_a_job_alert_and_makes_no_record(tmp_path):
    digest = _clinch("syn-cl-9", TEAM, DIGEST, "2026-10-05T16:00:00Z", classification={})
    digest["subject"] = "Here are some new roles at Acme"
    digest["classification"] = mc.classify(digest, decider=lambda r: mc.Decision(mc.REVIEW, 0.0, "", "offline",
                                                                                pending=True),
                                           account_emails={OWNER}).to_dict()
    assert digest["classification"]["label"] == "job_alert"
    roots = _roots(tmp_path)
    _ingest(roots, [digest])
    assert _records(roots) == []


# ------------------------------------------------------------------------------------- the other paths are unchanged
SEEKER = _clinch("syn-zr-1", "Nadia Ferreira at QUILLFIELD STAFFING INC",
                 "Hi Alex,\n I'm Nadia and I've been using ZipRecruiter to find candidates for my ML Engineer "
                 "position at QUILLFIELD STAFFING INC.\n", "2026-09-20T17:00:00Z",
                 address="seekerteam@ziprecruiter.com",
                 classification={"label": "recruiter_outreach", "confidence": 0.85,
                                 "identity": {"company": "QUILLFIELD STAFFING INC", "role": "recruiter",
                                              "title": None}})
LINKEDIN = {"id": "syn-li-1", "from": "Rae Okafor <messages-noreply@linkedin.com>", "to": OWNER, "cc": "",
            "reply_to": "", "direction": "in", "subject": "Rae Okafor sent you a message",
            "body": "Rae Okafor\nFounder at Lattice Robotics\n\nHi Alex, we're hiring a founding AI engineer. "
                    "Would you be open to a call this week?\n\nhttps://www.linkedin.com/in/rae-okafor-synth\n",
            "date": "2026-09-26T17:00:00Z", "internal_date": "2026-09-26T17:00:00Z", "thread_id": "syn-li-1",
            "message_id": "<syn-li-1@example.test>", "references": None, "portal": None, "list_unsubscribe": "",
            "list_id": "", "classification": {"label": "founder_outreach", "confidence": 0.9,
                                              "identity": {"company": "Lattice Robotics", "role": "founder",
                                                           "title": "Founder"}}}
ORDINARY = {**LINKEDIN, "id": "syn-direct-1", "thread_id": "syn-direct-1", "message_id": "<syn-direct-1@example.test>",
            "from": "Mara Velloso <mara@quillfield-staffing.test>",
            "subject": "Applied AI Engineer role at Quillfield Staffing",
            "body": "Hi Alex, I'm Mara, a technical recruiter at Quillfield Staffing. Would you be open to a call?\n",
            "classification": {"label": "recruiter_outreach", "confidence": 0.9,
                               "identity": {"company": "Quillfield Staffing", "role": "recruiter",
                                            "title": "Technical Recruiter"}}}


def test_the_seekerteam_relay_is_unchanged():
    (ev,) = pr.gmail_events([SEEKER], {OWNER})
    assert ev["create"] is True and ev["channel"] == "email"
    assert ev["person"] == {"name": "Nadia Ferreira", "emails": [], "linkedin_url": "",
                            "company": "QUILLFIELD STAFFING INC", "title": None, "domain": ""}
    assert pr.sender_keys([_from_only(SEEKER)], {OWNER}) == {"name:nadia ferreira"}
    assert pr.sender_keys([SEEKER], {OWNER}) == {"name:nadia"}          # from_email: the invite's own "I'm Nadia"


def test_an_ordinary_sender_is_unchanged():
    (ev,) = pr.gmail_events([ORDINARY], {OWNER})
    assert ev["channel"] == "email" and ev["create"] is True
    assert ev["person"]["emails"] == ["mara@quillfield-staffing.test"]
    assert ev["person"]["name"] == "Mara Velloso" and ev["person"]["domain"] == "quillfield-staffing.test"
    assert pr.sender_keys([ORDINARY], {OWNER}) == {"mara@quillfield-staffing.test"}


def test_a_linkedin_relay_is_unchanged():
    (ev,) = pr.gmail_events([LINKEDIN], {OWNER})
    assert ev["channel"] == "linkedin-message" and ev["create"] is True
    assert ev["person"]["name"] == "Rae Okafor" and ev["person"]["emails"] == []
    assert ev["person"]["company"] == "Lattice Robotics" and "rae-okafor-synth" in ev["person"]["linkedin_url"]
