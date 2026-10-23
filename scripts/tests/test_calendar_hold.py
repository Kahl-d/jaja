"""calendar_hold.py — tentative, private interview holds; payload path (no Google calls)."""
from __future__ import annotations

import json
from datetime import date

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import calendar_hold as chd
import js_comms_util as cu

TODAY = date(2026, 9, 24)


def _rec(fid, **override):
    item = next(i for i in ch.load_labeled() if i["id"] == fid)
    item = {**item, **override}
    if override.get("calendar", "keep") is None:
        item.pop("calendar", None)
    return ch.records([item])[0]


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / chd.JS_PREFIX
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def test_payload_from_a_google_invite_uses_the_ics_times_and_stays_private():
    payload = chd.build_payload(_rec("sch-gcal-01"))
    assert payload["start"] == {"dateTime": "2026-09-30T10:00:00-07:00", "timeZone": "America/Los_Angeles"}
    assert payload["end"]["dateTime"] == "2026-09-30T11:00:00-07:00"
    assert payload["summary"].startswith("HOLD: Technical Interview") and payload["summary"].endswith("Helio Systems")
    assert (payload["status"], payload["visibility"], payload["transparency"]) == ("tentative", "private", "opaque")
    assert "attendees" not in payload


def test_times_come_from_the_subject_when_there_is_no_ics():
    payload = chd.build_payload(_rec("sch-updated-05", calendar=None))
    assert payload["start"]["dateTime"] == "2026-08-05T09:00:00-07:00"
    assert payload["end"]["dateTime"] == "2026-08-05T13:00:00-07:00"
    assert payload["summary"] == "HOLD: Onsite Loop - Brightloop"


def test_times_come_from_a_scheduling_tool_body():
    payload = chd.build_payload(_rec("sch-goodtime-02"))
    assert payload["start"]["dateTime"] == "2026-10-02T13:00:00-07:00"
    assert payload["end"]["dateTime"] == "2026-10-02T13:45:00-07:00"


def test_an_invite_without_a_time_needs_an_explicit_start():
    rec = _rec("int-inv-02")
    with pytest.raises(chd.HoldError, match="--start"):
        chd.build_payload(rec)
    payload = chd.build_payload(rec, start="2026-09-29T14:00", duration_min=60, company="Helio Systems",
                                role="Senior ML Engineer")
    assert payload["start"]["dateTime"] == "2026-09-29T14:00:00-07:00"
    assert payload["end"]["dateTime"] == "2026-09-29T15:00:00-07:00"
    assert payload["summary"] == "HOLD: Interview · Helio Systems · Senior ML Engineer"


def test_description_links_the_application_prospect_prep_pack_and_thread():
    rec = _rec("sch-gcal-01")
    payload = chd.build_payload(rec, application="helio-systems-senior-ml-engineer", prospect="maya-chen")
    desc = payload["description"]
    assert "applications/helio-systems-senior-ml-engineer/application.md" in desc
    assert "prospects/maya-chen.md" in desc
    assert "prep/packs/helio-systems-senior-ml-engineer/" in desc
    assert f"https://mail.google.com/mail/u/0/#all/{rec['thread_id']}" in desc
    assert "not a confirmation" in desc
    private = payload["extendedProperties"]["private"]
    assert private["application"] == "helio-systems-senior-ml-engineer" and private["source_message_id"] == rec["id"]


def test_request_writes_an_approval_whose_exact_content_is_the_payload(roots):
    payload = chd.build_payload(_rec("sch-gcal-01"), prospect="maya-chen")
    aid, path = chd.request_approval(roots, payload, today=TODAY)
    appr = cu.parse_approval(path)
    assert aid == "A-2026-0001" and appr.hash_ok
    assert (appr.meta["action"], appr.meta["status"], appr.meta["target"]) == ("other", "pending", "calendar:primary")
    assert chd.approved_payload(appr) == payload


class FakeCalendar:
    def __init__(self):
        self.calls = []

    def events(self):
        return self

    def insert(self, calendarId, body, sendUpdates):
        self.calls.append({"calendarId": calendarId, "body": body, "sendUpdates": sendUpdates})

        class _R:
            def execute(inner):
                return {"id": f"evt{len(self.calls)}", "status": "tentative"}
        return _R()


def test_create_refuses_an_approval_that_is_not_approved(roots):
    _, path = chd.request_approval(roots, chd.build_payload(_rec("sch-gcal-01")), today=TODAY)
    cal = FakeCalendar()
    with pytest.raises(chd.HoldError, match="not approved"):
        chd.create_event(cal, path, today=TODAY)
    assert cal.calls == []


def test_create_inserts_exactly_the_approved_payload_without_notifying_anyone(roots):
    payload = chd.build_payload(_rec("sch-gcal-01"))
    _, path = chd.request_approval(roots, payload, today=TODAY)
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-24", decided_via="phone")
    cal = FakeCalendar()
    result = chd.create_event(cal, path, today=TODAY)
    assert result["event_id"] == "evt1" and cal.calls[0]["body"] == payload
    assert cal.calls[0]["calendarId"] == "primary" and cal.calls[0]["sendUpdates"] == "none"
    assert chd.create_event(cal, path, today=TODAY)["existing"] is True and len(cal.calls) == 1
    assert "calendar event evt1" in path.read_text()


def test_cli_prints_the_payload_for_the_calendar_connector(roots, tmp_path, capsys):
    rec = _rec("sch-gcal-01")
    harvest = tmp_path / "gmail.jsonl"
    harvest.write_text(json.dumps(rec) + "\n")
    assert chd.main(["build", "--harvest", str(harvest), "--message-id", rec["id"], "--prospect", "maya-chen",
                     "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-24"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "tentative" and "prospects/maya-chen.md" in payload["description"]
    assert not roots.approvals.exists()                       # printing never writes an approval


# --------------------------------------------------------------------------- --today stamps (2026-09-25)
from comms_helpers import clock_2030  # noqa: E402,F401


def test_create_receipt_is_stamped_on_today(roots, clock_2030):
    _, path = chd.request_approval(roots, chd.build_payload(_rec("sch-gcal-01")), today=TODAY)
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-24", decided_via="phone")
    chd.create_event(FakeCalendar(), path, today=date(2026, 9, 26))
    assert "- 2026-09-26T12:00:00+00:00 calendar event evt1" in path.read_text() and "2030" not in path.read_text()


def test_withdraw_marks_a_pending_hold_withdrawn_with_reason_and_successor(roots):
    old = chd.build_payload(_rec("sch-gcal-01"), prospect="maya-chen")
    old_id, old_path = chd.request_approval(roots, old, today=TODAY)
    res = chd.withdraw(old_path, reason="interview moved to Oct 2", superseded_by="A-2026-0009", today=TODAY)
    meta = cu.parse_approval(old_path).meta
    assert meta["status"] == "withdrawn" and meta["withdrawn_reason"] == "interview moved to Oct 2"
    assert meta["superseded_by"] == "A-2026-0009" and str(meta["updated"]) == "2026-09-24"
    assert res["status"] == "withdrawn"
    assert cu.approval_problems(cu.parse_approval(old_path), TODAY)  # can never be executed


def test_withdraw_refuses_a_hold_that_is_not_pending(roots):
    payload = chd.build_payload(_rec("sch-gcal-01"), prospect="maya-chen")
    _, path = chd.request_approval(roots, payload, today=TODAY)
    text = path.read_text(encoding="utf-8").replace("status: pending", "status: approved", 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(chd.HoldError):
        chd.withdraw(path, reason="moved", today=TODAY)


def test_build_request_supersedes_the_old_hold(roots, tmp_path, capsys):
    old = chd.build_payload(_rec("sch-gcal-01"), prospect="maya-chen")
    old_id, old_path = chd.request_approval(roots, old, today=TODAY)
    rec = _rec("sch-gcal-01")
    harvest = tmp_path / "gmail.jsonl"
    harvest.write_text(json.dumps(rec) + "\n")
    rc = chd.main(["build", "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-24",
                   "--harvest", str(harvest), "--message-id", rec["id"],
                   "--start", "2026-10-02T11:00:00-07:00", "--duration", "60", "--title", "Harborline interview",
                   "--request", "--supersedes", old_id])
    assert rc == 0
    meta = cu.parse_approval(old_path).meta
    assert meta["status"] == "withdrawn" and meta["superseded_by"] == "A-2026-0002"


# --------------------------------------------------------------------------- source link required (2026-09-25)
def _build(roots, *extra):
    return chd.main(["build", "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-24",
                     "--start", "2026-10-02T11:00:00-07:00", "--title", "Harborline interview", *extra])


def _harvest(tmp_path, rec):
    harvest = tmp_path / "gmail.jsonl"
    harvest.write_text(json.dumps(rec) + "\n")
    return harvest


def test_request_without_the_source_message_is_refused_and_writes_nothing(roots, tmp_path, capsys):
    rec = _rec("sch-gcal-01")
    harvest = _harvest(tmp_path, rec)
    for extra in ([], ["--harvest", str(harvest)], ["--message-id", rec["id"]], ["--manual", "  "]):
        assert _build(roots, "--request", *extra) == 2, extra
        out = capsys.readouterr().out
        assert "--harvest" in out and "--message-id" in out and "--manual" in out, extra
        assert not roots.approvals.exists() or not list(roots.approvals.glob("A-*.md")), extra


def test_request_refuses_a_source_message_without_a_thread(roots, tmp_path, capsys):
    rec = {k: v for k, v in _rec("sch-gcal-01").items() if k != "thread_id"}
    assert _build(roots, "--request", "--harvest", str(_harvest(tmp_path, rec)), "--message-id", rec["id"]) == 2
    assert "thread" in capsys.readouterr().out
    assert not roots.approvals.exists() or not list(roots.approvals.glob("A-*.md"))


def test_request_with_the_source_message_links_thread_and_message(roots, tmp_path, capsys):
    rec = _rec("sch-gcal-01")
    assert _build(roots, "--request", "--harvest", str(_harvest(tmp_path, rec)), "--message-id", rec["id"]) == 0
    path = next(roots.approvals.glob("A-*.md"))
    payload = chd.approved_payload(cu.parse_approval(path))
    assert f"#all/{rec['thread_id']}" in payload["description"]
    assert payload["extendedProperties"]["private"]["source_message_id"] == rec["id"]
    assert "manual" not in path.read_text().lower()


def test_manual_request_records_the_reason_in_the_approval(roots, capsys):
    reason = "Booked by phone with a recruiter; no email exists"
    assert _build(roots, "--request", "--manual", reason) == 0
    path = next(roots.approvals.glob("A-*.md"))
    appr = cu.parse_approval(path)
    assert appr.meta["manual_reason"] == reason
    assert f"Manual hold (no source email): {reason}" in chd.approved_payload(appr)["description"]  # hashed
    assert reason in cu.section_text(appr.body, "Why this is being sent")
    assert appr.hash_ok


def test_printing_without_request_still_needs_no_source(roots, capsys):
    assert _build(roots) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "tentative"


def test_help_documents_the_source_rule(capsys):
    with pytest.raises(SystemExit):
        chd.main(["build", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "--manual" in out and "--request needs --harvest and --message-id" in out
