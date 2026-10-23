#!/usr/bin/env python3
"""Approval-gated Gmail sends and drafts with local attachments. Nothing sends without the owner's approved file.

    python3 scripts/gmail_send.py compose (--harvest FILE --message-id ID | --to name@example.test [--to ...])
        [--cc other@example.test] [--subject "..."] (--body-file FILE | --body "...") [--attach PATH ...]
        [--thread-id T --in-reply-to "<id>" --references "<ids>"] [--mode send|draft] [--prospect slug] [--request]
    python3 scripts/gmail_send.py show --approval A-YYYY-NNNN        -> the exact message, as the owner approves it
    python3 scripts/gmail_send.py execute --approval A-YYYY-NNNN     -> send (or draft) it once

`compose` prints the message for review; `--request` files it as an approval whose Exact content is the whole action:
recipients, subject, body, thread headers and each attachment's path, size and sha256, all hashed. A reply built
from a harvested message (--harvest/--message-id) goes to its Reply-To (else its sender), keeps the subject with
"Re:", and carries threadId, In-Reply-To and References so Gmail keeps it in the thread. The body passes the same
voice gate as outbox drafts (the style rules in config: dash and phrase bans, greeting and sign-off form).

`execute` acts only on an approved, ticked, unexpired approval whose content still hashes to what the owner approved and
whose attachments still hash to what the owner saw, and never on an item or approval a hold file (state/outbox-*hold*.md, outbox.holds)
lists. Send mode loads the SEND token (gmail.send only); draft mode loads the
COMPOSE token and creates a Gmail draft, never a send. A send writes a "started" receipt before it calls Gmail and a
"done" receipt after; a started send without its done line is never repeated (check the Sent folder first). With
`--prospect` the send is logged as an outbound touch. Attachments are local files; credential folders are refused.
Messages over 4 MB go through Gmail's resumable upload; over 25 MB are refused (Gmail's own limit).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import sys
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Iterable

import agency_config as cfg
import calendar_hold as chd
import js_common as jc
import js_comms_util as cu

EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
INLINE_LIMIT = 4 * 1024 * 1024
JS_PREFIX = Path(str(cfg.get("identity.vault_prefix", "") or "").strip("/"))   # the job-search root seen from the vault root
MODES = ("send", "draft")


class MailError(RuntimeError):
    """The message cannot be built, requested or sent (why, in words; never a secret)."""


# --------------------------------------------------------------------------- build
def _addresses(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for _, addr in getaddresses([str(v) for v in values if v]):
        a = addr.strip().lower()
        if not EMAIL.match(a):
            raise MailError(f"not an email address: {addr!r}")
        if a not in out:
            out.append(a)
    for v in values:
        if v and not getaddresses([str(v)])[0][1]:
            raise MailError(f"not an email address: {v!r}")
    return out


def _forbidden_roots() -> list[Path]:
    import gmail_auth
    roots = [gmail_auth.config_dir(), jc.OS_ROOT / "vault"]
    return [r.expanduser().resolve() for r in roots]


def attachment_record(path: Any) -> dict[str, Any]:
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise MailError(f"attachment not found or not a file: {p}")
    real = p.resolve()
    if any(real == r or r in real.parents for r in _forbidden_roots()) or real.name.startswith("private"):
        raise MailError(f"attachment refused: {real} sits in a credential or private folder")
    data = real.read_bytes()
    mime = mimetypes.guess_type(real.name)[0] or "application/octet-stream"
    return {"path": str(real), "name": real.name, "mime": mime, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def build_action(*, body: str, to: Iterable[str] | None = None, cc: Iterable[str] | None = None,
                 subject: str | None = None, attachments: Iterable[Any] = (), reply_to: dict[str, Any] | None = None,
                 thread_id: str | None = None, in_reply_to: str | None = None, references: str | None = None,
                 mode: str = "send", prospect: str | None = None) -> dict[str, Any]:
    """The exact message as a hashable action. `reply_to` is a harvested message record."""
    if mode not in MODES:
        raise MailError(f"mode must be one of {MODES}")
    text = cu.canonical_text(body or "")
    if not text.strip():
        raise MailError("the body is empty")
    import outbox
    issues = outbox.lint_draft("email", text)
    if issues:
        raise MailError("the body fails the voice gate: " + "; ".join(issues))
    rec = reply_to or {}
    recipients = list(to or [])
    if rec and not recipients:
        recipients = [rec.get("reply_to") or rec.get("from_email") or rec.get("from") or ""]
    people = _addresses(recipients)
    if not people:
        raise MailError("a message needs at least one recipient")
    if rec and subject is None:
        base = str(rec.get("subject") or "")
        subject = base if re.match(r"^\s*re:", base, re.I) else f"Re: {base}".strip()
    subject = cu.one_line(subject or "", 250).strip()
    if not subject:
        raise MailError("a message needs a subject")
    if rec:
        thread_id = thread_id or rec.get("thread_id")
        in_reply_to = in_reply_to or rec.get("message_id")
        references = references or " ".join(x for x in (rec.get("references"), rec.get("message_id")) if x) or None
    action = {"action": mode, "to": people, "cc": _addresses(cc or []), "subject": subject, "body": text,
              "attachments": [attachment_record(a) for a in attachments], "thread_id": thread_id or None,
              "in_reply_to": in_reply_to or None, "references": references or None, "prospect": prospect or None}
    size = len(build_mime(action).as_bytes())
    if size > MAX_MESSAGE_BYTES:
        raise MailError(f"the message is too large ({size} bytes; Gmail's limit is {MAX_MESSAGE_BYTES})")
    return action


def build_mime(action: dict[str, Any]) -> EmailMessage:
    """The RFC 5322 message. No From (Gmail sets the account's address) and no Bcc."""
    msg = EmailMessage()
    msg["To"] = ", ".join(action["to"])
    if action.get("cc"):
        msg["Cc"] = ", ".join(action["cc"])
    msg["Subject"] = action["subject"]
    if action.get("in_reply_to"):
        msg["In-Reply-To"] = action["in_reply_to"]
    if action.get("references"):
        msg["References"] = action["references"]
    msg.set_content(action["body"] + "\n")
    for att in action.get("attachments") or []:
        maintype, subtype = att["mime"].split("/", 1)
        msg.add_attachment(Path(att["path"]).read_bytes(), maintype=maintype, subtype=subtype, filename=att["name"])
    return msg


# --------------------------------------------------------------------------- show + approval
def effect_text(action: dict[str, Any]) -> str:
    n = len(action.get("attachments") or [])
    files = f" with {n} attachment(s)" if n else ""
    if action["action"] == "draft":
        return (f"A Gmail draft to {', '.join(action['to'])}{files} appears in the owner's Drafts folder; nothing is sent "
                "until the owner clicks Send in Gmail.")
    cc = f" (cc {', '.join(action['cc'])})" if action.get("cc") else ""
    return (f"Gmail sends this message from the owner's account to {', '.join(action['to'])}{cc}{files}. "
            "Once sent it cannot be recalled.")


def render(action: dict[str, Any]) -> str:
    """The message as the owner reads it before approving."""
    lines = [f"{action['action'].upper()} (Gmail, from the owner's account)", f"To: {', '.join(action['to'])}"]
    if action.get("cc"):
        lines.append(f"Cc: {', '.join(action['cc'])}")
    lines.append(f"Subject: {action['subject']}")
    if action.get("thread_id"):
        lines.append(f"In reply to: thread {action['thread_id']} (message {action.get('in_reply_to') or 'unknown'})")
    for att in action.get("attachments") or []:
        lines.append(f"Attachment: {att['name']} ({att['bytes']} bytes, {att['mime']}, sha256 {att['sha256'][:12]}) "
                     f"from {att['path']}")
    if action.get("prospect"):
        lines.append(f"Logged as a touch for: {action['prospect']}")
    lines += ["", action["body"], "", effect_text(action)]
    return "\n".join(lines)


def request(roots: cu.Roots, action: dict[str, Any], *, today: date | None = None,
            requested_by: str = "role:messenger") -> tuple[str, Path]:
    today = today or jc.today()
    body = json.dumps(action, indent=2, sort_keys=True, ensure_ascii=False)
    label = f"{action['action'].capitalize()}: {action['subject']}"
    item = cu.ApprovalItem(
        id=f"mail-{jc.short_hash(body)}", channel="email", target=", ".join(action["to"]), subject=action["subject"],
        body=body, label=label, thread_id=action.get("thread_id"),
        attachments=[f"{a['name']} ({a['bytes']} bytes, sha256 {a['sha256'][:12]})" for a in action["attachments"]])
    why = ("Approval-gated Gmail " + ("draft" if action["action"] == "draft" else "send") + " (gmail_send.py). "
           "The Exact content is the whole message: recipients, subject, body, thread headers and each attachment's "
           "sha256. Read it with `gmail_send.py show --approval <id>`.")
    target = "to: " + ", ".join(action["to"]) + (f"; cc: {', '.join(action['cc'])}" if action.get("cc") else "")
    return cu.write_approval(roots, action="send-email", items=[item], requested_by=requested_by, today=today,
                             why=why, effect=effect_text(action), target=target)


def approved_action(appr: cu.Approval) -> dict[str, Any]:
    items = [i for i in appr.items if i.get("channel") == "email" and str(i.get("id", "")).startswith("mail-")]
    if len(items) != 1 or len(appr.items) != 1:
        raise MailError("the approval must hold exactly one gmail_send item")
    block = appr.blocks.get(str(items[0]["id"]))
    if block is None:
        raise MailError("the item is missing from the approval's Exact content")
    try:
        action = json.loads(block.split("\n\n", 1)[1])
    except (IndexError, ValueError):
        raise MailError("the item's Exact content is not a gmail_send action") from None
    if action.get("action") not in MODES:
        raise MailError("the item's Exact content is not a gmail_send action")
    return action


def token_kind(action: dict[str, Any]) -> str:
    return "send" if action["action"] == "send" else "compose"


# --------------------------------------------------------------------------- execute
def _media(data: bytes) -> Any:
    from googleapiclient.http import MediaInMemoryUpload
    return MediaInMemoryUpload(data, mimetype="message/rfc822", resumable=True)


def _js_root(approval_path: Path) -> Path:
    return Path(approval_path).resolve().parents[2] / JS_PREFIX


def execute(service: Any, approval_path: Path, *, today: date | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    today = today or jc.today()
    approval_path = Path(approval_path)
    appr = cu.parse_approval(approval_path)
    problems = cu.approval_problems(appr, today)
    if problems:
        raise MailError("; ".join(problems))
    action = approved_action(appr)
    item_id = str(appr.items[0]["id"])
    if not appr.decisions.get(item_id):
        raise MailError("the item is unticked in the approval")
    import outbox                        # a hold (state/outbox-*hold*.md) binds this send path too
    listed = outbox.holds(_js_root(approval_path))
    held = [x for x in (item_id, str(appr.meta.get("id") or "")) if x in listed]
    if held:
        raise MailError(f"{', '.join(held)} is on hold (listed in state/{listed[held[0]]}); it is never sent")
    receipts = cu.section_text(appr.body, "Receipts") or ""
    done = re.search(rf"gmail (?:send|draft) (\S+) done for {re.escape(item_id)}\b", receipts)
    if done:
        return {"message_id": done.group(1), "existing": True}
    if re.search(rf"gmail send started for {re.escape(item_id)}\b", receipts):
        raise MailError("a send of this item started and was not confirmed: look in Gmail's Sent folder before "
                        "anything else; it is never sent twice by this script")
    for att in action.get("attachments") or []:
        p = Path(att["path"])
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != att["sha256"]:
            raise MailError(f"attachment {att['name']} changed after approval (or is gone); build a new request")
    data = build_mime(action).as_bytes()
    if len(data) > MAX_MESSAGE_BYTES:
        raise MailError(f"the message is too large ({len(data)} bytes)")
    meta: dict[str, Any] = {"threadId": action["thread_id"]} if action.get("thread_id") else {}
    stamp = jc.run_stamp(today)
    users = service.users()
    if action["action"] == "draft":
        message = dict(meta)
        kw: dict[str, Any] = {"userId": "me", "body": {"message": message}}
        if len(data) > INLINE_LIMIT:
            kw["media_body"] = _media(data)
        else:
            message["raw"] = base64.urlsafe_b64encode(data).decode("ascii")
        made = users.drafts().create(**kw).execute()
        cu.append_receipt(approval_path, f"{stamp} gmail draft {made['id']} done for {item_id} "
                                         f"(to {', '.join(action['to'])}; nothing sent)")
        return {"draft_id": made["id"], "existing": False}
    kw = {"userId": "me", "body": dict(meta)}
    if len(data) > INLINE_LIMIT:
        kw["media_body"] = _media(data)
    else:
        kw["body"]["raw"] = base64.urlsafe_b64encode(data).decode("ascii")
    cu.append_receipt(approval_path, f"{stamp} gmail send started for {item_id}")
    sent = users.messages().send(**kw).execute()
    cu.append_receipt(approval_path, f"{stamp} gmail send {sent['id']} done for {item_id} (thread "
                                     f"{sent.get('threadId')}; to {', '.join(action['to'])}; "
                                     f"{len(action.get('attachments') or [])} attachment(s))")
    if action.get("prospect"):
        at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        jc.log_touch({"at": at, "prospect": action["prospect"], "channel": "email", "direction": "out",
                      "kind": "reply" if action.get("thread_id") else "message", "status": "sent",
                      "subject": action["subject"], "thread_id": sent.get("threadId"), "message_id": sent["id"],
                      "source": "gmail_send", "approval_id": appr.meta.get("id")}, root=_js_root(approval_path))
    return {"message_id": sent["id"], "thread_id": sent.get("threadId"), "existing": False}


# --------------------------------------------------------------------------- CLI
def gmail_service(kind: str) -> Any:
    import gmail_auth
    try:
        return gmail_auth.build_service(kind)
    except gmail_auth.AuthError as exc:
        raise MailError(str(exc)) from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="job-search root (default: this department)")
    common.add_argument("--os-root", help="root holding work/approvals (default: derived)")
    common.add_argument("--today")
    c = sub.add_parser("compose", parents=[common])
    c.add_argument("--harvest", help="harvest JSONL holding the message to reply to")
    c.add_argument("--message-id", help="Gmail id of that message")
    c.add_argument("--to", action="append", default=[])
    c.add_argument("--cc", action="append", default=[])
    c.add_argument("--subject")
    body = c.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file")
    c.add_argument("--attach", action="append", default=[])
    c.add_argument("--thread-id")
    c.add_argument("--in-reply-to")
    c.add_argument("--references")
    c.add_argument("--mode", choices=MODES, default="send")
    c.add_argument("--prospect")
    c.add_argument("--request", action="store_true", help="file the approval")
    for name in ("show", "execute"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--approval", required=True, help="path or A-id in work/approvals/")
    args = ap.parse_args(argv)
    js = Path(args.root) if args.root else jc.JS_ROOT
    roots = cu.Roots(js=js, os=Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT))
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        if args.cmd == "compose":
            rec = chd._find(Path(args.harvest), args.message_id) if args.harvest and args.message_id else None
            if bool(args.harvest) != bool(args.message_id):
                raise MailError("--harvest and --message-id go together")
            text = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
            action = build_action(body=text, to=args.to, cc=args.cc, subject=args.subject, attachments=args.attach,
                                  reply_to=rec, thread_id=args.thread_id, in_reply_to=args.in_reply_to,
                                  references=args.references, mode=args.mode, prospect=args.prospect)
            print(render(action))
            if args.request:
                aid, path = request(roots, action, today=today)
                print(json.dumps({"approval": aid, "path": str(path)}), file=sys.stderr)
            return 0
        path = chd._approval_path(roots, args.approval)
        appr = cu.parse_approval(path)
        if args.cmd == "show":
            print(render(approved_action(appr)))
            return 0
        problems = cu.approval_problems(appr, today)
        if problems:                                   # before any token is loaded
            raise MailError("; ".join(problems))
        action = approved_action(appr)
        print(json.dumps(execute(gmail_service(token_kind(action)), path, today=today), indent=2))
        return 0
    except (MailError, chd.HoldError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
