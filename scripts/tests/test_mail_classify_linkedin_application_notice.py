"""mail_classify.py: LinkedIn application notices from jobs-noreply@linkedin.com are application events, not job alerts.

"Your application was viewed by <company>" arrives from jobs-noreply@linkedin.com; it must not be labelled job_alert, or
the application event is lost. The fixture is a REDACTED copy of that mail's shape: the company is invented and the body
keeps only LinkedIn's footer wording."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

FOOTER = ("This email was intended for Pat Sample. Learn why we included this: "
          "https://www.linkedin.com/help/linkedin/answer/4788")


def _rec(subject: str, sender: str = "LinkedIn <jobs-noreply@linkedin.com>") -> dict:
    return {"id": "syn-li-app-1", "thread_id": "syn-li-app-1", "from": sender, "to": ch.DEFAULT_TO,
            "subject": subject, "body": FOOTER, "snippet": FOOTER[:120], "label_ids": ["INBOX"], "direction": "in"}


@pytest.mark.parametrize("subject", [
    "Your application was viewed by Lattice Robotics",
    "Your application was sent to Lattice Robotics",
    "Pat, your application to Lattice Robotics was viewed",
    "Lattice Robotics: application status update",
])
def test_linkedin_application_notices_are_application_events(subject):
    result = mc.classify(_rec(subject), account_emails={OWNER})
    assert result.label == "ats_confirmation", (subject, result.label, result.level, result.evidence)
    assert result.label in mc.PIPELINE_LABELS


@pytest.mark.parametrize("subject", [
    "Lattice Robotics is hiring for a Remote role",
    "AI engineer: Lattice Robotics and 9 more jobs in San Francisco",
])
def test_linkedin_job_alerts_stay_job_alerts(subject):
    result = mc.classify(_rec(subject), account_emails={OWNER})
    assert result.label == "job_alert", (subject, result.label, result.level)
