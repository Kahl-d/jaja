"""portal_digest.py: the morning brief's one line per portal: new jobs in 24 h, jobs on 2+ portals, messages
waiting and follow-ups due (follow_ups.py's portal tags), and setup state (state/accounts.yaml, else the registry)."""
from __future__ import annotations

import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_ats as fa  # noqa: E402
import portal_digest as pd  # noqa: E402

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def job(root, jid, source, first_seen, platform=None, sightings=None):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title=f"AI Engineer {jid}", source=source,
                         source_url=f"https://example.com/{jid}", apply_url=None, ats_vendor="greenhouse", posted_at=None,
                         location="SF", remote="onsite", comp=(None, None, None), employment_type="full-time",
                         jd_text="x", now=NOW)
    rec.update({"id": jid, "first_seen": first_seen, "status": "fit_checked"})
    if platform:
        rec["platform"] = platform
    if sightings:
        rec["sightings"] = sightings
    fa.save_job(root, rec)


def make(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", """
        platforms:
          - {id: linkedin, name: LinkedIn, control: alert-mail, setup_priority: P0, presence: {account: {state: exists}, alerts: {state: arriving}}}
          - {id: company-ats, name: Company ATS, control: api, setup_priority: none}
          - {id: wellfound, name: Wellfound, control: alert-mail, setup_priority: P0, presence: {account: {state: unknown}}}
          - {id: dice, name: Dice, control: alert-mail, setup_priority: P2}
        """)
    w(tmp_path / "state" / "accounts.yaml", """
        accounts:
          - {platform: wellfound, tenant: null, company: Wellfound, account_exists: true, email_used: k@example.com}
          - {platform: workday, tenant: acme, company: Acme, account_exists: true}
        """)
    job(tmp_path, "j1", "linkedin-alert", "2026-10-01T09:00:00Z", platform="linkedin",
        sightings=[{"portal": "linkedin", "url": "u1", "first_seen": "2026-10-01T09:00:00Z"},
                   {"portal": "company-ats", "url": "u2", "first_seen": "2026-10-01T10:00:00Z"}])
    job(tmp_path, "j2", "greenhouse", "2026-09-30T20:00:00Z")
    job(tmp_path, "j3", "greenhouse", "2026-09-28T09:00:00Z")
    return tmp_path


FOLLOW = {"date": "2026-10-01", "due": [
    {"kind": "portal-message", "portal": "linkedin", "who": "a"}, {"kind": "portal-message", "portal": "linkedin", "who": "b"},
    {"kind": "prospect", "portal": "linkedin", "who": "c"}, {"kind": "application", "portal": None, "who": "d"},
    {"kind": "portal-message", "portal": "email", "who": "e"}], "exhausted": [], "windows": []}


def test_each_portal_with_news_or_a_setup_step_gets_one_line(tmp_path):
    root = make(tmp_path)
    out = pd.digest(root, NOW, follow=lambda r, d: FOLLOW)
    rows = {r["id"]: r for r in out["portals"]}
    li = rows["linkedin"]
    assert (li["new_24h"], li["multi"], li["messages"], li["follow_ups"]) == (1, 1, 2, 1)
    assert (li["account"], li["alerts"]) == ("exists", "arriving")
    assert rows["company-ats"]["new_24h"] == 2                                 # j1 by sighting, j2 by source; j3 too old
    assert rows["wellfound"]["account"] == "exists"                            # state/accounts.yaml wins over the registry
    assert rows["wellfound"]["setup_pending"] is True                          # P0 and alerts not set
    assert out["quiet"] == ["dice"] and out["other"] == {"email": {"messages": 1, "follow_ups": 0},
                                                         "no portal": {"messages": 0, "follow_ups": 1}}
    assert out["tenants"] == {"workday": 1}


def test_the_brief_text_is_short_and_one_line_each(tmp_path):
    text = pd.render(pd.digest(make(tmp_path), NOW, follow=lambda r, d: FOLLOW))
    lines = text.strip().splitlines()
    assert lines[0].startswith("Portals 2026-10-01: 2 new jobs in 24 h, 1 on 2+ portals, 3 messages waiting, 2 follow-ups due")
    assert any(l.startswith("- linkedin: 1 new · 1 on 2+ portals · 2 waiting · 1 follow-up · account exists, alerts arriving")
               for l in lines)
    assert not any(l.startswith("- wellfound:") for l in lines)                # setup only: on the one setup line
    assert any(l.startswith("Setup pending: wellfound (alerts)") for l in lines)
    assert lines[-1].startswith("Quiet: dice") and len(lines) <= 12


def test_not_wanted_accounts_are_settled_and_connectors_ask_to_be_added(tmp_path):
    root = make(tmp_path)
    p = root / "targets" / "platforms.yaml"
    p.write_text(p.read_text() + "  - {id: builtin, control: alert-mail, setup_priority: P1, presence: {account: {state: not-wanted}}}\n"
                 "  - {id: dice-connector, control: connector, setup_priority: P0}\n", encoding="utf-8")
    d = pd.digest(root, NOW, follow=lambda r, day: FOLLOW)
    rows = {r["id"]: r for r in d["portals"]}
    assert "builtin" in d["quiet"] and rows["dice-connector"]["setup_pending"] is True
    assert "dice-connector (add the connector in Claude)" in pd.render(d)


def test_recruiter_firms_wait_under_setup_pending_register_until_accounts_mark_them(tmp_path):
    root = make(tmp_path)
    p = root / "targets" / "platforms.yaml"
    p.write_text(p.read_text() + "  - {id: harnham, control: manual, setup_priority: P1, presence: {kind: recruiter-firm, "
                 "account: {state: null}, registration: {state: null}}}\n"
                 "  - {id: ai-tinkerers-sf, control: manual, setup_priority: P1, presence: {kind: talent-network, "
                 "account: {state: null}}}\n", encoding="utf-8")
    follow = {**FOLLOW, "due": FOLLOW["due"] + [{"kind": "portal-message", "portal": "ai-tinkerers-sf", "who": "f"}]}
    d = pd.digest(root, NOW, follow=lambda r, day: follow)
    rows = {r["id"]: r for r in d["portals"]}
    assert rows["harnham"]["setup_pending"] is True and rows["harnham"]["messages"] == 0
    assert rows["ai-tinkerers-sf"]["messages"] == 1                         # a reply through the portal tag
    text = pd.render(d)
    assert "harnham (register)" in text.split("Setup pending: ", 1)[1].splitlines()[0]
    acc = root / "state" / "accounts.yaml"
    acc.write_text(acc.read_text() + "  - {platform: harnham, tenant: null, company: Harnham, account_exists: true}\n")
    d = pd.digest(root, NOW, follow=lambda r, day: FOLLOW)
    assert "harnham" in d["quiet"]


def test_the_linkedin_export_shows_pending_daily_until_a_parsed_export_exists(tmp_path):
    root = make(tmp_path)
    d = pd.digest(root, NOW, follow=lambda r, day: FOLLOW)
    assert d["linkedin_export"] is None
    text = pd.render(d)
    assert "LinkedIn export pending: request it at https://www.linkedin.com/mypreferences/d/download-my-data" in text
    assert "linkedin_export.py parse" in text
    harvest = root / "state" / "harvest"
    harvest.mkdir(parents=True, exist_ok=True)
    (harvest / "linkedin-2026-10-02.classified.jsonl").write_text("{}\n")      # a classified copy is not the parse
    assert pd.digest(root, NOW, follow=lambda r, day: FOLLOW)["linkedin_export"] is None
    (harvest / "linkedin-2026-10-02.jsonl").write_text('{"type": "profile"}\n')
    d = pd.digest(root, NOW, follow=lambda r, day: FOLLOW)
    assert d["linkedin_export"] == "state/harvest/linkedin-2026-10-02.jsonl"
    assert "LinkedIn export pending" not in pd.render(d)
