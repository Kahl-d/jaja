"""Tests for scripts/portal_sitting.py: one prep file per portal sitting with the owner present. Read-only on
records; private.yaml values never copied; no credential field ever."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import apply_pack as ap  # noqa: E402
import fetch_ats as fa  # noqa: E402
import portal_sitting as ps  # noqa: E402

SECRET_VALUES = ("Alex Example-LEGAL", "$999K", "open-to-relocate-SECRET", "Gender-SECRET", "hunter2-SECRET")


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def js(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", """
        platforms:
          - id: indeed-connector
            name: Indeed connector in Claude
            url: https://mcp.indeed.com/claude/mcp
            control: connector
            alert_senders: []
            alert_setup: |
              Alex adds the Indeed connector in Claude (Settings, Connectors) and signs in to Indeed.
          - id: linkedin
            name: LinkedIn Jobs
            url: https://www.linkedin.com/jobs/
            control: alert-mail
            alert_senders: [jobalerts-noreply@linkedin.com]
            alert_setup: |
              linkedin.com/jobs (signed in as Alex). Turn on "Job alert" for each search.
          - id: yc
            name: Y Combinator Work at a Startup
            url: https://www.workatastartup.com
            control: alert-mail
            alert_senders: [workatastartup.com]
            alert_sender_confirmed: false
            alert_setup: |
              workatastartup.com, sign in with the YC account and complete the one profile.
          - id: examplesearch
            name: Example Search
            url: https://www.examplesearch.com/
            control: manual
            alert_senders: []
            inbox_senders: [examplesearch.com]
            alert_setup: upload the base PDF through its CV form
            fraud_filter: "Alex never pays; never an SSN before an offer."
            presence: {kind: recruiter-firm}
        presence_surfaces:
          - {id: matchmaker, name: Example Matching Co, url: https://app.matching.example.test, next_step: "Alex joins once"}
        """)
    w(tmp_path / "profile" / "facts.yaml", """
        identity:
          full_name: Alex Example
          emails: {job_search_primary: {value: alex.job@example.test}}
          phone: {value: "+1 000 000 0000"}
          location: {value: San Francisco, CA}
          links:
            github: {value: github.com/alex-example, status: confirmed}
            linkedin: {value: linkedin.com/in/alex-example, status: confirmed}
        education:
          - {id: ed-ms, degree: Bachelor of Science, major: Data Science and AI, school: Example University, conferred: 2025-12}
        conflicts: []
        """)
    w(tmp_path / "profile" / "private.yaml", f"""
        legal_name: {{first: "{SECRET_VALUES[0]}", last: X}}
        salary_expectation: "{SECRET_VALUES[1]}"
        relocation: "{SECRET_VALUES[2]}"
        eeo: {{gender: "{SECRET_VALUES[3]}"}}
        portal_password: "{SECRET_VALUES[4]}"
        """)
    w(tmp_path / "hub" / "rulings.yaml", """
        rulings:
          - {id: R-1, quote: "plan deeply || I hold work authorization through an example program for the next two years, during which I will need a petition."}
          - {id: R-2, quote: "put the salry as 250K", reading: "Reading: the default salary answer is $250K base plus benefits."}
          - {id: R-3, quote: "the base, we keep 180 to 200", reading: "Reading (ORCH): the default answer is $180K to $200K base; where a posted range sits higher, answer within that range (never below it); where it sits lower and the role is really good, accept the role's range. Supersedes R-2's $250K default."}
          - {id: R-4, quote: "for the opioon i am ...", reading: "Reading (ORCH): recorded in profile/private.yaml (eeo)."}
        """)
    w(tmp_path / "legal" / "timers.md", """
        # timers
        ## Answer scripts
        - "Are you legally authorized to work in the US?" -> **Yes**
        - "Will you now or in the future require sponsorship?" -> **Yes, in the future (H-1B)**
        """)
    w(tmp_path / "hub" / "plans" / "portals-plan.md", """
        # Portals
        Shared alert queries (use these words on every portal that takes a saved search): "AI engineer", "machine learning
        engineer"; locations San Francisco Bay Area and Remote (United States); full-time; mid and senior.

        | # | portal | link | what you set up (once) | time | control | tool |
        |---|---|---|---|---|---|---|
        | 3 | **LinkedIn Jobs** | https://www.linkedin.com/jobs/ | up to 20 job alerts, daily (targets/platforms.yaml (linkedin)) | 15 min | alert mail | alert mail |
        """)
    w(tmp_path / "hub" / "plans" / "portal-census.md", """
        | # | portal | link | verdict | rec | deciding fact (grade) | new |
        |---|---|---|---|---|---|---|
        | 1 | Indeed | https://www.indeed.com | USE (connector) | partial | Official MCP docs: 4 tools (P) [indeed-connector] | no |
        """)
    w(tmp_path / "profile" / "lenses" / "ai-engineer.md", """
        ---
        family: ai-engineer
        titles_covered: ["AI Engineer*", "LLM Engineer"]
        ---
        """)
    w(tmp_path / "profile" / "lenses" / "product-engineer.md", """
        ---
        family: product-engineer
        titles_covered: ["Founding Product Engineer", "Product Engineer*"]
        ---
        """)
    w(tmp_path / "state" / "linkedin" / "LINKEDIN.md", """
        # LinkedIn master (approved)
        ## Headline
        AI Agents | LLM Evaluation
        ## About
        text
        """)
    (tmp_path / "resumes" / "bases" / "general").mkdir(parents=True)
    (tmp_path / "resumes" / "bases" / "general" / "AlexExample_Resume.pdf").write_bytes(b"%PDF")
    for n, title in enumerate(["Product Engineer", "Founding Product Engineer", "AI Engineer, Agents"]):
        rec = fa.make_record({"name": "Acme", "slug": "acme"}, title=title, source="yc-alert",
                             source_url=f"https://www.workatastartup.com/jobs/{n}", apply_url=None, ats_vendor=None,
                             posted_at=None, location="SF", remote="onsite", comp=(None, None, None),
                             employment_type="full-time", jd_text="x", now=NOW)
        rec.update({"id": f"acme-yc-{n}", "platform": "yc"})
        fa.save_job(tmp_path, rec)
    return tmp_path


def sitting(js, pid):
    assert ps.main(["--root", str(js), "--today", "2026-10-01", pid]) == 0
    return (js / "state" / "portal-sittings" / f"{pid}.md").read_text()


def test_a_sitting_has_the_link_steps_fields_profile_slot_alerts_and_the_closing_command(js):
    text = sitting(js, "linkedin")
    for heading in ("## 1. Link and steps", "## 2. Fields and values", "## 3. Profile text", "## 4. Alert settings",
                    "## 5. After the sitting"):
        assert heading in text
    assert "https://www.linkedin.com/jobs/" in text and 'Turn on "Job alert"' in text
    assert "up to 20 job alerts, daily" in text                                  # plan-file row naming (linkedin)
    assert "| Email | alex.job@example.test | profile/facts.yaml identity.emails.job_search_primary |" in text
    assert "I hold work authorization through an example program" in text and "hub/rulings.yaml R-1" in text
    assert "Yes, in the future (H-1B)" in text                                   # legal/timers.md answer script
    salary = [ln for ln in text.splitlines() if ln.startswith("| Desired salary |")][0]
    assert "$180K to $200K base" in salary and "R-3 (see private.yaml: salary_expectation)" in salary
    assert "R-2" not in text and "$250K" not in text                        # superseded (R-3)
    assert "AlexExample_Resume.pdf" in text
    assert "LINKEDIN.md ## Headline" not in text       # no per-heading rows; the approval's progress
    assert '"AI engineer", "machine learning' in text and "jobalerts-noreply@linkedin.com" in text
    assert ("python3 scripts/apply_pack.py account --platform linkedin --company \"LinkedIn Jobs\" --exists yes "
            "--email alex.job@example.test --created-on $(date +%F)") in text


def test_private_values_are_never_copied_only_named_and_no_credential_field_exists(js):
    for pid in ("linkedin", "yc", "examplesearch", "indeed-connector"):
        text = sitting(js, pid)
        for secret in ("LEGAL", "999K", "SECRET"):
            assert secret not in text
        if pid != "indeed-connector":                 # a connector sitting has no form (field_profile "setup")
            assert "from private.yaml: legal_name" in text and "from private.yaml: relocation" in text
            assert "from private.yaml: eeo.gender" in text and "the owner set these answers" in text
        labels = re.findall(r"^\| ([^|]+) \|", text.split("## 2.", 1)[1].split("## 3.", 1)[0], re.M)
        assert labels and not any(ap.CREDENTIAL_KEY_RE.search(lab) for lab in labels if lab.strip() != "Field")
        assert "portal_password" not in text


def test_the_profile_slot_names_the_focus_profile_from_the_portals_roles_and_never_writes_the_text(js):
    yc = sitting(js, "yc")
    slot = yc.split("## 3. Profile text", 1)[1].split("## 4.", 1)[0]
    assert "profile/lenses/product-engineer.md" in slot and "2 of 3" in slot  # titles of yc jobs vs titles_covered
    assert "_PROFILE-KIT.md" in slot and "TO BE DRAFTED" not in slot  # the kit holds the text
    li = sitting(js, "linkedin")                                                # no jobs from it: the default lens
    assert "profile/lenses/ai-engineer.md" in li.split("## 3. Profile text", 1)[1].split("## 4.", 1)[0]


def test_alerts_name_the_sender_to_confirm_and_connectors_and_firms_say_what_they_have(js):
    yc = sitting(js, "yc")
    assert "workatastartup.com (confirm on the first alert)" in yc
    con = sitting(js, "indeed-connector")
    assert "no alert mail: searched in sessions by the connector-search skill" in con
    assert "Official MCP docs: 4 tools" in con                                 # census row [indeed-connector]
    firm = sitting(js, "examplesearch")
    assert "replies from examplesearch.com" in firm and "Alex never pays" in firm


def test_all_writes_every_sitting_in_the_configured_order_and_skips_ids_with_no_record(js, capsys):
    assert ps.main(["--root", str(js), "--today", "2026-10-01", "--all"]) == 0
    out = capsys.readouterr().out
    written = sorted(p.stem for p in (js / "state" / "portal-sittings").glob("*.md"))
    assert written == sorted(["indeed-connector", "linkedin", "gmail", "yc", "matchmaker", "examplesearch"])
    order = [ln.split()[1] for ln in out.splitlines() if ln.startswith("wrote ")]
    assert order == ["indeed-connector", "linkedin", "gmail", "yc", "matchmaker", "examplesearch"]
    assert "skipped (not in the registry): ziprecruiter-connector" in out
    gmail = (js / "state" / "portal-sittings" / "gmail.md").read_text()
    assert "CONNECTORS.md" in gmail and "apply_pack.py account" not in gmail


def test_an_unknown_portal_is_refused_and_records_are_untouched(js):
    before = {p: p.read_bytes() for p in js.rglob("*") if p.is_file()}
    assert ps.main(["--root", str(js), "--today", "2026-10-01", "nope"]) == 2
    after = {p: p.read_bytes() for p in js.rglob("*") if p.is_file()}
    assert before == after


def test_every_sheets_closing_command_runs(tmp_path, monkeypatch):
    """Each live sheet's section 5 command parses and records, for every sheet kind,
    in a tmp root (the live state is never touched)."""
    import re as _re
    import shlex
    import shutil
    from datetime import date as _date
    import apply_pack as ap
    from layout import JS as js
    monkeypatch.setattr(ps, "focus_lens", lambda root, pid: ("ai-engineer", 0, 0))   # the jobs scan is not under test
    import functools
    monkeypatch.setattr(ps, "_yaml", functools.lru_cache(maxsize=None)(ps._yaml))     # the registry is read once
    (tmp_path / "targets").mkdir()
    shutil.copy(js / "targets" / "platforms.yaml", tmp_path / "targets" / "platforms.yaml")
    import yaml
    reg = yaml.safe_load((js / "targets" / "platforms.yaml").read_text(encoding="utf-8"))
    ids = [str(p["id"]) for p in reg.get("platforms") or []] + [str(p["id"]) for p in reg.get("presence_surfaces") or []]
    kinds, ran = set(), 0
    for pid in ids:
        entry = ps.find_entry(js, pid)
        if entry is None or entry.get("control") == "oauth" or entry.get("sitting_note"):
            continue                    # a sitting_note sheet (another owner, e.g. bigco-careers) has no closing command
        text = ps.render(js, entry, _date(2026, 10, 2))
        m = _re.search(r"`python3 scripts/apply_pack\.py (account [^`]+)`", text)
        assert m, pid
        cmd = m.group(1).replace("$(date +%F)", "2026-10-02").replace("<company_slug>", "acme").replace("COMPANY_SLUG", "acme").replace("COMPANY_NAME", "Acme")
        assert ap.main(["--root", str(tmp_path)] + shlex.split(cmd) + ["--today", "2026-10-02"]) == 0, (pid, cmd)
        kinds.add(ps.field_profile(entry))
        ran += 1
    assert {"profile", "setup", "subscription"} <= kinds and ran >= 20   # the shipped starter registry; a long-used one has more
    rows = yaml.safe_load((tmp_path / "state" / "accounts.yaml").read_text())["accounts"]
    assert all(r["profile_filled_on"] in (None, "2026-10-02") for r in rows)
