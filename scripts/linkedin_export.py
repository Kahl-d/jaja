#!/usr/bin/env python3
"""Parse LinkedIn's official "Download your data" export into normalized people and touches.

    python3 scripts/linkedin_export.py parse profile/_inbox/linkedin-export/Basic_LinkedIn_Data.zip
    python3 scripts/linkedin_export.py parse profile/_inbox/linkedin-export/unzipped/ [--me-url URL]

Reads messages.csv, Invitations.csv, Connections.csv and Profile.csv from a zip or a folder (any
depth). Headers are matched by name, so both current exports (messages.csv with ATTACHMENTS and
IS MESSAGE DRAFT; Invitations.csv with inviterProfileUrl/inviteeProfileUrl) and older ones
parse; Connections.csv's "Notes:" preamble is skipped by locating the header row.

Output: state/harvest/linkedin-<date>.jsonl (git-ignored, local only), one JSON object per line
with a `type`: `meta` (me, stats), `person` (everyone who messaged the owner, whom the owner messaged, or who
sent/received an invitation note; enriched from Connections by profile URL), `touch` (one per
message or invitation note, stable `key`, plus id/from/subject/body/direction so
`mail_classify.py classify --in` can label it), `conversation` (never_answered / awaiting_reply)
and `connection` (the prospector's lookup table; --no-connections leaves them out).
No network, no scraping.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

import js_common as jc
import js_comms_util as cu

EXPORT_FILES = ("messages.csv", "invitations.csv", "connections.csv", "profile.csv")
TEXT_CAP = 4000
TRUE = {"true", "yes", "1", "y"}


# --------------------------------------------------------------------------- reading
def open_export(path: Path) -> dict[str, str]:
    """{lower-case file name: text} for the CSVs we use, from a .zip or a folder."""
    path = Path(path)
    found: dict[str, str] = {}
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                base = Path(name).name.lower()
                if base in EXPORT_FILES and base not in found:
                    found[base] = zf.read(name).decode("utf-8-sig", errors="replace")
    elif path.is_dir():
        for p in sorted(path.rglob("*.csv")):
            base = p.name.lower()
            if base in EXPORT_FILES and base not in found:
                found[base] = p.read_text(encoding="utf-8-sig", errors="replace")
    else:
        raise FileNotFoundError(f"no LinkedIn export at {path} (expected a .zip or a folder)")
    return found


def read_rows(text: str, first_header: str | None = None) -> list[dict[str, str]]:
    """CSV rows as dicts. With `first_header`, parsing starts at the line beginning with it
    (skips preambles such as Connections.csv's "Notes:" section, even multi-line quoted ones)."""
    text = (text or "").lstrip("﻿")
    if first_header:
        m = re.search(rf"(?m)^﻿?{re.escape(first_header)}", text)
        if not m:
            return []
        text = text[m.start():].lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        clean = {(k or "").strip().lstrip("﻿"): (v or "").strip() for k, v in row.items() if k is not None}
        if any(clean.values()):
            rows.append(clean)
    return rows


def _text(content: str) -> str:
    if re.search(r"<(p|br|div|span|a)\b|</\w+>", content or "", re.I):
        content = jc.html_to_text(cu.strip_hidden_html(content))
    return (content or "").strip()[:TEXT_CAP]


def parse_messages(text: str) -> list[dict[str, Any]]:
    out = []
    for r in read_rows(text, "CONVERSATION ID"):
        to_names = [n.strip() for n in (r.get("TO") or "").split(",") if n.strip()]
        out.append({
            "conversation_id": r.get("CONVERSATION ID", ""),
            "title": r.get("CONVERSATION TITLE", ""),
            "from_name": cu.display_name(r.get("FROM", "")) or r.get("FROM", ""),
            "from_url": r.get("SENDER PROFILE URL", ""),
            "to_names": to_names,
            "to_urls": cu.find_linkedin_urls(r.get("RECIPIENT PROFILE URLS", "")),
            "at": cu.to_utc_iso(r.get("DATE")),
            "subject": r.get("SUBJECT", ""),
            "content": _text(r.get("CONTENT", "")),
            "folder": r.get("FOLDER", ""),
            "is_draft": (r.get("IS MESSAGE DRAFT") or "").strip().lower() in TRUE,
            "has_attachments": bool((r.get("ATTACHMENTS") or "").strip()),
        })
    return out


def parse_invitations(text: str) -> list[dict[str, Any]]:
    return [{
        "from_name": cu.display_name(r.get("From", "")) or r.get("From", ""),
        "to_name": cu.display_name(r.get("To", "")) or r.get("To", ""),
        "at": cu.to_utc_iso(r.get("Sent At")),
        "message": _text(r.get("Message", "")),
        "direction": (r.get("Direction") or "").upper(),
        "inviter_url": cu.normalize_linkedin_url(r.get("inviterProfileUrl", "")),
        "invitee_url": cu.normalize_linkedin_url(r.get("inviteeProfileUrl", "")),
    } for r in read_rows(text, "From,")]


def parse_connections(text: str) -> list[dict[str, str]]:
    return read_rows(text, "First Name,")


def parse_profile(text: str) -> dict[str, str]:
    rows = read_rows(text, "First Name,")
    if not rows:
        return {}
    r = rows[0]
    return {"first_name": r.get("First Name", ""), "last_name": r.get("Last Name", ""),
            "name": f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip(), "headline": r.get("Headline", "")}


# --------------------------------------------------------------------------- identity
def identify_me(messages: list[dict], profile: dict, me_name: str | None = None,
                me_url: str | None = None) -> dict[str, Any]:
    names = {cu.normalize_person_name(n) for n in cu.LINKEDIN_ME_NAMES}
    urls = set(cu.LINKEDIN_ME_URLS)
    if me_name:
        names.add(cu.normalize_person_name(me_name))
    if profile.get("name"):
        names.add(cu.normalize_person_name(profile["name"]))
    if me_url:
        urls.add(cu.normalize_linkedin_url(me_url))
    # Infer: the person present in the most conversations is the account owner.
    presence: dict[str, set[str]] = {}
    for m in messages:
        for name in [m["from_name"], *m["to_names"]]:
            presence.setdefault(cu.normalize_person_name(name), set()).add(m["conversation_id"])
    if not names & set(presence) and presence:
        names.add(max(presence, key=lambda k: len(presence[k])))
    for m in messages:
        if cu.normalize_person_name(m["from_name"]) in names and "/in/" in m["from_url"]:
            urls.add(cu.normalize_linkedin_url(m["from_url"]))
    display = profile.get("name") or me_name or next(
        (m["from_name"] for m in messages if cu.normalize_person_name(m["from_name"]) in names), "")
    observed = [u for u in urls if u not in cu.LINKEDIN_ME_URLS] or sorted(urls)
    return {"name": display, "url": observed[0] if observed else "", "names": sorted(names), "urls": sorted(urls)}


def _is_me(me: dict, name: str, url: str) -> bool:
    return (url and cu.normalize_linkedin_url(url) in me["urls"]) or cu.normalize_person_name(name) in me["names"]


# --------------------------------------------------------------------------- build
def build(export: dict[str, str], me_name: str | None = None, me_url: str | None = None,
          all_connections: bool = False, generated_at: str | None = None) -> dict[str, Any]:
    messages = parse_messages(export.get("messages.csv", ""))
    invitations = parse_invitations(export.get("invitations.csv", ""))
    connections = parse_connections(export.get("connections.csv", ""))
    profile = parse_profile(export.get("profile.csv", ""))
    me = identify_me(messages, profile, me_name, me_url)
    stats = {"messages": len(messages), "invitations": len(invitations), "connections": len(connections),
             "drafts_skipped": 0, "company_page_messages": 0, "invitations_without_message": 0}

    people: dict[str, dict[str, Any]] = {}
    by_url = {cu.normalize_linkedin_url(c.get("URL", "")): c for c in connections if c.get("URL")}

    def person(name: str, url: str) -> dict[str, Any]:
        url = cu.normalize_linkedin_url(url)
        key = url or f"name:{cu.normalize_person_name(name)}"
        if key not in people:
            people[key] = {"key": key, "name": cu.display_name(name) or name, "name_key": cu.normalize_person_name(name),
                           "linkedin_url": url, "email": None, "company": None, "position": None,
                           "connected_on": None, "sources": [], "conversation_ids": [], "inbound_count": 0,
                           "outbound_count": 0, "first_inbound_at": None, "last_inbound_at": None,
                           "last_outbound_at": None, "invitation_message": None}
        return people[key]

    def note(p: dict, source: str, conversation: str | None = None) -> None:
        if source not in p["sources"]:
            p["sources"].append(source)
        if conversation and conversation not in p["conversation_ids"]:
            p["conversation_ids"].append(conversation)

    touches: list[dict[str, Any]] = []
    conversations: dict[str, dict[str, Any]] = {}
    ordered = sorted((m for m in messages), key=lambda m: (m["conversation_id"], m["at"] or ""))
    for m in ordered:
        if m["is_draft"]:
            stats["drafts_skipped"] += 1
            continue
        from_me = _is_me(me, m["from_name"], m["from_url"])
        if not from_me and m["from_url"] and "/in/" not in m["from_url"]:
            stats["company_page_messages"] += 1
            continue
        conv = conversations.setdefault(m["conversation_id"], {
            "conversation_id": m["conversation_id"], "title": m["title"], "participants": [], "messages": 0,
            "inbound": 0, "outbound": 0, "last_inbound_at": None, "last_outbound_at": None, "_last_dir": None})
        conv["messages"] += 1
        if from_me:
            counterparts = [(n, u) for n, u in _pair(m["to_names"], m["to_urls"]) if not _is_me(me, n, u)]
            kind = "reply" if conv["_last_dir"] == "in" else ("follow_up" if conv["_last_dir"] == "out" else "intro")
            conv["outbound"] += 1
            conv["last_outbound_at"] = m["at"]
            conv["_last_dir"] = "out"
            for name, url in counterparts:
                p = person(name, url)
                note(p, "messages", m["conversation_id"])
                p["outbound_count"] += 1
                p["last_outbound_at"] = max(filter(None, [p["last_outbound_at"], m["at"]]))
                _add_participant(conv, p["name"])
                touches.append(_touch(m, p, "out", kind, "linkedin-message"))
        else:
            p = person(m["from_name"], m["from_url"])
            first_from_them = m["conversation_id"] not in p["conversation_ids"]
            note(p, "messages", m["conversation_id"])
            kind = "reply" if conv["_last_dir"] == "out" else ("intro" if first_from_them else "follow_up")
            p["inbound_count"] += 1
            p["first_inbound_at"] = min(filter(None, [p["first_inbound_at"], m["at"]]))
            p["last_inbound_at"] = max(filter(None, [p["last_inbound_at"], m["at"]]))
            conv["inbound"] += 1
            conv["last_inbound_at"] = m["at"]
            conv["_last_dir"] = "in"
            _add_participant(conv, p["name"])
            touches.append(_touch(m, p, "in", kind, "linkedin-message"))

    for inv in invitations:
        if not inv["message"]:
            stats["invitations_without_message"] += 1
            continue
        incoming = inv["direction"] == "INCOMING" or (inv["direction"] != "OUTGOING" and not _is_me(me, inv["from_name"], inv["inviter_url"]))
        name, url = (inv["from_name"], inv["inviter_url"]) if incoming else (inv["to_name"], inv["invitee_url"])
        p = person(name, url)
        note(p, "invitations")
        p["invitation_message"] = inv["message"]
        if incoming:
            p["inbound_count"] += 1
            p["first_inbound_at"] = min(filter(None, [p["first_inbound_at"], inv["at"]]))
            p["last_inbound_at"] = max(filter(None, [p["last_inbound_at"], inv["at"]]))
        else:
            p["outbound_count"] += 1
            p["last_outbound_at"] = max(filter(None, [p["last_outbound_at"], inv["at"]]))
        touches.append({
            "key": "li-inv-" + cu.sha256_text(f"{name}|{url}|{inv['at']}|{inv['direction']}")[7:23],
            "at": inv["at"], "person_key": p["key"], "person_name": p["name"], "channel": "linkedin-connect",
            "direction": "in" if incoming else "out", "kind": "intro", "conversation_id": None, "thread_id": None,
            "subject": "Invitation to connect", "text": inv["message"], "summary": cu.one_line(inv["message"], 160)})

    for p in people.values():
        c = by_url.get(p["linkedin_url"]) if p["linkedin_url"] else None
        if c:
            note(p, "connections")
            p["email"] = (c.get("Email Address") or "").lower() or None
            p["company"] = c.get("Company") or None
            p["position"] = c.get("Position") or None
            p["connected_on"] = _iso_date(c.get("Connected On"))
        p["never_answered"] = p["inbound_count"] > 0 and p["outbound_count"] == 0
        p["awaiting_reply"] = bool(p["last_inbound_at"]) and (p["last_inbound_at"] > (p["last_outbound_at"] or ""))

    convs = []
    for conv in conversations.values():
        conv.pop("_last_dir", None)
        conv["group"] = len(conv["participants"]) > 1
        conv["never_answered"] = conv["inbound"] > 0 and conv["outbound"] == 0
        conv["awaiting_reply"] = bool(conv["last_inbound_at"]) and conv["last_inbound_at"] > (conv["last_outbound_at"] or "")
        convs.append(conv)
    touches.sort(key=lambda t: (t["at"] or "", t["key"]))
    stats.update({"people": len(people), "touches": len(touches), "conversations": len(convs),
                  "never_answered": sum(1 for p in people.values() if p["never_answered"]),
                  "awaiting_reply": sum(1 for p in people.values() if p["awaiting_reply"])})
    out = {"generated_at": generated_at or jc.utc_now_iso(), "me": {"name": me["name"], "url": me["url"]},
           "people": sorted(people.values(), key=lambda p: (p["name"].lower(), p["key"])),
           "touches": touches, "conversations": sorted(convs, key=lambda c: c["conversation_id"]), "stats": stats}
    out["connections"] = [{"name": f"{c.get('First Name', '')} {c.get('Last Name', '')}".strip(),
                           "first_name": c.get("First Name") or None, "last_name": c.get("Last Name") or None,
                           "linkedin_url": cu.normalize_linkedin_url(c.get("URL")),
                           "email": (c.get("Email Address") or "").lower() or None, "company": c.get("Company") or None,
                           "position": c.get("Position") or None,
                           "connected_on": _iso_date(c.get("Connected On"))} for c in connections]
    return out


def to_rows(data: dict[str, Any], include_connections: bool = True) -> list[dict[str, Any]]:
    """Typed JSONL rows; touch rows double as harvest records for mail_classify."""
    me = data.get("me") or {}
    rows: list[dict[str, Any]] = [{"type": "meta", "generated_at": data.get("generated_at"), "me": me,
                                   "stats": data.get("stats") or {}}]
    rows += [{"type": "person", **p} for p in data.get("people") or []]
    for t in data.get("touches") or []:
        sender = t["person_name"] if t["direction"] == "in" else (me.get("name") or "me")
        rows.append({"type": "touch", **t, "id": t["key"], "from": sender, "body": t.get("text") or "",
                     "snippet": t.get("summary") or "", "internal_date": t.get("at"), "label_ids": [],
                     "subject": t.get("subject") or ""})
    rows += [{"type": "conversation", **c} for c in data.get("conversations") or []]
    if include_connections:
        rows += [{"type": "connection", **c} for c in data.get("connections") or []]
    return rows


def write_jsonl(path: Path, data: dict[str, Any], include_connections: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                            for r in to_rows(data, include_connections)), encoding="utf-8")
    return path


def _iso_date(value: Any) -> str | None:
    d = jc.parse_date(value)
    return d.isoformat() if d else None


def _pair(names: list[str], urls: list[str]) -> list[tuple[str, str]]:
    if len(names) == len(urls):
        return list(zip(names, urls))
    return [(n, "") for n in names] or [("", u) for u in urls]


def _add_participant(conv: dict, name: str) -> None:
    if name not in conv["participants"]:
        conv["participants"].append(name)


def _touch(m: dict, p: dict, direction: str, kind: str, channel: str) -> dict[str, Any]:
    key = "li-msg-" + cu.sha256_text(f"{m['conversation_id']}|{m['at']}|{m['from_name']}|{p['key']}|{m['content'][:200]}")[7:23]
    return {"key": key, "at": m["at"], "person_key": p["key"], "person_name": p["name"], "channel": channel,
            "direction": direction, "kind": kind, "conversation_id": m["conversation_id"],
            "thread_id": m["conversation_id"], "subject": m["subject"] or m["title"], "text": m["content"],
            "summary": cu.one_line(m["content"], 160)}


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse an export zip or folder")
    p.add_argument("export")
    p.add_argument("--root", help="job-search root (default: this department)")
    p.add_argument("--out", help="output file (default: state/harvest/linkedin-<date>.jsonl)")
    p.add_argument("--me-name")
    p.add_argument("--me-url")
    p.add_argument("--no-connections", action="store_true", help="leave the connection lookup rows out")
    p.add_argument("--today")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else jc.JS_ROOT
    today = jc.parse_date(args.today) if args.today else jc.today()
    data = build(open_export(Path(args.export)), me_name=args.me_name, me_url=args.me_url,
                 generated_at=jc.run_stamp(today))
    out = Path(args.out) if args.out else cu.Roots(js=root).harvest / f"linkedin-{today:%Y-%m-%d}.jsonl"
    if out.suffix == ".json":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        write_jsonl(out, data, include_connections=not args.no_connections)
    print(json.dumps({"out": str(out), **data["stats"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
