"""Portal tags (every portal, even email, has tags, trackers and follow-ups): every mail record mail_classify.py emits
from a job-board or board-relay sender carries `portal: <targets/platforms.yaml id>`, and the touches written from that
mail carry the same field. Tags live in our records only: no Gmail label is written."""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import mail_classify as mc  # noqa: E402
import prospects as pr  # noqa: E402

PLATFORMS = {"platforms": [
    {"id": "linkedin", "alert_senders": ["jobalerts-noreply@linkedin.com"]},
    {"id": "indeed", "alert_senders": ["indeed.com"]},
    {"id": "wellfound", "alert_senders": ["wellfound.com", "angel.co"]},
    {"id": "yc", "alert_senders": ["workatastartup.com", "ycombinator.com"]},
    {"id": "jobright", "alert_senders": ["jobright.ai"], "inbox_senders": ["support@jobright-mail.example"]},
    {"id": "indeed-connector", "alert_senders": []},
]}


def _platforms(root):
    path = root / "targets" / "platforms.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(PLATFORMS))
    return mc.portal_index(root)


def test_alert_senders_inbox_senders_and_the_relay_fallback(root):
    idx = _platforms(root)
    assert mc.portal_for("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", idx) == "linkedin"
    assert mc.portal_for("Dana via LinkedIn <messages-noreply@linkedin.com>", idx) == "linkedin"   # relay fallback
    assert mc.portal_for("jobs@indeed.com", idx) == "indeed"
    assert mc.portal_for("Acme Employer <abc123@indeedemail.com>", idx) == "indeed"               # message domain
    assert mc.portal_for("founder@wellfound.us", idx) == "wellfound"
    assert mc.portal_for("hello@mail.workatastartup.com", idx) == "yc"                           # subdomain
    assert mc.portal_for("support@jobright-mail.example", idx) == "jobright"                     # inbox_senders
    assert mc.portal_for("no-reply@greenhouse-mail.io", idx) is None                             # an ATS is no portal
    assert mc.portal_for("jane@acme.example.test", idx) is None


def test_a_fallback_id_missing_from_the_registry_is_never_emitted(root):
    path = root / "targets" / "platforms.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"platforms": [{"id": "linkedin", "alert_senders": []}]}))
    idx = mc.portal_index(root)
    assert mc.portal_for("founder@wellfound.us", idx) is None                                    # no wellfound id
    assert mc.portal_for("messages-noreply@linkedin.com", idx) == "linkedin"


def test_no_registry_means_no_tags(root):
    assert mc.portal_index(root) == {}
    assert mc.portal_for("jobs@indeed.com", {}) is None


def test_classify_file_tags_the_record(root):
    idx = _platforms(root)
    src = root / "state" / "harvest" / "gmail-2026-10-01.jsonl"
    src.parent.mkdir(parents=True)
    rows = [{"id": "m1", "from": "Dana via LinkedIn <messages-noreply@linkedin.com>", "subject": "Dana sent you a message",
             "body": "Hi Alex, are you open to a chat about an AI engineer role?", "date": "2026-10-01T10:00:00Z"},
            {"id": "m2", "from": "Jane <jane@acme.example.test>", "subject": "Lunch", "body": "Lunch Friday?",
             "date": "2026-10-01T11:00:00Z"}]
    src.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = mc.classify_file(src, decider=mc.ReviewBackend(), portals=idx)
    got = {r["id"]: r.get("portal") for r in map(json.loads, out.read_text().splitlines())}
    assert got == {"m1": "linkedin", "m2": None}


def test_gmail_events_and_the_touch_carry_the_portal(root):
    idx = _platforms(root)
    row = {"id": "m3", "from": "Ari (Acme) <ari@wellfound.us>", "subject": "Acme is interested in you",
           "body": "Hi Alex, I am the founder of Acme; would you talk about our founding engineer role?",
           "date": "2026-10-01T10:00:00Z", "internal_date": "2026-10-01T10:00:00Z", "thread_id": "t3", "portal": "wellfound",
           "classification": {"label": "founder_outreach", "confidence": 0.9, "identity": {}}}
    ev = pr.gmail_events([row])[0]
    assert ev["portal"] == "wellfound"
    meta = pr.new_meta("ari-acme", "Ari", source="gmail-backlog", today=date(2026, 10, 1))
    _, touch = pr._apply(meta, "# Ari\n\n## Timeline\n", ev, date(2026, 10, 1))
    assert touch is not None and touch["portal"] == "wellfound"


def test_a_touch_without_a_portal_has_no_portal_key(root):
    ev = pr._event(key="gm-9", at="2026-10-01T10:00:00Z", direction="in", channel="email", label="recruiter_outreach",
                   text="hello", summary="hello", thread_id="t9")
    meta = pr.new_meta("x-y", "X Y", source="gmail-backlog", today=date(2026, 10, 1))
    _, touch = pr._apply(meta, "# X\n\n## Timeline\n", ev, date(2026, 10, 1))
    assert touch is None or "portal" not in touch


# ----------------------------------------------------------------------------- one source: portal_registry
def test_the_index_reads_its_senders_through_portal_registry(root, monkeypatch):
    import portal_registry as preg
    _platforms(root)
    monkeypatch.setattr(preg, "board_domains", lambda r, boards_only=True: {"zz-board": {"zz.example"}})
    idx = mc.portal_index(root)
    assert mc.portal_for("a@zz.example", idx) == "zz-board"


@pytest.mark.real_records
def test_the_index_agrees_with_portal_for_sender_on_the_real_registry():
    import portal_registry as preg
    from js_common import JS_ROOT
    idx = mc.portal_index(JS_ROOT)
    for sender in sorted({s for ss in preg.board_domains(JS_ROOT, boards_only=False).values() for s in ss}):
        addr = sender if "@" in sender else f"someone@{sender}"
        assert mc.portal_for(addr, idx) == preg.portal_for_sender(JS_ROOT, addr), addr
