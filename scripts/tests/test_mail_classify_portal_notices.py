"""mail_classify.py: ZipRecruiter recruiter invites are recruiter outreach; the boards' own housekeeping is other.

ZipRecruiter invites from seekerteam@ ("I'm interested in you for my <Title> position at <Company>", a recruiter,
often a staffing or contract firm, writing through the board) and portal notices (Wellfound's password mail,
Glassdoor's profile sync note, Welcome to the Jungle's profile and welcome notes, Indeed's "your job alert is active"
confirmations) must not be labelled job_alert.
scripts/job_alerts.py already ledgers those notices as notices (NOTICE_SUBJECT). Fixtures are SYNTHETIC: the shape of
the real mails (sender, Gmail categories, List-Unsubscribe, subject and lede) with invented names, companies, places and
tokens."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

UNSUB = "<mailto:unsubscribe@unsub.example-mta.test?subject=unsubscribe:SYNTHtoken0001>"


def _mail(sender: str, subject: str, body: str, categories: tuple[str, ...] = ("CATEGORY_UPDATES", "INBOX"),
          unsub: bool = True, list_id: str = "") -> dict:
    rec = {"id": "syn-notice-1", "thread_id": "syn-notice-1", "from": sender, "to": ch.DEFAULT_TO,
           "subject": subject, "body": body, "snippet": body[:120], "label_ids": list(categories), "direction": "in"}
    if unsub:
        rec["list_unsubscribe"] = UNSUB
    if list_id:
        rec["list_id"] = list_id
    return rec


# ------------------------------------------------------------------------------------- ZipRecruiter recruiter invites
ZIP_FOOTER = (" ZipRecruiter, Inc.\n 100 Example Way, Suite 1, Fairview, OR\n 00000, USA\n"
              "Privacy Policy <https://www.ziprecruiter.com/SYNTHprivacy> | Unsubscribe "
              "<https://www.ziprecruiter.com/SYNTHunsub>\n")


def _invite(first: str, full: str, company: str, title: str, place: str) -> dict:
    body = (f"Hi Sam,\n I'm {first} and I've been using ZipRecruiter to find candidates for my  {title} "
            f"<https://www.ziprecruiter.com/km/SYNTHjob0001?tsid=000000000&auth_token=SYNTHauth0001> position at "
            f"{company} in {place}. I'm interested in candidates with your qualifications and would love to invite you "
            "to apply. If you're interested in learning more about the position, click \"I'm Interested\" to move "
            "forward and submit an application.\nI'm Interested <https://www.ziprecruiter.com/km/SYNTHjob0002>\n "
            f"Regards,\n {full} at {company}\n\n" + ZIP_FOOTER)
    display = f"{full} at {company}"
    display = f'"{display}"' if "," in display else display     # quoted like the real header
    return _mail(f"{display} <seekerteam@ziprecruiter.com>",
                 f"I'm interested in you for my {title} position at {company}", body,
                 categories=("CATEGORY_PROMOTIONS", "UNREAD", "INBOX"), list_id="<syn-jobs.ziprecruiter.com>")


INVITES = {
    "staffing firm, contract role": _invite("Mara", "Mara Velloso", "Quillfield Staffing Inc",
                                            "Applied AI Engineer (Contract Role)", "Fairview, Oregon"),
    "employer, full-time role": _invite("Tomas", "Tomas Brenner", "Harrowgate Robotics, LLC",
                                        "Autonomy Software Engineer", "Port Marlow, California"),
}

ZIP_ALERTS = {
    "daily alert, open position subject": _mail(
        "ZipRecruiter <alerts@ziprecruiter.com>", "Sam, Brightwell Analytics has an open position",
        "https://www.ziprecruiter.com/SYNTHlogo\n\nHi Sam,\n Here are today's jobs recommended for you:\n"
        " Machine Learning Engineer  <https://www.ziprecruiter.com/km/SYNTHjob0003>\n\n"
        "Brightwell Analytics • Fairview, OR • On-site\n\n 1-Click Apply  <https://www.ziprecruiter.com/km/X>\n"
        " AI Engineer  <https://www.ziprecruiter.com/km/SYNTHjob0004>\n\nQuarrystone Labs • Remote\n\n"
        " View Details  <https://www.ziprecruiter.com/km/Y>\n", list_id="<job_alerts.ziprecruiter.com>"),
    "daily alert, opening at subject": _mail(
        "ZipRecruiter <alerts@ziprecruiter.com>", "Applied AI Engineer - Remote opening at Quarrystone Labs",
        "Hi Sam,\n Here are today's jobs recommended for you:\n Applied AI Engineer - Remote  "
        "<https://www.ziprecruiter.com/km/SYNTHjob0005>\n\nQuarrystone Labs • Fairview, OR • Remote\n\n"
        " 1-Click Apply  <https://www.ziprecruiter.com/km/Z>\n", list_id="<job_alerts.ziprecruiter.com>"),
    "career advisor, candidates like you": _mail(
        "\"Phil @ ZipRecruiter\" <phil@ziprecruiter.com>", "Brightwell Analytics is looking for candidates like you!",
        "Hi Sam,\n\n Here's a NEW job at Brightwell Analytics. I wanted to get it in front of you ASAP.\n\n"
        " Senior AI Engineer\nNEW!  <https://www.ziprecruiter.com/km/SYNTHjob0006>\n * Brightwell Analytics\n"
        " * Fairview, OR\n\n 1-Click Apply  <https://www.ziprecruiter.com/km/W>\n\n View More Jobs  "
        "<https://www.ziprecruiter.com/km/V>\n\n Phil\n Your Career Advisor\n",
        categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX"), list_id="<syn-jobs.ziprecruiter.com>"),
}


@pytest.mark.parametrize("name", sorted(INVITES))
def test_a_ziprecruiter_invite_is_recruiter_outreach_not_a_job_alert(name):
    result = mc.classify(INVITES[name], account_emails={OWNER})
    assert result.label == "recruiter_outreach", (name, result.label, result.level, result.evidence)
    assert result.confidence >= mc.THRESHOLD and not result.needs_review
    assert result.evidence


@pytest.mark.parametrize("name", sorted(ZIP_ALERTS))
def test_ziprecruiter_alerts_stay_job_alert(name):
    result = mc.classify(ZIP_ALERTS[name], account_emails={OWNER})
    assert result.label == "job_alert", (name, result.label, result.level)


@pytest.mark.parametrize("name,company", [("staffing firm, contract role", "Quillfield Staffing Inc"),
                                          ("employer, full-time role", "Harrowgate Robotics, LLC")])
def test_an_invite_names_the_company_from_the_subject_and_no_made_up_title(name, company):
    identity = mc.classify(INVITES[name], account_emails={OWNER}).identity
    assert identity["company"] == company and identity["title"] is None, (name, identity)


def test_the_invite_shape_from_another_ziprecruiter_box_is_not_outreach():
    rec = dict(INVITES["staffing firm, contract role"], **{"from": "ZipRecruiter <alerts@ziprecruiter.com>"})
    assert mc.classify(rec, account_emails={OWNER}).label == "job_alert"


# ------------------------------------------------------------------------------------------ the boards' housekeeping
INDEED_ACTIVE = ("Your job alert is active\n\nYou'll receive your first daily job alert for {q} in {where} when jobs "
                 "become available.\n\nIn the meantime, you can browse new jobs on Indeed any time by copying and "
                 "pasting the link below\n\nhttps://www.indeed.com/jobs?q=SYNTHquery&from=jaWelcome\n\nIf you have "
                 "received this email in error or no longer wish to receive these types of emails, you may "
                 "unsubscribe by copying and pasting the link below\n\nhttps://www.indeed.com/SYNTHunsub\n\n"
                 "© 2026 Indeed, Inc.\nPrivacy Policy: https://www.indeed.com/SYNTHprivacy\n")
INDEED_LIST = "<jobalert.0000synth0000.indeed.com>"

NOTICES = {
    "wellfound password reset": _mail(
        "Wellfound <accounts@wellfound.com>", "Resetting your Wellfound password",
        "Sam,\nSomeone has requested a link to change your password. You can do this through the link below.\n"
        "Change my password\nIf you didn't request this, please ignore this email.\nThanks,\nTeam Wellfound\n",
        categories=("IMPORTANT", "CATEGORY_PERSONAL", "INBOX"), unsub=False),
    "wellfound password changed": _mail(
        "Wellfound <accounts@wellfound.com>", "Your Wellfound password was changed",
        "Sam,\nThis is a confirmation that your Wellfound password has just been changed.\n"
        "If you didn't make this change, please reply to this message immediately.\nTeam Wellfound\n",
        categories=("UNREAD", "IMPORTANT", "CATEGORY_PERSONAL", "INBOX"), unsub=False),
    "glassdoor profiles synced": _mail(
        "Glassdoor <noreply@glassdoor.com>", "Your Glassdoor and Indeed profiles are now synced", "",
        categories=("UNREAD", "IMPORTANT", "CATEGORY_UPDATES", "INBOX"), unsub=False),
    "wttj profile enhanced": _mail(
        "Welcome to the Jungle <no-reply@welcometothejungle.com>", "Your profile has been enhanced!",
        "Please review the information that was automatically added to complete your Welcome profile!\n\n"
        " Your profile has been enhanced!\n\n Good news, Sam!\n\nWe've completed the Work experience, Skills and "
        "Education sections of your profile based on your resume.\n\nReview my profile ( "
        "https://u0000000.ct.sendgrid.net/ls/click?upn=SYNTHprofile )\n",
        categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX"), unsub=False),
    "wttj welcome": _mail(
        "Welcome to the Jungle <help@welcometothejungle.com>", "Welcome! You're in \U0001f389",
        "Hi Sam,\nWe've done our best to build something that makes finding your next career move easier.\n"
        "Pro tip: Keep your profile updated so opportunities can come to you.\nStep 1: Skip, save or apply\n"
        "Start swiping\nStep 2: Update your profile\nAdd information\nStep 3: Turn on alerts\nSet up alerts\n",
        categories=("CATEGORY_PROMOTIONS", "UNREAD", "IMPORTANT", "INBOX")),
    "indeed alert confirmation, title query": _mail(
        "Indeed <donotreply@jobalert.indeed.com>",
        "Your job alert for title:(\"agent engineer\" OR \"LLM engineer\") -intern jobs in Remote is now active",
        INDEED_ACTIVE.format(q="title:(\"agent engineer\" OR \"LLM engineer\") -intern", where="Remote"),
        categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX"), list_id=INDEED_LIST),
    "indeed alert confirmation, phrase query": _mail(
        "Indeed <donotreply@jobalert.indeed.com>",
        "Your job alert for \"founding engineer\" (AI OR LLM) jobs in Fairview, OR is now active",
        INDEED_ACTIVE.format(q="\"founding engineer\" (AI OR LLM)", where="Fairview, OR"),
        categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX"), list_id=INDEED_LIST),
}

REAL_ALERTS = {
    "glassdoor daily list": _mail(
        "Glassdoor Jobs <noreply@glassdoor.com>", "All the top jobs for AI Engineer in Fairview, OR. Ready to apply?",
        "Job alert: AI Engineer\nCheck out your newest listings! You'll get the latest ones each day.\n"
        "Your job listings for October 5, 2026\nAI Engineer\nFairview, OR\nBrightwell Analytics\n$150K - $190K "
        "(Employer est.)\nEasy Apply\n3d\nApplied AI Engineer\nFairview, OR\nQuarrystone Labs\n5d\n"),
    "wellfound job digest": _mail(
        "Wellfound <jobs@mail.wellfound.com>", "New jobs: AI Engineer at Quarrystone Labs and  6 more jobs",
        "Jobs matching your preferences\n\nAI Engineer\nQuarrystone Labs · Fairview, OR\nView job\n\n"
        "Founding Engineer\nBrightwell Analytics · Remote\nView job\n\nSee all jobs\n"),
    "wttj job digest": _mail(
        "Welcome to the Jungle <no-reply@welcometothejungle.com>", "New jobs for you: Agent Platform Engineer",
        "Jobs picked for you\n\nAgent Platform Engineer\nLanternfield AI\nPort Marlow, CA\n"
        "https://www.welcometothejungle.com/en/companies/lanternfield-ai/jobs/agent-platform-engineer_port-sable\n"
        "See all jobs\n"),
    "wttj welcome that carries job picks": _mail(
        "Welcome to the Jungle <help@welcometothejungle.com>", "Welcome! You're in \U0001f389",
        "Hi Sam,\nJobs picked for you\n\nAgent Platform Engineer\nLanternfield AI\nPort Marlow, CA\n"
        "https://www.welcometothejungle.com/en/companies/lanternfield-ai/jobs/agent-platform-engineer_port-sable\n"
        "See all jobs\n", categories=("CATEGORY_PROMOTIONS", "INBOX")),
    "indeed daily alert with the same subject form": _mail(
        "Indeed <donotreply@jobalert.indeed.com>", "Your job alert for \"agent engineer\" in Fairview, OR",
        "12 new agent engineer jobs in Fairview, OR\n\nJobs that match your saved search\n\nAgent Engineer\n"
        "Brightwell Analytics - Fairview, OR\n$160,000 - $200,000 a year\nEasily apply\n\nLLM Engineer\n"
        "Quarrystone Labs - Remote\nNew\n\nSee all jobs\n© 2026 Indeed, Inc.\n", list_id=INDEED_LIST),
}


@pytest.mark.parametrize("name", sorted(NOTICES))
def test_a_board_housekeeping_notice_is_other_not_a_job_alert(name):
    result = mc.classify(NOTICES[name], account_emails={OWNER})
    assert result.label == "other", (name, result.label, result.level, result.evidence)
    assert result.level == "L0" and result.confidence >= mc.THRESHOLD and not result.needs_review
    assert result.evidence


@pytest.mark.parametrize("name", sorted(REAL_ALERTS))
def test_real_alerts_from_the_same_senders_stay_job_alert(name):
    result = mc.classify(REAL_ALERTS[name], account_emails={OWNER})
    assert result.label == "job_alert", (name, result.label, result.level)


def test_the_indeed_confirmation_is_read_from_the_body_never_the_subject_alone():
    rec = dict(NOTICES["indeed alert confirmation, title query"])
    rec["body"] = REAL_ALERTS["indeed daily alert with the same subject form"]["body"]   # "... is now active" subject
    assert mc.classify(rec, account_emails={OWNER}).label == "job_alert"


# ---------------------------------------------------------------------------------------- people stay people
RELAYED = {
    "employer through indeedemail.com": _mail(
        "Acme Robotics <reply-syn0001@indeedemail.com>", "Applied AI Engineer at Acme Robotics",
        "Hi Sam, your job alert is active on our side too: we saw your profile and think you would fit our Applied "
        "AI Engineer role. Would you be open to a 20 minute call this week?\n", categories=("INBOX",), unsub=False),
    "founder through wellfound.us": _mail(
        "Rae Okafor <rae@wellfound.us>", "Welcome! You're in our founding engineer shortlist at Lattice Robotics",
        "Hi Sam, I'm Rae, the founder of Lattice Robotics. Your profile has been enhanced by your evaluation work, "
        "and we're hiring a founding AI engineer. Would you be open to a 20 minute call this week?\n",
        categories=("INBOX",), unsub=False),
    "recruiter writing from a staffing firm's own domain": _mail(
        "Mara Velloso <mara@quillfield-staffing.example.com>",
        "I'm interested in you for my Applied AI Engineer position at Quillfield Staffing",
        "Hi Sam, I'm Mara, a technical recruiter at Quillfield Staffing. I'm hiring for an Applied AI Engineer "
        "role with my client in Fairview, OR. Would you be open to a quick call this week?\n",
        categories=("INBOX",), unsub=False),
}


@pytest.mark.parametrize("name", sorted(RELAYED))
def test_a_person_writing_is_never_a_board_notice_or_alert(name):
    result = mc.classify(RELAYED[name], account_emails={OWNER})
    assert result.label not in ("other", "job_alert", "newsletter"), (name, result.label, result.level)
