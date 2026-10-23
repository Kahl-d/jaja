"""prospects.gmail_events: a machine sender never creates a person.

A mail pass must not make people of machines: an iCIMS auto-reply (examplecorp+autoreply@talent.icims.com, a referral
invite naming a person), an employer's job digest (donotreply@email.careers.globex.test) and hackajob's bot
(assistant-noreply@hackajob.com). The guard is on create only: a real person at the same company still creates a
record, a LinkedIn relay (messages-noreply@linkedin.com carries a person's words) still creates one, and a machine mail whose
address the existing matcher already knows still lands on that record. Fake rows, temp tree; never the real prospects/.
"""
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


def _roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _row(mid, frm, subject, body, *, label="review", identity=None, at="2026-10-05T08:24:58Z", reply_to="",
         list_id="", thread=None):
    """One harvested inbound mail row in the classified file's shape (state/harvest/gmail-*.classified.jsonl)."""
    return {"id": mid, "from": frm, "to": OWNER, "cc": "", "reply_to": reply_to, "subject": subject,
            "date": at, "internal_date": at, "thread_id": thread or mid, "message_id": f"<{mid}@example.test>",
            "references": None, "list_unsubscribe": "", "list_id": list_id, "portal": None, "direction": "in",
            "body": body, "snippet": body[:120],
            "classification": {"label": label, "confidence": 0.0 if label == "review" else 0.9,
                               "identity": identity or {"company": None, "role": None, "title": None}}}


# Three machine rows in the shape of a classified harvest, with invented names and short bodies.
ICIMS_1 = _row("1a10b2a868b94f5c", '"pat.rivera@examplecorp.test" <examplecorp+autoreply@talent.icims.com>',
               "Career Opportunity at Examplecorp Technologies Inc",
               "Dear Alex,\n\nCongratulations! You were referred to Examplecorp Technologies by Pat Rivera. "
               "Please click the link below to be taken to the job to apply.",
               reply_to="<pat.rivera@examplecorp.test>", list_id="<spc.131558.7293.sparkpostmail.com>")
ICIMS_2 = {**ICIMS_1, "id": "1a10b2b8e5e0f396", "message_id": "<1a10b2b8e5e0f396@example.test>",
           "internal_date": "2026-10-05T08:26:06Z", "date": "2026-10-05T08:26:06Z", "thread_id": "1a10b2a868b94f5c"}
GLOBEX_DIGEST = _row("1a108ea502ce9d93", "Globex Careers <donotreply@email.careers.globex.test>",
                 "New jobs at Globex that match your profile",
                 "Principal Applied Scientist\nUnited States, Washington, Redmond\nApply Now\nView all matching jobs",
                 at="2026-10-04T21:55:36Z", reply_to="Globex Careers <donotreply@email.careers.globex.test>")
ARCHER = _row("1a10a920f337e433", "Archer <assistant-noreply@hackajob.com>", "You're in - Archer's finding your matches",
              "You're in, Alex. Archer's already working.\n\nI'm Archer, your AI agent. I'll find roles where you "
              "genuinely match and show you exactly why - so you apply with confidence.",
              at="2026-10-05T05:38:26Z", identity={"company": None, "role": None, "title": "Archer"},
              reply_to="Archer <notifications+bot@hackajob.co>")
MACHINE_ROWS = {"icims": [ICIMS_1, ICIMS_2], "globex": [GLOBEX_DIGEST], "archer": [ARCHER]}


def _ingest(roots, rows):
    src = roots.harvest / "gmail-2026-10-05.classified.jsonl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return pr.ingest(roots, gmail_files=[src], today=TODAY, account_emails={OWNER})


def _slugs(roots):
    return sorted(p.stem for p in roots.prospects.glob("*.md")) if roots.prospects.is_dir() else []


def _seed(roots, slug, name, emails, company):
    meta = pr.new_meta(slug, name, "outbound", TODAY)
    meta.update({"emails": emails, "company": company, "tags": ["referrer"]})
    roots.prospects.mkdir(parents=True, exist_ok=True)
    cu.write_record(roots.prospects / f"{slug}.md", meta, f"# {name}\n\n## Notes\n\n## Timeline\n")
    return (roots.prospects / f"{slug}.md").read_bytes()


# --------------------------------------------------------------------------- the classifier's test
@pytest.mark.parametrize("email", ["examplecorp+autoreply@talent.icims.com", "donotreply@email.careers.globex.test",
                                   "assistant-noreply@hackajob.com", "no-reply@initech.test", "noreply@globex.test",
                                   "do-not-reply@umbrella.test", "recruiting-no-reply@hooli.test",
                                   "Examplecorp+AutoReply@Talent.iCIMS.com"])
def test_machine_addresses_are_machines(email):
    assert mc.is_machine_sender(email) is True


@pytest.mark.parametrize("email", ["pat.rivera@examplecorp.test", "jane.recruiter@examplecorp.test", "sam.doe@globex.test",
                                   "priya@hackajob.com", "noreen@acme.test", "reply.guy@acme.test", "",
                                   None, "messages-noreply@linkedin.com", "inmail-hit-reply@linkedin.com",
                                   "no-reply@wellfound.us"])
def test_people_and_person_relays_are_not_machines(email):
    assert mc.is_machine_sender(email) is False


# --------------------------------------------------------------------------- ingest: the three machine cases
@pytest.mark.parametrize("case", sorted(MACHINE_ROWS))
def test_machine_sender_creates_no_prospect(tmp_path, case):
    roots = _roots(tmp_path)
    report = _ingest(roots, MACHINE_ROWS[case])
    assert _slugs(roots) == [] and report["created"] == 0


def test_all_three_together_create_nothing_and_leave_a_known_referrer_unchanged(tmp_path):
    """Pat's record carries pat.rivera@examplecorp.test; the iCIMS mail comes from the iCIMS address with the
    display name "Pat.rivera Examplecorp.com", which the existing matcher cannot tie to him. Nothing is created and his
    record is not touched (the referral invite reaches the owner only by hand)."""
    roots = _roots(tmp_path)
    before = _seed(roots, "pat-rivera-ortiz", "Pat Rivera Ortiz", ["pat.rivera@examplecorp.test"],
                   "Examplecorp Technologies")
    report = _ingest(roots, [ICIMS_1, ICIMS_2, GLOBEX_DIGEST, ARCHER])
    assert _slugs(roots) == ["pat-rivera-ortiz"]
    assert report["created"] == 0 and report["skipped"] == 4
    assert (roots.prospects / "pat-rivera-ortiz.md").read_bytes() == before


def test_machine_mail_still_lands_on_a_record_the_matcher_knows(tmp_path):
    """The guard is on create only: a record that already carries the machine address (for example an old
    do_not_contact record) still receives the event, as before."""
    roots = _roots(tmp_path)
    _seed(roots, "pat-rivera-examplecorp-com", "Pat.rivera Examplecorp.com", ["examplecorp+autoreply@talent.icims.com"], None)
    report = _ingest(roots, [ICIMS_2])
    assert _slugs(roots) == ["pat-rivera-examplecorp-com"] and report["created"] == 0
    meta, body = jc.read_record(roots.prospects / "pat-rivera-examplecorp-com.md")
    assert "[k:gm-1a10b2b8e5e0f396]" in body and meta["inbound_count"] == 1


# --------------------------------------------------------------------------- people still create records
def test_real_person_at_the_same_company_domain_still_creates_a_record(tmp_path):
    roots = _roots(tmp_path)
    examplecorp = _row("p-examplecorp", "Jane Recruiter <jane.recruiter@examplecorp.test>", "AI R&D Engineer at Examplecorp",
                    "Hi Alex, I'm a recruiter at Examplecorp Technologies. We're hiring an AI R&D Engineer; "
                    "open to a quick call this week?", label="recruiter_outreach",
                    identity={"company": "Examplecorp Technologies", "role": "recruiter", "title": "Recruiter"})
    globex = _row("p-globex", "Sam Doe <sam.doe@globex.test>", "Applied Scientist role",
                     "Hi Alex, I lead hiring for an Applied Scientist role on my team. Interested in the position?",
                     at="2026-10-05T09:00:00Z")                   # label review + job words: the path the three machine rows took
    report = _ingest(roots, [ICIMS_1, examplecorp, globex])
    assert _slugs(roots) == ["jane-recruiter", "sam-doe"] and report["created"] == 2


def test_real_person_at_the_bot_domain_still_creates_a_record(tmp_path):
    roots = _roots(tmp_path)
    priya = _row("p-hackajob", "Priya Shah <priya@hackajob.com>", "A role that fits you",
                 "Hi Alex, I'm a talent partner at hackajob. A client is hiring an ML Engineer; open to a chat?",
                 label="recruiter_outreach", identity={"company": "hackajob", "role": "recruiter", "title": None})
    report = _ingest(roots, [ARCHER, priya])
    assert _slugs(roots) == ["priya-shah"] and report["created"] == 1


def test_linkedin_relay_from_messages_noreply_still_creates_a_record(tmp_path):
    """messages-noreply@linkedin.com carries a person's message (follow_ups.py says the same); the guard must not
    read its local part as a machine. A guard that read the local part alone would drop every such row."""
    roots = _roots(tmp_path)
    relay = _row("p-relay", "Dana Fox <messages-noreply@linkedin.com>", "Dana sent you a message",
                 "Dana Fox\nHi Alex, we're hiring an ML Engineer at Acme AI. Open to a chat?\n"
                 "https://www.linkedin.com/in/dana-fox-123", label="recruiter_outreach",
                 identity={"company": "Acme AI", "role": "recruiter", "title": "Recruiter"})
    report = _ingest(roots, [relay])
    assert report["created"] == 1 and len(_slugs(roots)) == 1
