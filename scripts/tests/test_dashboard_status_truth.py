"""The status surface says only true things to its reader.
- "Waiting on Alex" lists only what needs him (approvals, offers, mail awaiting his call). Gate reviews are the
  fit-scorer's work (it settles review gates), so they move to an agency backlog line in plain words, out of the owner's
  line, out of the Needs Alex section and out of the HOME feed's needs_you.
- Mail review never says a bare "no data yet": it names when mail-triage last ran (its receipt), or that it has not
  run (the hourly routine is off; the pass runs when the owner starts the daily mail check), and when Gmail was
  last pulled.
- The queue line counts what is overdue (due before today) and names the oldest due date.
Fixture tree only (test_dashboard_status's)."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

from test_dashboard_status import _app, _write, populate, run  # noqa: E402


def _gate_review_job(root, jid="acme-job-1"):
    _write(root / "jobs" / f"{jid}.md", {"id": jid, "company": "Acme", "title": "AI Engineer", "status": "discovered",
                                         "gate": "review", "gate_reasons": ["location: not stated"]}, "# job\n")


def _status(root):
    text = (root / "DASHBOARD.md").read_text()
    return text, text[text.index("## Status"):text.index("## Needs Alex")]


def test_gate_reviews_are_the_fit_scorers_backlog_not_the_owners(root, os_root):
    populate(root, os_root)
    _gate_review_job(root)
    _gate_review_job(root, "acme-job-2")
    run(root, os_root)
    text, section = _status(root)
    owner_line = next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))
    assert "gate review" not in owner_line
    assert "- Agency backlog (the roles' own work, nothing for Alex to do): fit-scorer 2 gate reviews to settle" in section
    needs = text[text.index("## Needs Alex"):].split("\n## ", 1)[0]
    assert "Gate reviews" not in needs and "a gate needs your call" not in needs
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    assert not any("Gate review" in n["title"] for n in feed["needs_you"])
    st = json.loads((root / "state" / "dashboard.json").read_text())["status"]
    assert "gate_reviews" not in st["waiting_on"]["owner"] and st["agency_backlog"]["gate_reviews"] == 2


def test_mail_review_says_mail_triage_has_not_run(root, os_root):
    populate(root, os_root)
    run(root, os_root)
    _, section = _status(root)
    assert "routine's state is not on record (routines/js-mail-triage.yaml)" in section     # no routine file
    (root / "routines").mkdir()
    (root / "routines" / "js-mail-triage.yaml").write_text("name: js-mail-triage\nenabled: false\n", encoding="utf-8")
    run(root, os_root)
    _, section = _status(root)
    owner_line = next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))
    assert "no data yet" not in owner_line
    assert ("mail review: mail-triage has not run yet (no receipt in logs/runs/js-mail-triage/; the hourly "
            "routine is off, so it runs when Alex starts the daily mail check)") in owner_line


def test_mail_review_names_the_last_run_and_the_last_gmail_pull(root, os_root):
    populate(root, os_root)
    rec = os_root / "logs" / "runs" / "js-mail-triage" / "2026-09-23.md"
    rec.parent.mkdir(parents=True)
    rec.write_text("# js-mail-triage 2026-09-23\n", encoding="utf-8")
    _write(root / "state" / "review" / "mail-2026-09-23.md", {"type": "mail-review"},
           "## items\n- id: m-1\n  decision:\n")
    (root / "state" / "harvest").mkdir(parents=True)
    (root / "state" / "harvest" / "gmail.state.json").write_text(json.dumps(
        {"runs": [{"at": "2026-09-23T22:39:46+00:00", "new": 143}]}), encoding="utf-8")
    run(root, os_root)
    _, section = _status(root)
    owner_line = next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))
    assert "mail review 1 open: mail-triage last ran 2026-09-23 (logs/runs/js-mail-triage/2026-09-23.md)" in owner_line
    assert "Gmail last pulled 2026-09-23 15:39 PT: 143 new messages harvested" in owner_line


def test_the_queue_counts_what_is_overdue(root, os_root):
    populate(root, os_root)                                    # acme is due 2026-09-24, the fixture's today
    _app(root, "old-ai-202609", "Old", [("2026-09-20T10:00:00Z", "discovered")], next_action_at="2026-09-20",
         next_action="score fit (fit-scorer)")
    run(root, os_root)
    _, section = _status(root)
    assert "- Next actions due (2: 1 past due, oldest 2026-09-20; 1 due today) (applying is paused: steps before applying wait):" in section
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    row = next(r for r in feed["today"] if r["title"].startswith("Job search:"))
    assert row["title"] == "Job search: 2 next actions due (1 past due) while applying is paused, 1 waiting on companies"


def test_needs_owner_says_the_mail_truth_too(root, os_root):
    """DASHBOARD.md's Needs Alex section never says a bare "no data yet" for mail."""
    populate(root, os_root)
    run(root, os_root)
    text, _ = _status(root)
    needs = text[text.index("## Needs Alex"):].split("\n## ", 1)[0]
    mail = next(ln for ln in needs.splitlines() if ln.startswith("- Mail review"))
    assert "no data yet" not in mail and mail.startswith("- Mail review: mail-triage has not run yet")


def test_gate_reviews_leave_needs_owner_in_the_data_and_the_mcp_view(root, os_root):
    """build() keeps gate reviews under agency_backlog, so no reader (the MCP dashboard tool
    included) can tell the owner they owe them."""
    import dashboard as db
    from disc_harness import NOW
    populate(root, os_root)
    _gate_review_job(root)
    data = db.build(root, os_root, NOW)
    assert "gate_reviews" not in data["needs_owner"] and len(data["agency_backlog"]["gate_reviews"]) == 1


def test_mail_line_edges():
    """missing is not empty; harvested is not triaged; Pacific time."""
    import dashboard as db
    run = {"receipt": "logs/runs/js-mail-triage/2026-09-22.md", "last_run": "2026-09-22",
           "routine_enabled": False, "gmail_pulled": "2026-09-24T05:39:46+00:00", "gmail_new": 143}
    line = db.mail_line(None, run)
    assert "no review file yet" in line and "0 open" not in line
    assert "Gmail last pulled 2026-09-23 22:39 PT: 143 new messages harvested, not yet triaged" in line
    same_day = {**run, "last_run": "2026-09-23", "receipt": "logs/runs/js-mail-triage/2026-09-23.md"}  # can't order
    assert "not yet triaged" not in db.mail_line(2, same_day) and "mail review 2 open" in db.mail_line(2, same_day)


def test_mail_run_reads_only_dated_receipts_and_a_real_enabled_flag(root, os_root):
    import dashboard as db
    folder = os_root / "logs" / "runs" / "js-mail-triage"
    folder.mkdir(parents=True)
    assert db.mail_run(root, os_root)["last_run"] is None                   # an empty folder: not run
    (folder / "2026-09-23.md").write_text("r\n", encoding="utf-8")
    (folder / "README.md").write_text("about\n", encoding="utf-8")
    assert db.mail_run(root, os_root)["last_run"] == "2026-09-23"
    (root / "routines").mkdir()
    (root / "routines" / "js-mail-triage.yaml").write_text("name: js-mail-triage\n", encoding="utf-8")
    assert db.mail_run(root, os_root)["routine_enabled"] is None             # no enabled key: not on record
    (root / "state" / "harvest").mkdir(parents=True)
    (root / "state" / "harvest" / "gmail.state.json").write_text("{not json", encoding="utf-8")
    assert db.mail_run(root, os_root)["gmail_pulled"] is None


# ----------------------------------------------------------------------------- drafts are not the owner's, approvals are
def _drafts(root, os_root):
    _write(root / "outbox" / "O-2026-0001.md", {"type": "outbox", "id": "O-2026-0001", "channel": "email",
                                                "prospect": "ann", "status": "pending", "approval": "A-2026-0001"})
    _write(root / "outbox" / "O-2026-0002.md", {"type": "outbox", "id": "O-2026-0002", "channel": "linkedin-message",
                                                "prospect": "bob", "status": "drafted", "approval": None})
    _write(root / "outbox" / "O-2026-0003.md", {"type": "outbox", "id": "O-2026-0003", "channel": "email",
                                                "prospect": "cy", "status": "pending", "approval": "A-2026-0099"})
    _write(os_root / "work" / "approvals" / "A-2026-0099.md",
           {"id": "A-2026-0099", "action": "send-email", "status": "expired", "project": "job-search",
            "created": "2026-09-10", "expires": "2026-09-17"})
    _write(root / "hub" / "plans" / "APPROVALS-REVIEW.md", {"title": "approvals review"},
           "# list\n| A-0001 | approve |\n")      # the pointer shows only when the list names a pending approval


def test_needs_owner_shows_approvals_not_drafts(root, os_root):
    populate(root, os_root)
    _drafts(root, os_root)
    run(root, os_root)
    text, section = _status(root)
    needs = text[text.index("## Needs Alex"):].split("\n## ", 1)[0]
    assert "- Approvals waiting: 1 (they hold 1 outbox draft; One-pass list for Alex: hub/plans/APPROVALS-REVIEW.md)" \
        in needs
    assert "Outbox drafts" not in needs and "O-2026-0001" not in needs and "O-2026-0002" not in needs
    backlog = text[text.index("## Agency backlog"):].split("\n## ", 1)[0]
    assert "- Drafts for messenger to file for approval: 1" in backlog and "O-2026-0002" in backlog
    assert "- Drafts whose approval is no longer pending (expired; messenger: re-file or close): 1" in backlog
    assert "O-2026-0003" in backlog
    owner_line = next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))
    assert "approvals 1 (hub/plans/APPROVALS-REVIEW.md)" in owner_line
    agency = next(ln for ln in section.splitlines() if ln.startswith("- Agency backlog"))
    assert "messenger 1 draft not yet filed for approval" in agency and "1 draft whose approval is no longer pending" in agency
    data = json.loads((root / "state" / "dashboard.json").read_text())
    assert "outbox" not in data["needs_owner"]
    assert [d["id"] for d in data["agency_backlog"]["drafts_unfiled"]] == ["O-2026-0002"]
    assert [d["id"] for d in data["agency_backlog"]["drafts_not_pending"]] == ["O-2026-0003"]


def test_the_approvals_list_is_capped_and_the_pointer_needs_the_file(root, os_root):
    import dashboard as db
    populate(root, os_root)
    for n in range(2, 14):
        _write(os_root / "work" / "approvals" / f"A-2026-{n:04d}.md",
               {"id": f"A-2026-{n:04d}", "action": "send-email", "status": "pending", "project": "job-search",
                "created": "2026-09-23", "expires": "2026-09-30"})
    run(root, os_root)
    text, section = _status(root)
    needs = text[text.index("## Needs Alex"):].split("\n## ", 1)[0]
    assert needs.count("  - A-2026-") == db.STATUS_CAP and "and 3 more" in needs
    assert "APPROVALS-REVIEW" not in needs and "APPROVALS-REVIEW" not in section       # no file, no pointer


def test_draft_edges_approved_unsynced_foreign_and_missing_approvals(root, os_root):
    """an approved approval whose draft is still open is the sender's (apply-decisions has not
    synced it), never 'lapsed'; a draft citing another project's pending approval is not counted as the owner's; a draft
    naming a missing approval file says so with the id."""
    populate(root, os_root)
    _write(os_root / "work" / "approvals" / "A-2026-0050.md",
           {"id": "A-2026-0050", "action": "send-email", "status": "approved", "project": "job-search"})
    _write(os_root / "work" / "approvals" / "A-2026-0060.md",
           {"id": "A-2026-0060", "action": "post", "status": "pending", "project": "brand", "created": "2026-09-23"})
    for oid, appr in (("O-2026-0010", "A-2026-0050"), ("O-2026-0011", "A-2026-0060"), ("O-2026-0012", "A-2026-0077")):
        _write(root / "outbox" / f"{oid}.md", {"type": "outbox", "id": oid, "channel": "email", "prospect": "x",
                                               "status": "pending", "approval": appr})
    run(root, os_root)
    text, section = _status(root)
    needs = text[text.index("## Needs Alex"):].split("\n## ", 1)[0]
    assert "they hold" not in needs                                   # no job-search pending approval holds them
    backlog = text[text.index("## Agency backlog"):].split("\n## ", 1)[0]
    assert "- Approved drafts the outbox has not synced (sender; run apply-decisions): 1" in backlog
    assert "O-2026-0010 · email · x · approval A-2026-0050" in backlog
    assert "- Drafts whose approval is no longer pending (pending in another project; messenger: re-file or close): 1" \
        in backlog
    assert "- Drafts naming a missing approval file (messenger: re-file): 1" in backlog and "A-2026-0077" in backlog
    agency = next(ln for ln in section.splitlines() if ln.startswith("- Agency backlog"))
    assert "lapsed" not in agency and "sender 1 approved draft not yet synced" in agency


def test_the_draft_lists_are_capped(root, os_root):
    import dashboard as db
    populate(root, os_root)
    for n in range(12):
        _write(root / "outbox" / f"O-2026-{n + 100:04d}.md", {"type": "outbox", "id": f"O-2026-{n + 100:04d}",
                                                              "channel": "email", "prospect": "y", "status": "drafted"})
    run(root, os_root)
    text, _ = _status(root)
    backlog = text[text.index("## Agency backlog"):].split("\n## ", 1)[0]
    assert backlog.count("  - O-2026-") == db.STATUS_CAP and "and 2 more" in backlog
