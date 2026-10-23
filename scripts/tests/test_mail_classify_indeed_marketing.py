"""mail_classify.py: Indeed's own marketing and notices are newsletter mail, its job alerts stay job_alert.

Indeed sends Resume Builder news, a "Free Resume Report" offer, Terms of Service updates and a "best jobs of 2026"
editorial from the same no-reply box as its alerts; they must not be labelled job_alert. Fixtures are SYNTHETIC: the shape of the real mails
(sender, Gmail categories, List-Unsubscribe, subject and lede) with invented jobs, companies, places and tokens."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

UNSUB = "<mailto:unsubscribe@unsub.example-mta.test?subject=unsubscribe:SYNTHtoken0001>"
FOOTER = ("Access Indeed anywhere\nDownload the free app.\n© 2026 Indeed, Inc.\n"
          "Indeed processes and analyzes your activity in this email.\nIndeed\nPrivacy Policy\nTerms\nHelp\nUnsubscribe\n")


def _indeed(subject: str, body: str, sender: str = "Indeed <no-reply@indeed.com>",
            categories: tuple[str, ...] = ("CATEGORY_PROMOTIONS", "INBOX")) -> dict:
    return {"id": "syn-indeed-1", "thread_id": "syn-indeed-1", "from": sender, "to": ch.DEFAULT_TO,
            "subject": subject, "body": body, "snippet": body[:120], "label_ids": list(categories),
            "direction": "in", "list_unsubscribe": UNSUB, "list_id": "<spc.000000.0.example-mta.test>"}


MARKETING = {
    "resume builder news": _indeed(
        "Good news: Indeed Resume Builder is staying online",
        "Your documents are secure, and Indeed Resume Builder will continue operating.\n" + FOOTER,
        categories=("CATEGORY_UPDATES", "INBOX")),
    "free resume report": _indeed(
        "Free Resume Report",
        "Hello!\nIndeed is excited to offer you a free resume review scan. It is 100% free.\n"
        "Get resume feedback\n" + FOOTER, sender="Indeed Career Services <no-reply@indeed.com>"),
    "terms of service update": _indeed(
        "Terms of Service Updates",
        "You're receiving a notice about our Terms of Service updates.\nHere are some key updates.\n" + FOOTER,
        categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX")),
    "best jobs of 2026 editorial": _indeed(
        "You'll want to see these job openings",
        "Find Jobs\nSign in\nThe best jobs of 2026\nLooking for a great job in 2026? We've rounded up the best roles "
        "with above-average pay and remote or hybrid flexibility.\nRead more\n" + FOOTER),
}

ALERTS = {
    "one-job match mail": _indeed(
        "Data Platform Engineer (Fairview, OR) @ Brightwell Analytics",
        "We thought this job for a Data Platform Engineer at Brightwell Analytics in Fairview, OR paying "
        "$150,000 - $190,000 a year would be a good fit. Check out the job at "
        "https://cts.indeed.com/v3/H4sIAAAAAAAA_SYNTHpayload0001/SYNTHsig0001\n"
        "Unsubscribe: https://cts.indeed.com/v3/H4sIAAAAAAAA_SYNTHpayload0002/SYNTHsig0002\n",
        sender="Indeed <donotreply@match.indeed.com>", categories=("IMPORTANT", "CATEGORY_UPDATES", "INBOX")),
    "daily job alert digest": _indeed(
        "12 new machine learning engineer jobs in Fairview, OR",
        "Jobs that match your saved search: machine learning engineer in Fairview, OR\n\n"
        "Machine Learning Engineer\nBrightwell Analytics - Fairview, OR\n$160,000 - $200,000 a year\nEasily apply\n\n"
        "Applied Scientist, Ranking\nQuarrystone Labs - Remote\nNew\n\n"
        "See all jobs\n" + FOOTER, sender="Indeed <alert@indeed.com>"),
    "alert with a Resume Builder promo in the footer": _indeed(
        "3 new AI engineer jobs in Fairview, OR",
        "Jobs that match your saved search: AI engineer in Fairview, OR\n\n"
        "AI Engineer\nQuarrystone Labs - Fairview, OR\nEasily apply\n\n"
        "LLM Evaluation Engineer\nBrightwell Analytics - Remote\n\n"
        "Polish your profile with Indeed Resume Builder before you apply.\n" + FOOTER,
        sender="Indeed <alert@indeed.com>"),
}


@pytest.mark.parametrize("name", sorted(MARKETING))
def test_indeed_marketing_and_notices_are_newsletter_not_job_alert(name):
    result = mc.classify(MARKETING[name], account_emails={OWNER})
    assert result.label == "newsletter", (name, result)
    assert result.level == "L0" and result.confidence >= mc.THRESHOLD and not result.needs_review
    assert result.evidence                                  # a verbatim sentence or the subject, never empty


@pytest.mark.parametrize("name", sorted(ALERTS))
def test_indeed_job_alerts_stay_job_alert(name):
    result = mc.classify(ALERTS[name], account_emails={OWNER})
    assert result.label == "job_alert", (name, result)


def test_the_marketing_rule_is_for_indeed_mail_only_and_never_for_a_person_writing_through_indeed():
    for name in MARKETING:                                  # the helper fires on every marketing fixture...
        assert mc._indeed_marketing(mc.features(MARKETING[name], {OWNER})), name
    employer = dict(MARKETING["free resume report"], **{"from": "Acme Robotics <reply-syn0001@indeedemail.com>"})
    other_sender = dict(MARKETING["resume builder news"], **{"from": "Pat Doe <pat@co.example.com>"})
    for rec in (employer, other_sender):                    # ...and never on an employer relay or another sender
        assert mc._indeed_marketing(mc.features(rec, {OWNER})) is None, rec["from"]
