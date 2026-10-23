"""mail_classify.py: founders and recruiters writing from Wellfound's recruiter domains are people, not alerts.

Besides wellfound.us, Wellfound recruiter mail arrives from wellfound-lab.com and wellfound-careers.com addresses. Fixtures are SYNTHETIC (invented names, companies)."""
from __future__ import annotations

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import agency_config as cfg
import mail_classify as mc

OWNER = cfg.get("identity.emails")[0]       # the configured persona's address

FOUNDER = ("Hi Sam, I'm Rae, the founder of Lattice Robotics. We're hiring a founding AI engineer for our agent "
           "platform, and your evaluation bench work stood out. Would you be open to a 20 minute call this week?")


def _rec(sender: str) -> dict:
    return {"id": "syn-wf-1", "thread_id": "syn-wf-1", "from": sender, "to": ch.DEFAULT_TO,
            "subject": "Founding AI engineer at Lattice Robotics", "body": FOUNDER, "snippet": FOUNDER[:120],
            "label_ids": ["INBOX"], "direction": "in"}


@pytest.mark.parametrize("domain", ["wellfound-lab.com", "wellfound-careers.com"])
def test_wellfound_recruiter_domains_are_board_message_domains(domain):
    assert domain in mc.BOARD_MESSAGE_DOMAINS
    assert mc.RELAY_PORTALS[domain] == "wellfound"


@pytest.mark.parametrize("address", ["rae@wellfound-lab.com", "noreply+rae@wellfound-careers.com"])
def test_a_relay_address_is_never_a_machine(address):
    assert mc.is_machine_sender(address) is False


@pytest.mark.parametrize("sender", ["Rae Okafor <rae@wellfound-lab.com>", "Rae Okafor <rae@wellfound-careers.com>"])
def test_a_founder_through_wellfound_recruiter_domains_is_not_an_alert(sender):
    result = mc.classify(_rec(sender), account_emails={OWNER})
    assert result.label not in ("job_alert", "newsletter"), (sender, result.label, result.level)
