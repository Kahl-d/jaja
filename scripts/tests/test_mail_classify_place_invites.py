"""mail_classify.py: calendar invites for a place (a viewing, a street, an address) are not interview scheduling.

A calendar invite for an apartment viewing is not an interview: invites that say "Tour" are handled by TOUR_WORDS, and
one titled only with a street name must not become interview_scheduling just because the organizer's domain is a
company domain. Fixtures are SYNTHETIC: invented sender, company and streets, the subject shape kept."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

BODY = "Invitation from Google Calendar. You have been invited to the following event. Join with Google Meet: no."


def _rec(subject: str, body: str = BODY) -> dict:
    return {"id": "syn-cal-1", "thread_id": "syn-cal-1", "from": "Rae Okafor <rae@latticerobotics.example.com>",
            "to": ch.DEFAULT_TO, "subject": subject, "body": body, "snippet": body[:120], "label_ids": ["INBOX"],
            "direction": "in", "has_calendar": True,
            "calendar": {"summary": subject.split(": ", 1)[-1].split(" @ ")[0], "organizer": "rae@latticerobotics.example.com"}}


@pytest.mark.parametrize("subject", [
    "Invitation from an unknown sender: Juniper street @ Sun Sep 13, 2026 3pm - 3:30pm (PDT) (k@example.com)",
    "Invitation: 1450 Larkin @ Sun Sep 13, 2026 2pm - 2:30pm (PDT)",
    "Invitation: Viewing at Harbor Lofts @ Sat Sep 12, 2026 11am - 12pm (PDT)",
])
def test_place_invites_are_not_interview_scheduling(subject):
    result = mc.classify(_rec(subject), account_emails={OWNER})
    assert result.label != "interview_scheduling", (subject, result.label, result.level, result.evidence)


@pytest.mark.parametrize("subject", [
    "Invitation: Interview with Lattice Robotics @ Mon Oct 6, 2026 10am - 11am (PDT)",
    "Invitation: Onsite loop, 500 Howard Street @ Tue Oct 7, 2026 9am - 2pm (PDT)",
])
def test_interview_invites_stay_interview_scheduling(subject):
    result = mc.classify(_rec(subject), account_emails={OWNER})
    assert result.label == "interview_scheduling", (subject, result.label, result.level, result.evidence)
