"""portal_report.py: per portal account, alerts, new jobs, applications, waiting messages and follow-ups; and every job
seen on 2 or more portals."""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_ats as fa  # noqa: E402
import js_common as jc  # noqa: E402
import portal_report as rp  # noqa: E402
import prospects as pros  # noqa: E402

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def job(root, jid, *, source, first_seen, platform=None, sightings=None):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title=f"AI Engineer {jid}", source=source,
                         source_url=f"https://example.com/{jid}", apply_url=None, ats_vendor="greenhouse",
                         posted_at=None, location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                         employment_type="full-time", jd_text="Build agents.", now=NOW)
    rec.update({"id": jid, "first_seen": first_seen, "status": "fit_checked"})
    if platform:
        rec["platform"] = platform
    if sightings:
        rec["sightings"] = sightings
    fa.save_job(root, rec)


@pytest.fixture
def root(tmp_path):
    w(tmp_path / "targets" / "platforms.yaml", """
        platforms:
          - id: linkedin
            name: LinkedIn Jobs
            control: alert-mail
            status: probation
            alert_senders: [jobalerts-noreply@portal.example.com]
            inbox_senders: [messages-noreply@portal.example.com]
            presence: {account: {state: exists}, alerts: {state: arriving}}
          - id: company-ats
            name: Company ATS boards
            control: api
            status: active
            alert_senders: []
        presence_surfaces:
          - {id: github, name: GitHub, kind: code}
          - {id: underdog, name: Underdog, kind: job-board, account: {state: unknown}}
        """)
    job(tmp_path, "j1", source="linkedin-alert", platform="linkedin", first_seen="2026-10-01T09:00:00Z",
        sightings=[{"portal": "linkedin", "url": "https://li/1", "first_seen": "2026-10-01T09:00:00Z"},
                   {"portal": "company-ats", "url": "https://ats/1", "first_seen": "2026-10-01T12:00:00Z"}])
    job(tmp_path, "j2", source="greenhouse", first_seen="2026-09-28T09:00:00Z")
    job(tmp_path, "j3", source="greenhouse", first_seen="2026-09-10T09:00:00Z")
    w(tmp_path / "state" / "job-alerts.jsonl",
      '{"at": "2026-09-30T20:00:00Z", "platform": "linkedin"}\n{"at": "2026-10-01T07:00:00Z", "platform": "linkedin"}\n')
    w(tmp_path / "applications" / "a1" / "application.md", "---\nid: a1\napplied_at: '2026-09-30'\napplied_via: linkedin\n---\n")
    w(tmp_path / "applications" / "a2" / "application.md", "---\nid: a2\napplied_at: '2026-09-01'\napplied_via: ats\n---\n")
    w(tmp_path / "applications" / "a3" / "application.md", "---\nid: a3\napplied_at: null\n---\n")
    rows = [
        {"id": "m1", "direction": "in", "from_email": "messages-noreply@portal.example.com", "thread_id": "t1",
         "internal_date": "2026-09-29T10:00:00Z", "classification": {"label": "recruiter_outreach"}},
        {"id": "m2", "direction": "in", "from_email": "messages-noreply@portal.example.com", "thread_id": "t2",
         "internal_date": "2026-09-29T10:00:00Z", "classification": {"label": "recruiter_outreach"}, "portal": "linkedin"},
        {"id": "m3", "direction": "in", "from_email": "jobalerts-noreply@portal.example.com", "thread_id": "t3",
         "internal_date": "2026-09-30T10:00:00Z", "classification": {"label": "job_alert"}},
        {"id": "m4", "direction": "in", "from_email": "messages-noreply@portal.example.com", "thread_id": "t4",
         "internal_date": "2026-08-01T10:00:00Z", "classification": {"label": "recruiter_outreach"}}]
    w(tmp_path / "state" / "harvest" / "gmail-2026-10-01.classified.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
    jc.log_touch({"at": "2026-09-30T10:00:00Z", "prospect": "x", "channel": "linkedin-message", "direction": "out",
                  "kind": "reply", "status": "sent", "thread_id": "t2"}, root=tmp_path)
    rec = pros.new_meta("rae", "Rae Lin", source="linkedin-export", today=TODAY)
    rec.update({"status": "replied", "last_outbound_at": "2026-09-20T17:00:00Z", "last_inbound_at": "2026-09-10T00:00:00Z",
                 "outbound_count": 1, "reply_context": {"channel": "linkedin-message"}})
    jc.write_record(tmp_path / "prospects" / "rae.md", rec, "# Rae\n\n## Timeline\n")
    return tmp_path


def test_one_row_per_portal_with_every_count(root):
    rep = rp.report(root, TODAY)
    rows = {r["id"]: r for r in rep["portals"]}
    assert list(rows) == ["linkedin", "company-ats", "underdog"]               # github is not a portal
    li, ats = rows["linkedin"], rows["company-ats"]
    assert (li["account"], li["alerts"], li["last_alert"]) == ("exists", "arriving", "2026-10-01T07:00:00Z")
    assert (li["new_today"], li["new_week"]) == (1, 1)
    assert (ats["new_today"], ats["new_week"]) == (1, 2)                       # j1 by its sighting, j2 by source; not j3
    assert (li["applied_total"], li["applied_week"]) == (1, 1) and ats["applied_total"] == 1 and ats["applied_week"] == 0
    assert li["messages_waiting"] == 1                                          # m1 only: m2 answered, m3 an alert, m4 old
    assert li["follow_ups_due"] == 1
    assert rows["underdog"]["status"] == "not vetted"


def test_jobs_seen_on_two_portals_are_listed_with_dates(root):
    rep = rp.report(root, TODAY)
    assert rep["multi_portal_jobs"] == [{"id": "j1", "company": "Acme", "title": "AI Engineer j1", "status": "fit_checked",
                                         "seen": [("linkedin", "2026-10-01"), ("company-ats", "2026-10-01")]}]
    text = rp.render(rep)
    assert "| linkedin | alert-mail | exists | arriving | 2026-10-01 | 1 | 1 | 1 (1) | 1 | 1 |" in text
    assert "- j1 · Acme · AI Engineer j1 (fit_checked): linkedin 2026-10-01, company-ats 2026-10-01" in text


def test_the_cli_prints_markdown_or_json(root, capsys):
    assert rp.main(["--root", str(root), "--today", "2026-10-01"]) == 0
    assert capsys.readouterr().out.startswith("# Portals 2026-10-01")
    assert rp.main(["--root", str(root), "--today", "2026-10-01", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["today"] == "2026-10-01"
