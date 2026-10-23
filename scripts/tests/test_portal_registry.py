"""portal_registry.py: targets/platforms.yaml is the one source per portal: the presence tracker, inbox
senders and alert senders all come from it; state/presence/portals.yaml is read only as a legacy fallback."""
from __future__ import annotations

import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal_registry as pr  # noqa: E402
import presence_check as pc  # noqa: E402

REGISTRY = """
platforms:
  - id: linkedin
    name: LinkedIn Jobs
    url: https://www.linkedin.com/jobs/
    access: [email-alert, browser]
    control: alert-mail
    alert_senders: [jobalerts-noreply@linkedin.com]
    inbox_senders: [messages-noreply@linkedin.com, invitations@linkedin.com]
    status: probation
    presence:
      kind: profile-network
      url: https://www.linkedin.com/in/alex-example
      account: {state: exists, email: alex@example.test}
      profile: {state: current, checked_on: '2026-09-27'}
      next_refresh: '2026-09-20'
      inbox: {in_app: linkedin.com/messaging, last_swept: '2026-09-27'}
  - id: boardone
    name: BoardOne
    url: https://boardone.test/jobs
    access: [email-alert]
    control: alert-mail
    alert_senders: [boardone.test]
    inbox_senders: [boardone.example.test]
    status: probation
  - id: x
    name: X
    url: https://x.com/
    access: [api]
    control: api
    alert_senders: []
    status: probation
presence_base_resume: {iteration: 72, pdf_sha256: abc}
presence_surfaces:
  - id: github
    name: GitHub
    kind: code
    account: {state: exists}
    next_refresh: '2026-12-01'
"""


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", REGISTRY)
    return tmp_path


def test_the_tracker_is_composed_from_the_registry(root):
    t = pr.tracker(root)
    assert t["source"] == "targets/platforms.yaml"
    assert [s["id"] for s in t["surfaces"]] == ["linkedin", "github"]       # only entries with presence, then surfaces
    li = t["surfaces"][0]
    assert li["url"] == "https://www.linkedin.com/in/alex-example" and li["name"] == "LinkedIn Jobs"
    assert {x["sender"] for x in li["inbox"]["email_senders"]} == {
        "jobalerts-noreply@linkedin.com", "messages-noreply@linkedin.com", "invitations@linkedin.com"}
    assert t["base_resume"] == {"iteration": 72, "pdf_sha256": "abc"}
    assert pr.surface_ids(root) == {"linkedin", "github"}


def test_the_legacy_tracker_is_read_only_when_the_registry_has_no_presence_data(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", "platforms: []\n")
    w(tmp_path / "state" / "presence" / "portals.yaml", "surfaces:\n  - {id: old, next_refresh: '2026-01-01'}\n")
    t = pr.tracker(tmp_path)
    assert t["source"] == "state/presence/portals.yaml" and [s["id"] for s in t["surfaces"]] == ["old"]
    w(tmp_path / "state" / "presence" / "portals.yaml", "moved_to: targets/platforms.yaml\n")
    assert pr.tracker(tmp_path)["surfaces"] == []
    with pytest.raises(FileNotFoundError):
        pr.tracker(tmp_path / "nowhere")


def test_portal_for_sender_matches_addresses_and_registrable_domains(root):
    assert pr.portal_for_sender(root, "jobalerts-noreply@linkedin.com") == "linkedin"
    assert pr.portal_for_sender(root, "Messages-NoReply@LinkedIn.com") == "linkedin"
    assert pr.portal_for_sender(root, "team@hi.boardone.test") == "boardone"           # a domain entry covers subdomains
    assert pr.portal_for_sender(root, "jane@boardone.example.test") == "boardone"
    assert pr.portal_for_sender(root, "someone@example.com") is None
    assert pr.board_domains(root)["boardone"] == {"boardone.test", "boardone.example.test"}


def test_presence_check_reads_the_registry(root):
    flags, notes, data = pc.check(root, date(2026, 9, 28))
    assert any(f.startswith("linkedin: refresh due") for f in flags)
    assert data["source"] == "targets/platforms.yaml"


def test_board_domains_leave_out_ats_feeds_portfolios_and_connectors(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", """
        platforms:
          - {id: company-ats, control: api, alert_senders: [jobs2web.com]}
          - {id: vc-portfolio, control: alert-mail, alert_senders: [getro.com]}
          - {id: indeed-connector, control: connector, alert_senders: []}
          - {id: indeed, control: alert-mail, alert_senders: [indeed.com], inbox_senders: [indeedemail.com]}
        """)
    assert pr.board_domains(tmp_path) == {"indeed": {"indeed.com", "indeedemail.com"}}
    assert set(pr.board_domains(tmp_path, boards_only=False)) == {"company-ats", "vc-portfolio", "indeed"}
    assert pr.portal_for_sender(tmp_path, "alerts@jobs2web.com") == "company-ats"   # tagging still sees every portal


# ----------------------------------------------------------------------------- writers 
FULL = """# header comment stays
platforms:
  - id: linkedin
    name: LinkedIn
    alert_senders: []

# Platforms seen but not yet vetted.
candidates:
  - id: old-cand
    name: Old

blocked_tools:
  - {id: lazyapply, name: LazyApply}
"""

NEW = {"id": "aidevjobs", "name": "AI Dev Jobs", "url": "https://aidevboard.com", "access": ["api"], "control": "api",
       "adapter": None, "alert_senders": [], "tos": {"quote": "terms not read", "url": "https://aidevboard.com/terms",
       "read_on": "unread", "verified": "no"}, "agent_terms": "names-agents", "sponsorship_filter": "none",
       "account_needed": False, "alert_setup": "None.", "setup_priority": "P1", "alerts_on": None, "status": "manual",
       "yield": {"fetched": 0, "new": 0, "queued": 0, "applied": 0, "interviews": 0}, "yield_as_of": None,
       "last_reviewed": "2026-10-01", "notes": "census row 34"}


def test_add_platform_appends_an_entry_keeping_comments_and_refuses_duplicates(tmp_path):
    p = w(tmp_path / "targets" / "platforms.yaml", FULL)
    pr.add_platform(tmp_path, NEW)
    text = p.read_text()
    assert text.startswith("# header comment stays") and "# Platforms seen but not yet vetted." in text
    data = yaml.safe_load(text)
    assert [e["id"] for e in data["platforms"]] == ["linkedin", "aidevjobs"]
    assert data["platforms"][1]["tos"]["read_on"] == "unread" and data["candidates"][0]["id"] == "old-cand"
    with pytest.raises(ValueError, match="already"):
        pr.add_platform(tmp_path, NEW)
    with pytest.raises(ValueError, match="missing"):
        pr.add_platform(tmp_path, {"id": "half", "name": "Half"})


def test_add_candidate_and_blocked_go_to_their_sections(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", FULL)
    pr.add_candidate(tmp_path, {"id": "usajobs", "name": "USAJOBS", "url": "https://www.usajobs.gov",
                                "why": "SKIP: federal roles need US citizenship", "access_guess": ["api"],
                                "checked_on": "2026-10-01", "next_step": "none"})
    pr.add_blocked(tmp_path, {"id": "teal", "name": "Teal", "agent_terms": "gateway-only", "reason": "SKIP: tracker"})
    data = pr.load(tmp_path)
    assert [c["id"] for c in data["candidates"]] == ["old-cand", "usajobs"]
    assert [b["id"] for b in data["blocked_tools"]] == ["lazyapply", "teal"]
    with pytest.raises(ValueError, match="already"):
        pr.add_candidate(tmp_path, {"id": "linkedin", "name": "x", "url": "u", "why": "w", "access_guess": [],
                                    "checked_on": "2026-10-01", "next_step": "n"})


# Recruiter firms and talent networks are channels in the one registry (presence.kind recruiter-firm / talent-network).


def test_the_live_registry_carries_every_firm_as_a_platform_with_its_kind_and_fraud_note():
    from js_common import JS_ROOT
    data = pr.load(JS_ROOT)
    platforms = {e["id"]: e for e in data["platforms"]}
    firms = {pid: e for pid, e in platforms.items() if (e.get("presence") or {}).get("kind") in pr.FIRM_KINDS}
    if not firms:
        pytest.skip("the data folder's registry carries no recruiter firm or talent network")
    surfaces = {e.get("id") for e in data.get("presence_surfaces") or []}
    assert not (set(firms) & surfaces)                                                    # one source per portal
    for pid, e in firms.items():
        pres = e["presence"]
        assert e["who_pays"] and e["url"].startswith("https://")
        assert pres["account"]["state"] in (None, "exists") and pres["registration"]["state"] is None
        assert "never pays" in e["fraud_filter"] and "SSN" in e["fraud_filter"]
        assert e.get("inbox_senders") or e.get("sender_note") == "confirm on first mail"
        assert e["setup_priority"] == "P1" and e["tos"]["quote"]
        for sender in e.get("inbox_senders") or []:
            if "@" not in str(sender):
                assert pr.portal_for_sender(JS_ROOT, f"x@{sender}") == pid


def test_board_domains_never_count_recruiter_firms_or_talent_networks_as_job_boards(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", """
        platforms:
          - {id: talentfirm, control: manual, inbox_senders: [talentfirm.example.test], presence: {kind: recruiter-firm}}
          - {id: meetup-network, control: manual, inbox_senders: [meetup.example.test], presence: {kind: talent-network}}
          - {id: indeed, control: alert-mail, alert_senders: [indeed.com]}
        """)
    assert pr.board_domains(tmp_path) == {"indeed": {"indeed.com"}}
    assert pr.portal_for_sender(tmp_path, "jane@talentfirm.example.test") == "talentfirm"            # still tagged


def test_add_platform_never_writes_yaml_anchors_for_a_shared_value(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "platforms.yaml").write_text(FULL)
    day = date(2026, 10, 1)
    for pid in ("one", "two"):
        pr.add_platform(tmp_path, {**NEW, "id": pid, "last_reviewed": day,
                                   "tos": {**NEW["tos"], "read_on": day}})
    text = (tmp_path / "targets" / "platforms.yaml").read_text()
    assert "&id" not in text and "*id" not in text
    assert [e["id"] for e in yaml.safe_load(text)["platforms"]] == ["linkedin", "one", "two"]
