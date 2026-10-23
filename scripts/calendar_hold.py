#!/usr/bin/env python3
"""Tentative Google Calendar holds for interviews. Never invites anyone.

    python3 scripts/calendar_hold.py build --harvest state/harvest/gmail-2026-09-24.classified.jsonl \
        --message-id <gmail id> [--start 2026-09-30T10:00] [--duration 45] [--application slug]
        [--prospect slug] [--prep path] [--title ...]          -> prints the event payload (JSON)
    python3 scripts/calendar_hold.py build ... --request        -> also writes the approval file
    python3 scripts/calendar_hold.py build --start ... --request --manual "<reason>"   (no source email)
    python3 scripts/calendar_hold.py create --approval work/approvals/A-YYYY-NNNN.md [--fake]

The payload is a private, tentative, opaque event with no attendees; its description links the
Gmail thread, the application, the prospect and the prep pack. Times come from the invite's
calendar part, else a Google Calendar subject ("@ Tue Sep 30, 2026 10am - 11am (PDT)"), else
scheduling-tool lines ("Date: ... / Time: 1:00 PM - 1:45 PM PDT"), else --start.
Without --create, paste the printed payload into the claude.ai Google Calendar connector.
`create` needs an approved, unexpired approval whose Exact content still hashes to what the owner
approved, and the separate calendar login (`gmail_auth.py login --calendar`); it inserts exactly
that payload with sendUpdates=none.
--request needs --harvest and --message-id, and the message must carry a Gmail thread id, so every hold links
its source thread and message. A hold with no source email needs --manual "<reason>": the reason goes into the
event description (hashed with the Exact content), the approval's Why section and `manual_reason`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import agency_config as cfg
import js_common as jc
import js_comms_util as cu

DEFAULT_TZ = str(cfg.get("identity.timezone", "UTC"))        # your zone: used when an invite names none
TZ_ABBREV = {"PDT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PT": "America/Los_Angeles",
             "EDT": "America/New_York", "EST": "America/New_York", "ET": "America/New_York",
             "CDT": "America/Chicago", "CST": "America/Chicago", "CT": "America/Chicago",
             "MDT": "America/Denver", "MST": "America/Denver", "MT": "America/Denver",
             "UTC": "UTC", "GMT": "UTC"}
JS_PREFIX = str(cfg.get("identity.vault_prefix", "") or "").strip("/")    # the job-search root seen from the vault root


def _vault_path(rel: str) -> str:
    """rel under identity.vault_prefix (plain rel when the vault root is the job-search root)."""
    return f"{JS_PREFIX}/{rel}" if JS_PREFIX else rel
GCAL_SUBJECT = re.compile(
    r"@\s+(?:[A-Z][a-z]{2}\s+)?(?P<mon>[A-Z][a-z]{2})\w*\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\s+"
    r"(?P<t1>\d{1,2}(?::\d{2})?\s?[ap]m)\s*[-–]\s*(?P<t2>\d{1,2}(?::\d{2})?\s?[ap]m)\s*\((?P<tz>[A-Z]{2,5})\)", re.I)
BODY_DATE = re.compile(r"Date:\s*(?:[A-Z][a-z]+,\s*)?(?P<mon>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})")
BODY_TIME = re.compile(r"Time:\s*(?P<t1>\d{1,2}(?::\d{2})?\s?[AaPp][Mm])\s*[-–]\s*(?P<t2>\d{1,2}(?::\d{2})?\s?[AaPp][Mm])\s*(?P<tz>[A-Z]{2,5})?")


class HoldError(RuntimeError):
    """Cannot build or create the hold (missing time, unapproved approval, ...)."""


# --------------------------------------------------------------------------- times
def _clock(text: str) -> tuple[int, int]:
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s?([ap]m)", text.strip().lower())
    if not m:
        raise ValueError(text)
    hour, minute = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0), int(m.group(2) or 0)
    return hour, minute


def _month(name: str) -> int:
    return datetime.strptime(name[:3].title(), "%b").month


def extract_times(rec: dict[str, Any]) -> tuple[datetime | None, datetime | None, str]:
    cal = rec.get("calendar") or {}
    if cal.get("start"):
        start = datetime.fromisoformat(cal["start"])
        end = datetime.fromisoformat(cal["end"]) if cal.get("end") else None
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(cal.get("tz") or DEFAULT_TZ))
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=start.tzinfo)
        return start, end, "ics"
    m = GCAL_SUBJECT.search(rec.get("subject") or "")
    if m:
        tz = ZoneInfo(TZ_ABBREV.get(m.group("tz").upper(), DEFAULT_TZ))
        day = date(int(m.group("year")), _month(m.group("mon")), int(m.group("day")))
        (h1, m1), (h2, m2) = _clock(m.group("t1")), _clock(m.group("t2"))
        return (datetime(day.year, day.month, day.day, h1, m1, tzinfo=tz),
                datetime(day.year, day.month, day.day, h2, m2, tzinfo=tz), "subject")
    body = rec.get("body") or ""
    d, t = BODY_DATE.search(body), BODY_TIME.search(body)
    if d and t:
        tz = ZoneInfo(TZ_ABBREV.get((t.group("tz") or "").upper(), DEFAULT_TZ))
        day = date(int(d.group("year")), _month(d.group("mon")), int(d.group("day")))
        (h1, m1), (h2, m2) = _clock(t.group("t1")), _clock(t.group("t2"))
        return (datetime(day.year, day.month, day.day, h1, m1, tzinfo=tz),
                datetime(day.year, day.month, day.day, h2, m2, tzinfo=tz), "body")
    return None, None, "none"


def _title(rec: dict[str, Any] | None, company: str | None, role: str | None) -> str:
    rec = rec or {}
    cal = rec.get("calendar") or {}
    if cal.get("summary"):
        return cal["summary"]
    m = re.match(r"^\s*(?:updated invitation|invitation)\s*:\s*(?P<t>.+?)\s+@\s+", rec.get("subject") or "", re.I)
    if m:
        return m.group("t").strip()
    if company or role:
        return " · ".join(["Interview"] + [x for x in (company, role) if x])
    return cu.one_line(rec.get("subject") or "Interview", 90)


# --------------------------------------------------------------------------- payload
def build_payload(rec: dict[str, Any] | None = None, *, title: str | None = None, start: str | datetime | None = None,
                  end: str | datetime | None = None, duration_min: int = 45, tz: str = DEFAULT_TZ,
                  application: str | None = None, prospect: str | None = None, prep: str | None = None,
                  company: str | None = None, role: str | None = None,
                  manual: str | None = None) -> dict[str, Any]:
    rec = rec or {}
    zone = ZoneInfo(tz)
    s, e, source = extract_times(rec)
    if start:
        s = datetime.fromisoformat(str(start)) if not isinstance(start, datetime) else start
        s = s if s.tzinfo else s.replace(tzinfo=zone)
        e = None
        source = "given"
    if end:
        e = datetime.fromisoformat(str(end)) if not isinstance(end, datetime) else end
        e = e if e.tzinfo else e.replace(tzinfo=zone)
    if s is None:
        raise HoldError("no interview time found in the message; pass --start YYYY-MM-DDTHH:MM (and --duration)")
    e = e or s + timedelta(minutes=duration_min)
    event_tz = str(getattr(s.tzinfo, "key", "")) or tz
    lines = ["Tentative hold created from an interview email (not a confirmation from the organizer)."]
    if rec.get("subject"):
        lines.append(f"Email: \"{cu.one_line(rec['subject'], 140)}\" from {cu.one_line(rec.get('from'), 80)}")
    if rec.get("thread_id"):
        lines.append(f"Gmail thread: https://mail.google.com/mail/u/0/#all/{rec['thread_id']}")
    if manual:
        lines.append(f"Manual hold (no source email): {cu.one_line(manual, 300)}")
    if application:
        lines.append(f"Application: {_vault_path(f'applications/{application}/application.md')}")
    if prospect:
        lines.append(f"Prospect: {_vault_path(f'prospects/{prospect}.md')}")
    prep_path = prep or (_vault_path(f"prep/packs/{application}/") if application else None)
    if prep_path:
        lines.append(f"Prep pack: {prep_path}")
    lines.append(f"Times from: {source}. Confidential: keep this event private.")
    private = {"jaja": "job-search", "source_message_id": rec.get("id"), "application": application,
               "prospect": prospect}
    return {
        "summary": f"HOLD: {title or _title(rec, company, role)}",
        "start": {"dateTime": s.isoformat(), "timeZone": event_tz},
        "end": {"dateTime": e.astimezone(s.tzinfo).isoformat(), "timeZone": event_tz},
        "status": "tentative",
        "visibility": "private",
        "transparency": "opaque",
        "description": "\n".join(lines),
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]},
        "extendedProperties": {"private": {k: v for k, v in private.items() if v}},
    }


# --------------------------------------------------------------------------- approval + create
SOURCE_RULE = ("--request needs --harvest and --message-id (the hold must link its source Gmail thread and "
               "message), or --manual \"<reason>\" when no source email exists")


def request_approval(roots: cu.Roots, payload: dict[str, Any], *, today: date | None = None,
                     requested_by: str = "role:mail-triage", manual: str | None = None) -> tuple[str, Path]:
    today = today or jc.today()
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    item = cu.ApprovalItem(id=f"cal-{jc.short_hash(body)}", channel="calendar", target="calendar:primary",
                           subject=payload["summary"], body=body, label=payload["summary"])
    why = "Tentative hold for an interview found in email."
    if manual:
        why = f"Manual hold (no source email): {cu.one_line(manual, 300)}. Tentative interview hold."
    aid, path = cu.write_approval(
        roots, action="other", items=[item], requested_by=requested_by, target="calendar:primary", today=today,
        why=why,
        effect="A private, tentative event appears on the owner's primary Google Calendar. Nobody is invited and no "
               "email is sent (sendUpdates=none). Delete the event any time.")
    if manual:
        cu.set_frontmatter(path, manual_reason=cu.one_line(manual, 300))
    return aid, path


def check_source(rec: dict[str, Any], harvest: str | None, message_id: str | None, manual: str | None) -> str | None:
    """None when a --request may be written; else why not. A found message must carry its thread id."""
    manual = (manual or "").strip() or None
    if harvest and message_id:
        if not rec.get("thread_id") and not manual:
            return f"message {message_id} has no Gmail thread id; the hold cannot link its thread ({SOURCE_RULE})"
        return None
    if manual:
        return None
    return SOURCE_RULE


def withdraw(approval_path: Path, *, reason: str, superseded_by: str | None = None,
             today: date | None = None) -> dict[str, Any]:
    """Withdraw a still-pending hold request (the interview moved or was cancelled).

    Only `pending` requests can be withdrawn; an approved or executed hold is the owner's to delete.
    The Exact content is untouched, so the content hash stays valid; status `withdrawn` makes
    approval_problems() refuse it forever."""
    today = today or jc.today()
    meta, body = jc.fm.load(approval_path)
    status = str(meta.get("status") or "")
    if status != "pending":
        raise HoldError(f"{approval_path.name} is {status or 'missing a status'}, only pending holds can be withdrawn")
    meta["status"] = "withdrawn"
    meta["withdrawn_reason"] = reason
    if superseded_by:
        meta["superseded_by"] = superseded_by
    meta["updated"] = today.isoformat()
    cu.write_record(approval_path, meta, body)
    return {"approval": meta.get("id"), "status": "withdrawn", "superseded_by": superseded_by}


def approved_payload(appr: cu.Approval) -> dict[str, Any]:
    items = [i for i in appr.items if i.get("channel") == "calendar"]
    if len(items) != 1:
        raise HoldError("the approval must hold exactly one calendar item")
    block = appr.blocks.get(str(items[0]["id"]))
    if block is None:
        raise HoldError("the calendar item is missing from the approval's Exact content")
    return json.loads(block.split("\n\n", 1)[1])


def create_event(service: Any, approval_path: Path, *, today: date | None = None) -> dict[str, Any]:
    today = today or jc.today()
    appr = cu.parse_approval(Path(approval_path))
    problems = cu.approval_problems(appr, today)
    if problems:
        raise HoldError("; ".join(problems))
    item_id = str(appr.items[0]["id"]) if appr.items else ""
    if not appr.decisions.get(item_id):
        raise HoldError("the calendar item is unticked in the approval")
    receipts = cu.section_text(appr.body, "Receipts") or ""
    m = re.search(rf"calendar event (\S+) created for {re.escape(item_id)}\b", receipts)
    if m:
        return {"event_id": m.group(1), "existing": True}
    payload = approved_payload(appr)
    event = service.events().insert(calendarId="primary", body=payload, sendUpdates="none").execute()
    cu.append_receipt(Path(approval_path), f"{jc.run_stamp(today)} calendar event {event['id']} created for {item_id} "
                                           f"(tentative, private, no attendees)")
    return {"event_id": event["id"], "existing": False, "status": event.get("status")}


# --------------------------------------------------------------------------- CLI
def _find(harvest: Path, message_id: str) -> dict[str, Any]:
    for row in jc.read_jsonl(harvest):
        if str(row.get("id")) == message_id or row.get("message_id") == message_id:
            return row
    raise HoldError(f"message {message_id} not found in {harvest}")


def _approval_path(roots: cu.Roots, ref: str) -> Path:
    p = Path(ref)
    if p.is_file():
        return p
    q = roots.os / "work" / "approvals" / f"{ref}.md"
    if q.is_file():
        return q
    raise HoldError(f"approval not found: {ref}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="job-search root (default: this department)")
    common.add_argument("--os-root", help="root holding work/approvals (default: derived)")
    common.add_argument("--today")
    b = sub.add_parser("build", parents=[common])
    b.add_argument("--harvest", help="harvest JSONL holding the invite")
    b.add_argument("--message-id", help="Gmail id (or Message-ID) of the invite")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--duration", type=int, default=45)
    b.add_argument("--title")
    b.add_argument("--application")
    b.add_argument("--prospect")
    b.add_argument("--prep")
    b.add_argument("--company")
    b.add_argument("--role")
    b.add_argument("--request", action="store_true", help="also write the approval file for --create. "
                   + SOURCE_RULE)
    b.add_argument("--manual", metavar="REASON", help="request a hold with no source email; the reason is "
                   "recorded in the event description and the approval")
    b.add_argument("--supersedes", help="A-id of a pending hold this request replaces (it is withdrawn)")
    c = sub.add_parser("create", parents=[common])
    c.add_argument("--approval", required=True)
    w = sub.add_parser("withdraw", parents=[common], help="withdraw a pending hold (interview moved or cancelled)")
    w.add_argument("--approval", required=True, help="path or A-id in work/approvals/")
    w.add_argument("--reason", required=True)
    w.add_argument("--superseded-by")
    args = ap.parse_args(argv)
    js = Path(args.root) if args.root else jc.JS_ROOT
    roots = cu.Roots(js=js, os=Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT))
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        if args.cmd == "build":
            rec = _find(Path(args.harvest), args.message_id) if args.harvest and args.message_id else {}
            manual = (args.manual or "").strip() or None
            if args.request:
                refused = check_source(rec, args.harvest, args.message_id, manual)
                if refused:
                    raise HoldError(refused)
            elif args.manual is not None:
                raise HoldError("--manual needs --request")
            payload = build_payload(rec, title=args.title, start=args.start, end=args.end, duration_min=args.duration,
                                    application=args.application, prospect=args.prospect, prep=args.prep,
                                    company=args.company, role=args.role, manual=manual)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if args.request:
                aid, path = request_approval(roots, payload, today=today, manual=manual)
                out = {"approval": aid, "path": str(path)}
                if args.supersedes:
                    old = _approval_path(roots, args.supersedes)
                    out["withdrawn"] = withdraw(old, reason=f"superseded by {aid}", superseded_by=aid, today=today)
                print(json.dumps(out), file=sys.stderr)
            elif args.supersedes:
                raise HoldError("--supersedes needs --request")
            return 0
        if args.cmd == "withdraw":
            path = _approval_path(roots, args.approval)
            print(json.dumps(withdraw(path, reason=args.reason, superseded_by=args.superseded_by, today=today), indent=2))
            return 0
        if args.cmd == "create":
            appr = cu.parse_approval(Path(args.approval))
            problems = cu.approval_problems(appr, today)
            if problems:                          # before any token is loaded
                raise HoldError("; ".join(problems))
            import gmail_auth
            try:
                service = gmail_auth.build_service("calendar")
            except gmail_auth.AuthError as exc:
                raise HoldError(str(exc)) from exc
            print(json.dumps(create_event(service, Path(args.approval), today=today), indent=2))
            return 0
    except HoldError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
