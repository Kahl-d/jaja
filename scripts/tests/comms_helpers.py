"""Test helpers for the communications/CRM scripts (no network, no Google).

Turns the YAML fixtures in fixtures/comms/ into Gmail API `format=full` message dicts, the
same shape `users.messages.get` returns, so tests exercise the real extraction path.
"""
from __future__ import annotations

import base64
import hashlib
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

import pytest
import yaml

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = TESTS / "fixtures" / "comms"
OWNER = "alex@example.test"
DEFAULT_TO = f"Alex Example <{OWNER}>"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Autouse in every comms test module (import it): no sockets, no urlopen, no service key.
    Nothing in these tests may reach Google, an LLM service or LinkedIn."""
    import socket
    import urllib.request

    def refuse(*_a, **_k):
        raise RuntimeError("network access is disabled in comms tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("GMAIL_CLIENT_SECRET", raising=False)


def load_labeled() -> list[dict]:
    return yaml.safe_load((FIXTURES / "labeled_emails.yaml").read_text(encoding="utf-8"))


def load_outbound() -> list[dict]:
    return yaml.safe_load((FIXTURES / "outbound_emails.yaml").read_text(encoding="utf-8"))


def gmail_id(fixture_id: str) -> str:
    return hashlib.sha1(fixture_id.encode()).hexdigest()[:16]


def message_id(fixture_id: str) -> str:
    return f"<{fixture_id}@fixtures.test>"


def _b64(data: bytes) -> str:
    # Gmail returns base64url; strip padding so the decoder must cope with it.
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _ics(cal: dict) -> str:
    def stamp(value: str) -> str:
        return value.replace("-", "").replace(":", "")
    tz = cal.get("tz", "America/Los_Angeles")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Google Inc//Google Calendar 70.9054//EN", "VERSION:2.0",
        "METHOD:REQUEST", "BEGIN:VEVENT",
        f"DTSTART;TZID={tz}:{stamp(cal['start'])}",
        f"DTEND;TZID={tz}:{stamp(cal['end'])}",
        f"ORGANIZER;CN=Organizer:mailto:{cal.get('organizer', 'organizer@example.com')}",
        f"SUMMARY:{cal.get('summary', 'Event')}",
        "UID:fixture-uid@google.com", "END:VEVENT", "END:VCALENDAR", ""])


def build_email(item: dict, by_id: dict[str, dict] | None = None) -> EmailMessage:
    by_id = by_id or {}
    msg = EmailMessage()
    msg["From"] = item["from"]
    msg["To"] = item.get("to", DEFAULT_TO)
    if item.get("cc"):
        msg["Cc"] = item["cc"]
    if item.get("reply_to"):
        msg["Reply-To"] = item["reply_to"]
    msg["Subject"] = item["subject"]
    msg["Date"] = format_datetime(datetime.fromisoformat(item["at"]))
    msg["Message-ID"] = message_id(item["id"])
    parent = item.get("in_reply_to")
    if parent:
        msg["In-Reply-To"] = message_id(parent)
        msg["References"] = message_id(parent)
    for key, value in (item.get("headers") or {}).items():
        msg[key] = value
    text = item["body"]
    html = "<html><body>" + "".join(f"<p>{p}</p>" for p in text.split("\n\n")) + "</body></html>"
    if item.get("html_only"):
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(text)
        if item.get("html"):
            msg.add_alternative(html, subtype="html")
    if item.get("calendar"):
        if not msg.is_multipart():
            msg.make_alternative()
        cal = _ics(item["calendar"])
        msg.add_alternative(cal, subtype="calendar", params={"method": "REQUEST"})
        msg.add_attachment(cal.encode(), maintype="application", subtype="ics", filename="invite.ics")
    for name in item.get("attachments") or []:
        msg.add_attachment(b"%PDF-1.4 fixture", maintype="application", subtype="pdf", filename=name)
    return msg


def _payload(part, part_id: str = "") -> dict:
    node = {
        "partId": part_id,
        "mimeType": part.get_content_type(),
        "filename": part.get_filename() or "",
        "headers": [{"name": k, "value": str(v)} for k, v in part.items()],
        "body": {"size": 0},
    }
    if part.is_multipart():
        node["parts"] = [_payload(p, f"{part_id}.{i}" if part_id else str(i))
                         for i, p in enumerate(part.iter_parts())]
    else:
        data = part.get_payload(decode=True) or b""
        if node["filename"]:
            node["body"] = {"attachmentId": "ANGjdJ" + hashlib.sha1(data).hexdigest()[:12], "size": len(data)}
        else:
            node["body"] = {"size": len(data), "data": _b64(data)}
    return node


def to_gmail_message(item: dict, by_id: dict[str, dict] | None = None, history_id: int = 1000) -> dict:
    by_id = by_id or {}
    email_msg = build_email(item, by_id)
    at = datetime.fromisoformat(item["at"])
    thread_key = item.get("thread", item["id"])
    snippet = " ".join(item["body"].split())[:180]
    labels = item.get("gmail_labels") or ["INBOX"]
    return {
        "id": gmail_id(item["id"]),
        "threadId": gmail_id(thread_key),
        "labelIds": labels,
        "snippet": snippet,
        "historyId": str(history_id),
        "internalDate": str(int(at.timestamp() * 1000)),
        "sizeEstimate": len(email_msg.as_bytes()),
        "payload": _payload(email_msg),
    }


def mailbox(items: list[dict]) -> list[dict]:
    by_id = {i["id"]: i for i in items}
    out = []
    for n, item in enumerate(sorted(items, key=lambda i: i["at"])):
        out.append(to_gmail_message(item, by_id, history_id=1000 + n))
    return out


def records(items: list[dict]) -> list[dict]:
    """Fixture items -> harvest records through the production extractor."""
    import gmail_pull
    return [gmail_pull.extract_record(m, account_email=OWNER) for m in mailbox(items)]


@pytest.fixture
def clock_2030(monkeypatch):
    """The wall clock says 2030-01-01 09:00 UTC. Anything a script stamps while given --today must carry
    that day instead (run_stamp: the day at 12:00:00 UTC)."""
    import js_common as jc
    from datetime import date as _date
    monkeypatch.setattr(jc, "today", lambda: _date(2030, 1, 1))
    monkeypatch.setattr(jc, "utc_now_iso", lambda: "2030-01-01T09:00:00+00:00")
    monkeypatch.setattr(jc, "now_iso", lambda: "2030-01-01T01:00:00")
