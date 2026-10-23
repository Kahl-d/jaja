"""The sheet rules that are code: every line a sheet shows is read
from a record, never typed. The LinkedIn profile row counts the approval's items applied and left (outbox sent_at);
the data export, a sitting note and a per-employer tenant list come from registry fields; the profile text points at
the kit (its section 6 note for the portal, else sections 1 to 5) and the kit's focus wins over the lens data; a
portal the kit says needs no profile text gets none; an account already on record gets no --created-on. Fixture tree
only."""
from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import portal_sitting as ps  # noqa: E402

DAY = date(2026, 10, 2)

PLATFORMS = """
platforms:
  - id: linkedin
    name: LinkedIn Jobs
    control: alert-mail
    setup_priority: P0
    adapter: scripts/job_alerts.py
    alert_senders: [jobalerts-noreply@linkedin.com]
    data_export: "https://www.linkedin.com/mypreferences/d/download-my-data: Alex clicks Request"
    presence: {account: {state: exists}, alerts: {state: arriving},
               linked: {approval: "A-2026-0001 (3 items; generated from LINKEDIN.md)"}}
  - id: wellfound
    name: Wellfound (formerly AngelList Talent)
    control: alert-mail
    setup_priority: P0
    presence: {account: {state: exists}, alerts: {state: "off"}}
  - id: hiringcafe
    name: HiringCafe
    control: alert-mail
    setup_priority: P1
  - id: examplesearch
    name: Example Search
    control: manual
    setup_priority: P1
    presence: {kind: recruiter-firm}
  - id: bigco-careers
    name: Bigco Careers
    control: manual
    setup_priority: P1
    sitting_note: "Not a sitting for this owner: the lead owns the Bigco profile"
    alert_setup: Create a Career Profile and turn on alerts
  - id: yc
    name: Y Combinator Work at a Startup
    control: alert-mail
    setup_priority: P0
    presence: {account: {state: unknown}}
  - id: workday-candidate-home
    name: Workday Candidate Home (per employer)
    control: manual
    setup_priority: P1
    url: https://doc.workday.com
    per_tenant: workday
"""

KIT = """---
title: Portal profile kit
focus: profile/lenses/ai-engineer.md section 7 (the default lens)
---
# Portal profile kit

## 1. Headline
x

## 6. Per-portal notes

**Wellfound.** Use the short bio.

**Hiring Cafe.** A search tool: no profile text needed. Saved searches per the sheet.

**Recruiter firms** The intro paragraph.

## 7. Salary fields
y
"""


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "workspace" / "domains" / "career" / "projects" / "job-search"
    w(root / "targets" / "platforms.yaml", PLATFORMS)
    w(root / "state" / "portal-sittings" / "_PROFILE-KIT.md", KIT)
    w(root / "state" / "accounts.yaml", "accounts: []\n")
    w(root / "targets" / "companies.yaml", """
        companies:
          - {name: Globex, slug: globex, tier: dream, ats_vendor: workday,
             board_token: globex.wd5.myworkdayjobs.com/globex/GlobexExternalCareerSite}
          - {name: Visa, slug: visa, tier: good, ats_vendor: workday, board_token: visa.wd5.myworkdayjobs.com/visa/Visa}
          - {name: Oldco, slug: oldco, tier: fallback, ats_vendor: workday, board_token: old.wd1.myworkdayjobs.com/old/Jobs}
          - {name: Acme, slug: acme, tier: dream, ats_vendor: greenhouse, board_token: acme}
        """)
    w(tmp_path / "workspace" / "work" / "approvals" / "A-2026-0001.md", """
        ---
        type: approval
        id: A-2026-0001
        status: approved
        expires: 2026-10-05
        items:
        - id: O-2026-0001
          channel: linkedin-profile-edit
        - id: O-2026-0002
          channel: linkedin-profile-edit
        - id: O-2026-0003
          channel: linkedin-profile-edit
        ---
        # approval
        """)
    w(root / "applications" / "globex-ai-engineer-202609" / "application.md",
      "---\ntype: application\ncompany: Globex\ncompany_slug: globex\nstage: applied\napplied_via: ats\n"
      "applied_at: '2026-09-29T18:00:00Z'\n---\n")
    for oid, sent in (("O-2026-0001", "'2026-09-28T07:51:09Z'"), ("O-2026-0002", "'2026-09-28T07:52:00Z'"),
                      ("O-2026-0003", "null")):
        w(root / "outbox" / f"{oid}.md", f"---\nid: {oid}\nstatus: {'sent' if sent != 'null' else 'approved'}\n"
                                         f"sent_at: {sent}\n---\n")
    return root


def sheet(js, pid):
    ps.write(js, pid, DAY)
    return (js / "state" / "portal-sittings" / f"{pid}.md").read_text(encoding="utf-8")


def test_the_linkedin_profile_row_counts_what_was_applied_and_what_is_left(js):
    text = sheet(js, "linkedin")
    row = next(ln for ln in text.splitlines() if ln.startswith("| Profile |"))
    assert "A-2026-0001: 2 of 3 items applied 2026-09-28" in row and "1 left: O-2026-0003" in row
    assert "expires 2026-10-05" in row and "work/approvals/A-2026-0001.md" in row
    assert "LINKEDIN.md ##" not in text                          # no per-heading rows (changelog, upkeep ...)
    assert "Data export: https://www.linkedin.com/mypreferences/d/download-my-data" in text
    assert "--profile-filled-on $(date +%F)" in text and "after the 1 item left is applied" in text


def test_a_fully_applied_approval_dates_the_profile(js):
    (js / "outbox" / "O-2026-0003.md").write_text("---\nid: O-2026-0003\nstatus: sent\nsent_at: '2026-09-29T10:00:00Z'"
                                                  "\n---\n", encoding="utf-8")
    text = sheet(js, "linkedin")
    assert "A-2026-0001: 3 of 3 items applied (last 2026-09-29)" in text
    assert "--profile-filled-on 2026-09-29" in text


def test_an_account_on_record_gets_no_created_on(js):
    assert ps.status(js)["linkedin"] == "tracker linked" and ps.status(js)["wellfound"] == "account exists"
    assert "--created-on" not in sheet(js, "linkedin") and "--created-on" not in sheet(js, "wellfound")
    assert "--created-on $(date +%F)" in sheet(js, "examplesearch")   # to create: the creation date is today's


def test_the_profile_text_points_at_the_kit_and_the_kit_focus_wins(js):
    well = sheet(js, "wellfound")
    assert "TO BE DRAFTED" not in well
    assert "- Text: state/portal-sittings/_PROFILE-KIT.md section 6, 'Wellfound'" in well
    assert "the kit's focus (profile/lenses/ai-engineer.md) wins" in well
    firm = sheet(js, "examplesearch")
    assert "section 6, 'Recruiter firms'" in firm
    assert "- Text: state/portal-sittings/_PROFILE-KIT.md sections 1 to 5" in sheet(js, "yc")   # no section 6 note


def test_a_portal_the_kit_says_needs_no_profile_text_gets_none(js):
    cafe = sheet(js, "hiringcafe")
    assert "No profile text" in cafe and "_PROFILE-KIT.md section 6, 'Hiring Cafe'" in cafe
    assert "--profile-filled-on" not in cafe and "Desired salary" not in cafe


def test_a_sitting_note_opens_the_sheet(js):
    text = sheet(js, "bigco-careers")
    assert text.splitlines()[2].startswith("> Not a sitting for this owner: the lead owns the Bigco profile")


def test_the_per_employer_sheet_lists_its_tenants_and_a_safe_placeholder(js):
    text = sheet(js, "workday-candidate-home")
    assert "- Link: one Candidate Home per employer (below); Workday's help page: https://doc.workday.com" in text
    assert "Visa (good, --tenant visa): https://visa.wd5.myworkdayjobs.com/Visa" in text
    assert "Globex (dream, --tenant globex" in text and "Oldco" not in text and "Acme" not in text
    assert "<company_slug>" not in text and "--tenant COMPANY_SLUG" in text and "replace COMPANY_SLUG" in text


def test_the_linkedin_sitting_runs_the_hand_off_and_the_agent_never_types(js):
    """Profile items go through the linkedin-assist hand-off: the agent shows each item's text, the owner
    pastes and clicks, the agent reads back read-only and records outbox.py mark-sent."""
    text = sheet(js, "linkedin")
    how = next(ln for ln in text.splitlines() if ln.startswith("- How:"))
    assert ".agents/skills/linkedin-assist" in how and "the owner pastes and clicks" in how
    assert "python3 scripts/outbox.py mark-sent --id <O-id>" in how and "never types, clicks or sends" in how
    assert "- How:" not in sheet(js, "wellfound")


def test_an_approval_profile_takes_its_approved_items_never_the_kit(js):
    """Clean review 1: the LinkedIn text is the approved outbox items; kit text there would be an unapproved edit."""
    slot = sheet(js, "linkedin").split("## 3. Profile text", 1)[1].split("## 4.", 1)[0]
    assert "_PROFILE-KIT.md" not in slot and "the kit's focus" not in slot
    assert "- Text: the approved items of A-2026-0001, never the kit: 1 left (O-2026-0003)" in slot


def test_a_sitting_note_sheet_offers_no_fields_text_or_closing_command(js):
    """Clean review 2: ORCH owns that profile, so sections 2 to 5 would contradict the note."""
    text = sheet(js, "bigco-careers")
    assert "Resume to upload" not in text and "_PROFILE-KIT.md" not in text and "apply_pack.py account" not in text
    assert "Sections 2 to 5 do not apply: see the note above." in text
    assert "Create a Career Profile" not in text and "- Link:" in text      # no steps under another owner's note


def test_an_employer_with_an_applied_ats_application_has_an_account(js):
    """Clean review 3: a Workday application proves the Candidate Home account; never a fresh creation date."""
    text = sheet(js, "workday-candidate-home")
    assert "Globex (dream, --tenant globex, account exists: applied 2026-09-29)" in text
    assert "Visa (good, --tenant visa)" in text
    assert '--company "COMPANY_NAME"' in text and "omit --created-on for an employer marked account exists" in text


def test_an_unknown_account_state_conditions_the_creation_date(js):
    """Clean review 4: with the account state unknown, --created-on only if the account was made in this sitting."""
    assert "(only if the account was created in this sitting; else omit --created-on)" in sheet(js, "yc")
    assert "only if the account was created" not in sheet(js, "examplesearch")   # registration to make: the date is today's


def test_a_relative_root_works(js, monkeypatch):
    """Clean review 5: --root . must not crash on the approval lookup."""
    monkeypatch.chdir(js)
    from pathlib import Path as P
    assert "A-2026-0001: 2 of 3 items applied" in ps.render(P("."), ps.find_entry(P("."), "linkedin"), DAY)
