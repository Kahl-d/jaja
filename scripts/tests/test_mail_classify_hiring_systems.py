"""mail_classify.py: Oracle Recruiting Cloud (Taleo's successor), Eightfold, Avature and Gem mail is hiring-system mail.
Their confirmations and rejections must not fall to the body-marker fallback or to L2, or an application made
through them could be missed by the tracker. Fixtures are synthetic (Acme, example)."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import mail_classify as mc


def _rec(subject, body, sender):
    return {"id": "r1", "thread_id": "t1", "from": sender, "to": ch.DEFAULT_TO, "subject": subject, "body": body,
            "snippet": body[:120], "label_ids": ["INBOX"], "direction": "in"}


class Never:
    name = "never"

    def __call__(self, request):                       # L3 must not be needed for a known hiring system
        raise AssertionError("L3 was asked")


SENDERS = [
    "Acme Careers <no-reply@acme.fa.us2.oraclecloud.com>",            # Oracle Recruiting Cloud tenant
    "Acme Talent <noreply@acme.eightfold.ai>",                         # Eightfold career site
    "Acme Recruiting <acme-careers@acme.avature.net>",                 # Avature tenant
    "Pat Lee at Acme <pat@gem.com>",                                   # Gem sequence relay
]


@pytest.mark.parametrize("sender", SENDERS, ids=["oracle", "eightfold", "avature", "gem"])
def test_hiring_system_domains_are_ats_domains(sender):
    assert mc.cu.registrable_domain(mc.cu.email_domain(mc.cu.parse_address(sender)[1])) in mc.ATS_DOMAINS


@pytest.mark.parametrize("sender", SENDERS, ids=["oracle", "eightfold", "avature", "gem"])
def test_a_rejection_from_a_hiring_system_is_read_at_L1(sender):
    rec = _rec("Regarding your application", "We regret to inform you that you were not selected for the role.",
               sender)
    result = mc.classify(rec, decider=Never())
    assert (result.label, result.level) == ("rejection", "L1")
