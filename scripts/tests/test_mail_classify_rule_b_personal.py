"""mail_classify.py rule B: a person's own note through a career-site CRM relay or a copilot@ box is not a blast.

Rule B labels as job_alert any mail from a Clinch relay (user-<hash>@<company>.mail.clinchtalent.com), an alerts.*
sender, an "... Alerts" display name or a copilot@ box: such mail is normally a machine digest (an employer's "Here are
some new roles", a staffing firm's alert templates, an AI sourcing bot). But Clinch is an employer CRM and a company
sourcer may write from copilot@, so a recruiter's own note on those two routes must reach the normal ladder. Fixtures
are SYNTHETIC: typical sender shapes and subjects, with invented names, teams, companies and tokens."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

CLINCH = "user-0a1b2c3d4e5f60718293a4b5c6d7e8f9@acme.mail.clinchtalent.com"
CLINCH_TEAM = f"The Talent Acquisition team at Acme <{CLINCH}>"


def _mail(sender: str, subject: str, body: str, categories: tuple[str, ...] = ("CATEGORY_UPDATES", "INBOX")) -> dict:
    return {"id": "syn-ruleb-1", "thread_id": "syn-ruleb-1", "from": sender, "to": ch.DEFAULT_TO, "subject": subject,
            "body": body, "snippet": body[:120], "label_ids": list(categories), "direction": "in"}


def _label(rec: dict) -> mc.Classification:
    return mc.classify(rec, account_emails={OWNER})


def _blast(rec: dict) -> bool:
    return mc._blast(mc.features(rec, {OWNER}))


# ------------------------------------------------------------------------------------------------ personal notes
CLINCH_NOTE_BODY = (
    "Hi Sam,\n\nI'm Dana, a recruiter on Acme's Perception team. I came across your profile and your work on "
    "evaluation harnesses for LLM agents stood out.\n\nWe are growing the perception evaluation group and I'd love to "
    "talk about a Senior Machine Learning Engineer role on the team. Would you be open to a 20 minute call next "
    "week?\n\nBest,\nDana Okafor\nTechnical Recruiter, Perception\nAcme\n")
COPILOT_NOTE_BODY = (
    "Hi Sam,\n\nMy name is Priya and I lead technical sourcing at Northwind Robotics. I saw your profile and the "
    "agent tooling you built. We are hiring a founding applied AI engineer to own our planning stack.\n\nAre you open "
    "to a short chat this week?\n\nThanks,\nPriya Raman\nTechnical Sourcer, Northwind Robotics\n")

PERSONAL = {
    "clinch relay, recruiter's own note, team display name": _mail(
        CLINCH_TEAM, "Perception team at Acme: a quick chat?", CLINCH_NOTE_BODY),
    "clinch relay, recruiter's own note, person display name": _mail(
        f"Dana Okafor <{CLINCH}>", "Perception team at Acme: a quick chat?", CLINCH_NOTE_BODY),
    "copilot@ box, company sourcer's note": _mail(
        "Priya Raman <copilot@northwind-robotics.example.com>", "Sam, a note from Northwind Robotics", COPILOT_NOTE_BODY,
        categories=("CATEGORY_PERSONAL", "INBOX")),
}


@pytest.mark.parametrize("name", sorted(PERSONAL))
def test_a_persons_note_on_a_shared_channel_is_not_a_job_alert(name):
    rec = PERSONAL[name]
    assert not _blast(rec), name
    result = _label(rec)
    assert result.label != "job_alert", (name, result.label, result.level, result.evidence)
    assert result.label == "recruiter_outreach" and result.confidence >= mc.THRESHOLD, (name, result.label)


# ------------------------------------------------------------------------------------------------ guards (blasts stay)
ACME_DIGEST = _mail(CLINCH_TEAM, "Here are some new roles at Acme", (
    "Face, Head, Person, Electronics, Mobile Phone, Phone,\nIndoors, Basketball, Basketball (Ball), Sport\n\n"
    "Hi Sam,\n\nHere are some jobs that best fit your preferences!\n\n"
    "Machine Learning Engineer, Simulation Realism\nMountain View, California\n\n"
    "Acme is an autonomous driving technology company with\nthe mission to be the world's most trusted driver.\n\n"
    "Read More\n( https://api.clinchtalent.com/v1/public/messages/SYNTHmsg0001/events/clicked?signature=SYNTH01 )\n\n"
    "Senior Software Engineer, Model Data Infra\nMountain View, California\n\n"
    "Read More\n( https://api.clinchtalent.com/v1/public/messages/SYNTHmsg0001/events/clicked?signature=SYNTH02 )\n\n"
    "Research Scientist, Planner Evaluation\nSan Francisco, California\n\n"
    "Read More\n( https://api.clinchtalent.com/v1/public/messages/SYNTHmsg0001/events/clicked?signature=SYNTH03 )\n"))
STAFFING_ALERT = _mail("Staffing Alerts <jobs@alerts.staffingco.example.com>", "Keep Going With More Jobs Like Applied ML Engineer", (
    "Apply Now, Corin and Wren are hiring for Applied ML Engineer\nand Data Platform Engineer\nroles!\n\n"
    "[Staffing logo]\n\nSam Sample\n\n12 New Jobs for your Job Search\n\nRecommended Apply -- Based on your resume\n\n"
    "Applied ML Engineer\n[https://url0000.alerts.staffingco.example.com/ls/click?upn=SYNTHtoken01]\n\n"
    "[📍] Fairview, CA     [💵] $150,000 - $180,000\n1-Click Apply\n\n[Corin Vale]\nCorin\n"))
SOURCING_BOT = _mail("Alice from SourceBot <copilot@sourcebot.example.com>", "Sam, Alice flagged your profile this morning", (
    "Hi Sam,\n\nI hope you are doing well.\n\n I reviewed your profile this morning and wanted to reach out "
    "personally. With your experience as a Instructor , your background stood out and I flagged you as a top match "
    "for over 200 active roles across our network of 300,000+ companies right now. I felt your profile was worth a "
    "personal note rather than a standard job alert.\n\nWhen you sign up for free you get:\n\n- 200+ roles matched "
    "specifically to your background\n\n- Full job details unlocked instantly\n\n Takes 30 seconds. No credit card "
    "required.\n\n Explore your matches here\n\nLooking forward to helping you land your next great role.\n\nBest,\n "
    "Alice\n AI Recruiter\n SourceBot\n\n Unsubscribe\n"), categories=("CATEGORY_PROMOTIONS", "INBOX"))
# An alerts.* sender keeps rule B even when its text reads like a person: an alert system by its own name.
ALERTS_DIGEST = _mail("Jon Mercer <jon.mercer@alerts.staffingco.example.com>", "Interested in a Founding Engineer job?", (
    "Hi Sam!\n\nI'm Jon, a recruiter at Staffing Co. I came across your profile and I'd love to talk about the role "
    "below. Are you open to it?\n\nFounding Engineer\n[https://url0000.alerts.staffingco.example.com/ls/click?upn=SYNTHtoken02]\n\n"
    "Thank you,\nJon Mercer\n"), categories=("CATEGORY_PROMOTIONS", "INBOX"))

GUARDS = {"acme clinch digest": ACME_DIGEST, "staffing alert": STAFFING_ALERT, "alerts.* digest": ALERTS_DIGEST,
          "sourcing bot blast": SOURCING_BOT}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_machine_digests_on_rule_b_routes_stay_job_alert(name):
    assert _blast(GUARDS[name]), name
    result = _label(GUARDS[name])
    assert result.label == "job_alert", (name, result.label, result.level, result.evidence)


def test_sourcing_bot_reaches_out_personally_but_its_scale_keeps_it_a_blast():
    """The bot says "I reviewed your profile ... wanted to reach out personally" (a person signal) but offers "200 active
    roles across our network of 300,000+ companies": the scale marker keeps the AI copilot a blast."""
    f = mc.features(SOURCING_BOT, {OWNER})
    assert any(rx.search(f.body) for rx in mc.PERSONAL_NOTE_SIGNALS)
    assert not mc._personal_note(f)
    assert _label(SOURCING_BOT).label == "job_alert"


def test_an_alerts_sender_is_a_blast_even_with_a_persons_wording():
    f = mc.features(ALERTS_DIGEST, {OWNER})
    assert mc._personal_note(f)                    # the text alone would pass as a note
    assert mc._blast(f)                            # the alerts.* route is not a shared channel


# ------------------------------------------------------------------------- each signal and each marker, one at a time
NEUTRAL = "Hi Sam,\n\nThe team is growing this quarter.\n\nBest,\nDana Okafor\nAcme\n"
SIGNALS = {
    "my name is": "My name is Dana and the team is growing this quarter.",
    "i'm <name>, a recruiter": "I'm Dana, a technical recruiter on the Perception team.",
    "came across your profile": "I came across your profile while staffing the Perception team.",
    "i'd love to talk": "I'd love to talk with you about the Perception team.",
    "are you open to": "Are you open to a short call about the Perception team?",
}
MARKERS = {
    "here are some roles": "Here are some new roles on the team.",
    "jobs that best fit your": "We picked jobs that best fit your preferences.",
    "apply now": "Apply now while the team is growing.",
    "<name> and <name> are hiring for": "Corin and Wren are hiring for the team.",
    "scale": "You match 40 open roles this week.",
}


def _clinch(body: str) -> dict:
    return _mail(CLINCH_TEAM, "The Perception team at Acme", body)


def test_with_no_signal_a_clinch_mail_stays_a_blast():
    assert _blast(_clinch(NEUTRAL))


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_each_personal_signal_alone_lifts_rule_b(name):
    body = NEUTRAL.replace("The team is growing this quarter.", SIGNALS[name])
    assert not _blast(_clinch(body)), name


@pytest.mark.parametrize("name", sorted(MARKERS))
def test_each_digest_marker_alone_keeps_rule_b(name):
    body = f"Hi Sam,\n\n{SIGNALS['are you open to']} {MARKERS[name]}\n\nBest,\nDana\n"
    assert not _blast(_clinch(f"Hi Sam,\n\n{SIGNALS['are you open to']}\n\nBest,\nDana\n"))    # the note alone
    assert _blast(_clinch(body)), name


def test_a_list_of_three_short_title_lines_keeps_rule_b_and_two_do_not():
    titles = ["Machine Learning Engineer, Perception", "Research Scientist, Planner", "Software Engineer, Infra"]
    head = "Hi Sam,\n\nAre you open to a short call?\n\n"
    assert _blast(_clinch(head + "\n".join(titles) + "\n\nBest,\nDana\n"))
    assert not _blast(_clinch(head + "\n".join(titles[:2]) + "\n\nBest,\nDana\n"))
