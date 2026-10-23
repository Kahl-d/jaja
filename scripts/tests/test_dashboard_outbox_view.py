"""Approved outbox items that have not gone out sit on a list, each with its owner.
- Approved LinkedIn items (connect, message, profile edit) not sent: the owner pastes and sends them through the
  linkedin-assist hand-off, so they are Needs Alex: "To send on LinkedIn: N", capped at 10, each O-id and person.
- Approved email not sent: the sender's backlog, "sender: N approved, not sent".
- Drafts in an approval rejected for redraft (decided_via says "redraft requested") that no newer draft supersedes:
  the messenger's backlog, "messenger: N to redraft".
- The pointer to hub/plans/APPROVALS-REVIEW.md shows only when that list names at least one approval still pending.
Fixture tree only (test_dashboard_status's)."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

from test_dashboard_status import _write, populate, run  # noqa: E402


def _status(root):
    text = (root / "DASHBOARD.md").read_text()
    return text, text[text.index("## Status"):text.index("## Needs Alex")]


def _section(text, name):
    return text[text.index(f"## {name}"):].split("\n## ", 1)[0]


def _ob(root, oid, channel, status, approval, prospect="ann", sent_at=None, supersedes=None):
    _write(root / "outbox" / f"{oid}.md", {"type": "outbox", "id": oid, "channel": channel, "prospect": prospect,
                                           "status": status, "approval": approval, "sent_at": sent_at,
                                           "supersedes": supersedes})


def _appr(os_root, aid, status, decided_via=""):
    _write(os_root / "work" / "approvals" / f"{aid}.md",
           {"id": aid, "action": "send-email", "status": status, "project": "job-search", "created": "2026-09-23",
            "expires": "2026-09-30", "decided_via": decided_via})


def test_approved_linkedin_items_split_by_what_the_owner_can_do_now(root, os_root):
    """Review finding 1: profile edits are applied, connection requests and ready messages are sent now, a message to
    someone whose connection request is still unsent waits on the accept, and one whose checks carry a `trigger:` waits
    on that trigger; the count shown is what can be done today."""
    populate(root, os_root)
    _appr(os_root, "A-2026-0040", "approved")
    _ob(root, "O-2026-0040", "linkedin-connect", "approved", "A-2026-0040", prospect="carson-kahn")
    _ob(root, "O-2026-0041", "linkedin-message", "approved", "A-2026-0040", prospect="carson-kahn")   # after accept
    _ob(root, "O-2026-0043", "linkedin-message", "approved", "A-2026-0040", prospect="mo")            # ready now
    _ob(root, "O-2026-0044", "linkedin-profile-edit", "approved", "A-2026-0040", prospect=None)       # apply
    _write(root / "outbox" / "O-2026-0045.md", {"type": "outbox", "id": "O-2026-0045", "channel": "linkedin-message",
                                                "prospect": "ford", "status": "approved", "approval": "A-2026-0040",
                                                "checks": ["trigger: M0, OPTIONAL: send only if the owner wants it"]})
    _ob(root, "O-2026-0042", "linkedin-message", "sent", "A-2026-0040", sent_at="2026-09-24T10:00:00Z")
    run(root, os_root)
    text, section = _status(root)
    needs = _section(text, "Needs Alex")
    assert ("- LinkedIn for Alex to paste and send (the linkedin-assist hand-off): 1 profile edit to apply, "
            "1 connection request, 1 message ready now; 2 messages wait (1 on an accepted connection, 1 on a trigger); "
            "the approvals expire from 2026-09-30") in needs
    assert "  - O-2026-0040 · linkedin-connect · carson-kahn" in needs and "  - O-2026-0043 · linkedin-message · mo" in needs
    assert "O-2026-0041" not in needs.split("wait", 1)[0] and "O-2026-0042" not in needs
    owner_line = next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))
    assert "LinkedIn to apply or send now 3 (2 more waiting; approvals expire from 2026-09-30)" in owner_line
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    assert any(n["title"] == "LinkedIn: 3 approved items to apply or send (approvals expire from 2026-09-30)"
               for n in feed["needs_you"])


def test_an_expired_or_foreign_approval_is_not_the_owners_to_send(root, os_root):
    """Review finding 3: an outbox item is sendable only while its approval is approved, job-search and not expired."""
    populate(root, os_root)
    _write(os_root / "work" / "approvals" / "A-2026-0041.md",
           {"id": "A-2026-0041", "action": "send", "status": "approved", "project": "job-search", "expires": "2026-09-20"})
    _write(os_root / "work" / "approvals" / "A-2026-0042.md",
           {"id": "A-2026-0042", "action": "post", "status": "approved", "project": "brand", "expires": "2026-12-01"})
    _ob(root, "O-2026-0050", "linkedin-connect", "approved", "A-2026-0041", prospect="ex")
    _ob(root, "O-2026-0051", "linkedin-connect", "approved", "A-2026-0042", prospect="br")
    run(root, os_root)
    text, _ = _status(root)
    assert "LinkedIn for Alex" not in _section(text, "Needs Alex")
    backlog = _section(text, "Agency backlog")
    assert "- Approved items whose approval expired (messenger: re-request): 1" in backlog and "O-2026-0050" in backlog
    assert "- Approved items whose approval is not a job-search approval (messenger: re-file): 1" in backlog


def test_approved_email_is_the_senders_and_redrafts_are_messengers(root, os_root):
    populate(root, os_root)
    _appr(os_root, "A-2026-0050", "approved")
    _ob(root, "O-2026-0050", "email", "approved", "A-2026-0050")
    _appr(os_root, "A-2026-0060", "rejected", "chat; redraft requested (untrue or stale line)")
    _ob(root, "O-2026-0060", "email", "rejected", "A-2026-0060", prospect="bo")
    _ob(root, "O-2026-0061", "email", "rejected", "A-2026-0060", prospect="cy")
    _ob(root, "O-2026-0062", "email", "drafted", None, prospect="cy", supersedes="O-2026-0061")   # redrafted
    _appr(os_root, "A-2026-0070", "rejected", "chat; drop")
    _ob(root, "O-2026-0070", "email", "rejected", "A-2026-0070", prospect="di")               # dropped: no list
    run(root, os_root)
    text, section = _status(root)
    backlog = _section(text, "Agency backlog")
    assert "- Approved email the sender has not sent: 1" in backlog and "O-2026-0050" in backlog
    assert "- Drafts rejected for redraft (messenger: redraft and file again): 1" in backlog
    assert "O-2026-0060" in backlog and "O-2026-0061" not in backlog and "O-2026-0070" not in backlog
    agency = next(ln for ln in section.splitlines() if ln.startswith("- Agency backlog"))
    assert "sender 1 approved, not sent" in agency and "1 to redraft" in agency and "messenger " in agency
    assert "O-2026-0050" not in _section(text, "Needs Alex")
    data = json.loads((root / "state" / "dashboard.json").read_text())
    assert data["needs_owner"]["linkedin"]["ready"] == []
    assert [d["id"] for d in data["agency_backlog"]["email_approved_unsent"]] == ["O-2026-0050"]
    assert [d["id"] for d in data["agency_backlog"]["drafts_to_redraft"]] == ["O-2026-0060"]


def test_the_review_pointer_reads_the_short_ids_the_list_uses(root, os_root):
    """Review finding 2: APPROVALS-REVIEW.md writes ids short ("A-0001")."""
    populate(root, os_root)
    _write(root / "hub" / "plans" / "APPROVALS-REVIEW.md", {"title": "approvals review"}, "| A-0001 | approve |\n")
    run(root, os_root)
    _, section = _status(root)
    assert "approvals 1 (hub/plans/APPROVALS-REVIEW.md)" in section


def test_another_channel_is_not_email(root, os_root):
    """Review finding 5: an approved item on a channel that is neither LinkedIn nor email is its own row."""
    populate(root, os_root)
    _appr(os_root, "A-2026-0040", "approved")
    _ob(root, "O-2026-0060", "x-dm", "approved", "A-2026-0040", prospect="zed")
    run(root, os_root)
    backlog = _section((root / "DASHBOARD.md").read_text(), "Agency backlog")
    assert "Approved email" not in backlog
    assert "- Approved items on another channel the sender has not sent: 1" in backlog and "O-2026-0060 · x-dm" in backlog


def test_the_review_pointer_needs_a_pending_id_on_the_list(root, os_root):
    populate(root, os_root)                                    # A-2026-0001 is pending in the fixture
    _write(root / "hub" / "plans" / "APPROVALS-REVIEW.md", {"title": "approvals review"},
           "# list\n- A-2026-0040 approve\n")                  # names only a decided approval
    run(root, os_root)
    text, section = _status(root)
    assert "APPROVALS-REVIEW" not in section and "APPROVALS-REVIEW" not in _section(text, "Needs Alex")
    _write(root / "hub" / "plans" / "APPROVALS-REVIEW.md", {"title": "approvals review"},
           "# list\n- A-2026-0001 approve\n")                  # names a pending one
    run(root, os_root)
    text, section = _status(root)
    assert "approvals 1 (hub/plans/APPROVALS-REVIEW.md)" in section


def test_the_linkedin_list_is_capped(root, os_root):
    import dashboard as db
    populate(root, os_root)
    _appr(os_root, "A-2026-0040", "approved")
    for n in range(12):
        _ob(root, f"O-2026-{n + 100:04d}", "linkedin-connect", "approved", "A-2026-0040", prospect=f"p{n}")
    run(root, os_root)
    needs = _section((root / "DASHBOARD.md").read_text(), "Needs Alex")
    assert needs.count("  - O-2026-") == db.STATUS_CAP and "and 2 more" in needs
