"""portal_sitting.py --index: one deterministic status per portal in
targets/platforms.yaml (platforms and presence_surfaces), computed from records, never typed: account exists, to
create, alert set, tracker linked, manual only, no sitting. INDEX.md gains a generated status block and a count line;
the hand-written order above it is the owner's and is kept byte for byte. A portal that needs a sitting and has no sheet
gets one generated. Fixture tree only."""
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
  - id: company-ats
    name: Company ATS boards
    control: api
    setup_priority: none
  - id: adzuna
    name: Adzuna API
    control: api
    setup_priority: P1
  - id: linkedin
    name: LinkedIn Jobs
    control: alert-mail
    setup_priority: P0
    adapter: scripts/job_alerts.py
    alert_senders: [jobalerts-noreply@linkedin.com]
    presence: {account: {state: exists}, alerts: {state: arriving}}
  - id: indeed
    name: Indeed
    control: alert-mail
    setup_priority: P0
    adapter: scripts/job_alerts.py
    alert_senders: [indeed.com]
    presence: {account: {state: exists-inferred}, alerts: {state: "off"}}
  - id: acme-careers
    name: Google Careers
    control: alert-mail
    setup_priority: P1
    alerts_on: 2026-10-01
  - id: wttj
    name: Welcome to the Jungle
    control: alert-mail
    setup_priority: P0
    adapter: scripts/job_alerts.py
    alert_senders: [welcometothejungle.com]
    presence: {account: {state: unknown}, alerts: {state: unknown}}
  - id: builtin
    name: Built In
    control: alert-mail
    setup_priority: P1
    presence: {account: {state: not-wanted}}
  - id: remotive
    name: Remotive
    control: manual
    setup_priority: none
  - id: examplesearch
    name: Example Search
    control: manual
    setup_priority: P1
    presence: {kind: recruiter-firm}
  - id: tsenta
    name: Tsenta
    control: manual
    status: blocked
    setup_priority: none
  - id: indeed-connector
    name: Indeed connector
    control: connector
    setup_priority: P0
  - id: levels-fyi
    name: Levels.fyi
    control: alert-mail
    status: manual
    setup_priority: P2
    presence: {account: {state: unknown}}
  - id: vc-portfolio
    name: VC portfolio boards
    control: alert-mail
    setup_priority: P2
    account_needed: false
    alert_senders: [getro.com]
  - id: fund-portfolio
    name: Fund portfolio jobs
    control: browser-with-owner
    setup_priority: P2
    account_needed: false
presence_surfaces:
  - id: github
    name: GitHub
    kind: code
    account: {state: exists}
  - id: matchmaker
    name: Jack and Jill
    kind: ai-agent-matching
    account: {state: none}
"""

INDEX = """# Portal sittings · index

| # | sitting | sheet | minutes | why this place |
|---|---|---|---|---|
| 1 | Claude connectors | indeed-connector.md | 5 | three pools |
"""


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def js(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", PLATFORMS)
    w(tmp_path / "state" / "portal-sittings" / "INDEX.md", INDEX)
    w(tmp_path / "state" / "portal-sittings" / "linkedin.md", "# sheet\n")
    w(tmp_path / "state" / "portal-sittings" / "indeed-connector.md", "# sheet\n")
    w(tmp_path / "state" / "accounts.yaml", "accounts:\n  - {platform: wttj, tenant: null, account_exists: true}\n")
    return tmp_path


def statuses(js):
    return {r["id"]: r["status"] for r in ps.index_rows(js)}


def test_every_portal_gets_exactly_one_status_from_the_records(js):
    s = statuses(js)
    assert s == {
        "company-ats": "no sitting",           # api, nothing for the owner to do
        "adzuna": "to create",                 # api, but a key to create (P1)
        "linkedin": "tracker linked",          # account, alerts arriving, parsed by job_alerts.py
        "indeed": "account exists",            # account, alerts off
        "acme-careers": "alert set",         # alerts_on recorded, no parser mapped
        "wttj": "account exists",              # state/accounts.yaml overrides the registry's unknown
        "builtin": "no sitting",               # account not-wanted: settled by the owner
        "remotive": "manual only",
        "examplesearch": "to create",                # a recruiter firm to register
        "tsenta": "no sitting",                # blocked
        "indeed-connector": "to create",       # a connector to add
        "levels-fyi": "manual only",           # registry status manual (he reads it himself), setup P2
        "vc-portfolio": "to create",           # no account needed, but an alert subscription to make
        "fund-portfolio": "manual only",       # no account needed and no alert mail: he browses it
        "github": "no sitting",                # a presence surface, not a sitting
        "matchmaker": "to create",
    }
    assert set(ps.STATUSES) >= set(s.values())


def test_each_status_names_its_source(js):
    rows = {r["id"]: r for r in ps.index_rows(js)}
    assert "state/accounts.yaml" in rows["wttj"]["why"]
    assert "alerts_on 2026-10-01" in rows["acme-careers"]["why"]
    assert "job_alerts.py" in rows["linkedin"]["why"]
    assert rows["vc-portfolio"]["why"].startswith("alert subscription (no account needed)")
    assert rows["builtin"]["why"].startswith("no account (registry account state not-wanted")   # not always his choice
    assert "alerts off: the sitting still turns them on" in rows["indeed"]["why"]


def test_the_block_carries_a_plain_key(js):
    ps.write_index(js, DAY)
    text = (js / "state" / "portal-sittings" / "INDEX.md").read_text(encoding="utf-8")
    assert "Key: to create means" in text and "account exists means" in text
    assert "The order of the sittings is ORDER.md (`python3 scripts/sitting_order.py`)." in text
    assert "The order of the sittings is the table above." not in text


def test_index_keeps_setups_order_and_adds_a_status_block_with_counts(js):
    ps.write_index(js, DAY)
    text = (js / "state" / "portal-sittings" / "INDEX.md").read_text(encoding="utf-8")
    assert text.startswith(INDEX)                                    # SETUP's order, byte for byte
    assert ps.BEGIN in text and ps.END in text
    assert "| linkedin | LinkedIn Jobs | tracker linked |" in text
    assert ("16 portals: 5 to create, 2 account exists, 1 alert set, 1 tracker linked, 3 manual only, 4 no sitting"
            in text)


def test_rerunning_replaces_the_block_never_duplicates_it(js):
    ps.write_index(js, DAY)
    ps.write_index(js, DAY)
    text = (js / "state" / "portal-sittings" / "INDEX.md").read_text(encoding="utf-8")
    assert text.count(ps.BEGIN) == 1 and text.count(ps.END) == 1


def test_a_portal_that_needs_a_sitting_and_has_no_sheet_gets_one(js):
    made = ps.write_index(js, DAY)
    sheets = js / "state" / "portal-sittings"
    assert set(made) == {"adzuna", "indeed", "acme-careers", "wttj", "examplesearch", "matchmaker", "vc-portfolio"}
    assert all((sheets / f"{pid}.md").is_file() for pid in made)
    assert not (sheets / "remotive.md").exists() and not (sheets / "company-ats.md").exists()
    rows = {r["id"]: r for r in ps.index_rows(js)}
    assert rows["adzuna"]["sheet"] == "adzuna.md" and rows["remotive"]["sheet"] is None


def test_cli_index_mode(js, capsys):
    assert ps.main(["--root", str(js), "--today", "2026-10-02", "--index"]) == 0
    out = capsys.readouterr().out
    assert "16 portals:" in out and "generated 7 sheets" in out


def test_status_is_one_importable_map_for_sitting_order(js):
    s = ps.status(js)
    assert s["linkedin"] == "tracker linked" and s["examplesearch"] == "to create" and len(s) == 16
    assert s == statuses(js)


def _sheet(js, pid):
    ps.write(js, pid, DAY)
    return (js / "state" / "portal-sittings" / f"{pid}.md").read_text(encoding="utf-8")


def test_the_field_set_follows_the_kind_of_sitting(js):
    full = _sheet(js, "examplesearch")                                     # a recruiter firm: the whole profile
    assert ps.field_profile(ps.find_entry(js, "examplesearch")) == "profile"
    assert "Desired salary" in full and "Voluntary self-identification" in full and "Resume to upload" in full
    conn = _sheet(js, "indeed-connector")                            # a connector: nothing to fill but the sign-in email
    assert ps.field_profile(ps.find_entry(js, "indeed-connector")) == "setup"
    assert "Desired salary" not in conn and "Voluntary" not in conn and "Resume to upload" not in conn
    sub = _sheet(js, "vc-portfolio")                                 # account_needed false: a subscription
    assert ps.field_profile(ps.find_entry(js, "vc-portfolio")) == "subscription"
    assert "Desired salary" not in sub and "Phone" not in sub and "Voluntary" not in sub
    assert "No profile text" in sub and "No profile text" in conn and "No profile text" not in full


def test_subscribes_by_email_reads_as_a_subscription():
    e = {"id": "job-newsletters", "control": "alert-mail", "account_needed": True,
         "alert_setup": "Alex subscribes by email at https://fundjobs.example.test"}
    assert ps.field_profile(e) == "subscription"


def test_only_profile_sittings_record_the_profile_date(js):
    assert "--profile-filled-on $(date +%F)" in _sheet(js, "examplesearch")
    assert "--profile-filled-on" not in _sheet(js, "indeed-connector")
    assert "--profile-filled-on" not in _sheet(js, "vc-portfolio")


def test_every_closing_command_parses_and_records(js, capsys):
    import re
    import apply_pack as ap
    for pid in ("examplesearch", "indeed-connector", "vc-portfolio", "adzuna", "linkedin", "matchmaker", "fund-portfolio"):
        text = _sheet(js, pid)
        cmd = re.search(r"`python3 scripts/apply_pack\.py (account [^`]+)`", text).group(1)
        cmd = cmd.replace("$(date +%F)", "2026-10-02").replace("<email used>", "rowan.example@example.test")
        import shlex
        assert ap.main(["--root", str(js)] + shlex.split(cmd)) == 0, (pid, capsys.readouterr().err)
    import yaml
    rows = {a["platform"]: a for a in yaml.safe_load((js / "state" / "accounts.yaml").read_text())["accounts"]}
    assert rows["examplesearch"]["profile_filled_on"] == "2026-10-02" and rows["indeed-connector"]["profile_filled_on"] is None


def test_the_resume_row_names_the_live_base_from_its_readme(js):
    w(js / "resumes" / "bases" / "general" / "README.md", "# The base resume\n\n**Status: the base.** Iteration 75, promoted.\n")
    assert ps.base_label(js) == "base 75 (resumes/bases/general/README.md status line)"
    assert "base 75 (resumes/bases/general/README.md status line)" in _sheet(js, "examplesearch")
    (js / "resumes" / "bases" / "general" / "README.md").write_text("# no status\n", encoding="utf-8")
    assert ps.base_label(js) == "the base in resumes/bases/general (iteration not on record)"
