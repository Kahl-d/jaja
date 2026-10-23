#!/usr/bin/env python3
"""Gmail harvest (read-only) and Gmail draft creation from approved approvals.

    python3 scripts/gmail_pull.py harvest [--since YYYY-MM-DD] [--query "..."] [--fake mailbox.json]
    python3 scripts/gmail_pull.py harvest --since YYYY-MM-DD --alert-senders --refresh   # re-read alert mail
    python3 scripts/gmail_pull.py draft --approval <runtime>/work/approvals/A-YYYY-NNNN.md [--item ID]
    python3 scripts/gmail_pull.py status

harvest  pages through messages.list (after:<since> -in:chats -in:spam -in:trash [+ --query]),
         fetches each message with messages.get(format=full) in batches of <= 50, and appends one
         JSON line per new message to state/harvest/gmail-<date>.jsonl: headers, thread id, snippet
         and a plain-text body excerpt (<= 4,000 chars). Mail from a job-alert sender (an address or
         domain in targets/platforms.yaml `alert_senders`, matched as job_alerts.py matches it) keeps
         up to 200,000 chars (--alert-body-cap) and a `links` list read from its HTML part: every visible
         http(s) link in document order ({href, text}, each pair once; at most 300, `links_truncated` says
         when more existed; no link is ever fetched), and on
         each job link (the platform's job-link rules in job_alerts.py) the `card`: the text of the
         largest element around the link that holds no other job's link, so job_alerts.py can read a
         card even when the plain text lost it (HTML-only alerts, a cut body). An HTML-only alert's
         body is its HTML with each table cell on its own line. Hidden elements never count.
         Attachments are never stored; calendar
         invites keep only start/end/summary/organizer. state/harvest/gmail.state.json keeps the
         last historyId and message date, so the next run is incremental (history.list, falling
         back to a date query when the history id has expired). A filtered run (--query, --alert-senders)
         or a --refresh never moves that cursor, so it cannot skip the mail it did not list.
         --alert-senders narrows the listing to the registry's alert senders (from:(a OR b ...)).
         --refresh fetches listed messages again even when already harvested and replaces their rows in
         place (state/harvest/gmail-*.jsonl, and in each *.classified.jsonl twin every harvest field while
         the row's `classification` is kept), so a fix to extraction reaches mail harvested before it.
draft    creates Gmail DRAFTS (never sends) for the ticked email items of an approval file that
         is `status: approved`, unexpired, decided, and whose content hashes still match both the
         approval and the outbox draft. Prints the draft ids and appends a receipt.

Quotas (Gmail API, 2026): 6,000 units per user per minute; messages.list 5, messages.get 20,
history.list 2, drafts.create 10. The default budget is 5,000 units/minute (about 250 messages
a minute). Nothing here can send mail: the only write call is users.drafts.create.
"""
from __future__ import annotations

import argparse
import base64
import html as _html
import json
import mimetypes
import os
import re
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import agency_config as cfg
import gmail_auth
import js_common as jc
import js_comms_util as cu

def backlog_start(today: date | None = None) -> date:
    """The first day of a first harvest: policy.mail.backlog_start (YYYY-MM-DD) if set, else 90 days before today."""
    raw = str(cfg.get("policy.mail.backlog_start", "") or "").strip()
    try:
        return date.fromisoformat(raw) if raw else (today or date.today()) - timedelta(days=90)
    except ValueError:
        return (today or date.today()) - timedelta(days=90)
BODY_CAP = 4000
ALERT_BODY_CAP = 200_000        # job-alert senders: a whole digest keeps its cards (a 20,000 cap cut every daily
                                #   digest of a large alert sender, which runs to 32,000 to 36,000 characters)
LINKS_CAP = 300                 # links kept per job-alert message, in document order
HREF_CAP = 2048
LINK_TEXT_CAP = 300
CARD_CAP = 1500
SNIPPET_CAP = 300
BATCH_SIZE = 50
LIST_PAGE_SIZE = 500
UNIT_COST = {"messages.list": 5, "messages.get": 20, "history.list": 2, "drafts.create": 10, "getProfile": 1}
DEFAULT_UNITS_PER_MINUTE = 5000
BASE_QUERY = "-in:chats -in:spam -in:trash"
SKIP_LABELS = {"SPAM", "TRASH", "CHAT"}
RETRYABLE = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


class DraftRefused(RuntimeError):
    """The approval or a draft failed a safety check; nothing was created for it."""


# --------------------------------------------------------------------------- quota
class QuotaBudget:
    """Rolling 60-second budget of Gmail quota units. `units_per_minute=None` disables it."""

    def __init__(self, units_per_minute: int | None = DEFAULT_UNITS_PER_MINUTE,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.limit, self.clock, self.sleep = units_per_minute, clock, sleep
        self.window: deque[tuple[float, int]] = deque()
        self.spent = 0

    def spend(self, units: int) -> None:
        if self.limit:
            while True:
                now = self.clock()
                while self.window and now - self.window[0][0] >= 60:
                    self.window.popleft()
                used = sum(u for _, u in self.window)
                if used + units <= self.limit or not self.window:
                    break
                self.sleep(max(0.01, 60 - (now - self.window[0][0])))
            self.window.append((self.clock(), units))
        self.spent += units


# --------------------------------------------------------------------------- errors
def http_status(exc: BaseException) -> int | None:
    for attr in ("status_code",):
        if isinstance(getattr(exc, attr, None), int):
            return getattr(exc, attr)
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_retryable(exc: BaseException) -> bool:
    status = http_status(exc)
    if status in RETRYABLE:
        return True
    return status == 403 and re.search(r"rateLimitExceeded|userRateLimitExceeded", str(exc)) is not None


def _execute(request: Any, sleep: Callable[[float], None]) -> Any:
    delay = 1.0
    for attempt in range(MAX_RETRIES + 1):
        try:
            return request.execute()
        except Exception as exc:  # googleapiclient.errors.HttpError or the fake's
            if attempt == MAX_RETRIES or not is_retryable(exc):
                raise
            sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- extraction
def _b64decode(data: str | None) -> bytes:
    if not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _part_headers(part: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in part.get("headers") or []}


def _charset(part: dict) -> str:
    ctype = _part_headers(part).get("content-type", "")
    m = re.search(r'charset="?([\w.\-]+)"?', ctype, re.I)
    return m.group(1) if m else "utf-8"


def _decode_part(part: dict) -> str:
    raw = _b64decode((part.get("body") or {}).get("data"))
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _ics_value(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v for k, _, v in (p.partition("=") for p in params)}, value


def _ics_time(value: str, params: dict[str, str]) -> str | None:
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        if "T" not in value:
            return datetime.strptime(value, "%Y%m%d").date().isoformat()
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tzid.strip('"')))
            except Exception:
                pass
        return dt.isoformat()
    except ValueError:
        return None


def parse_ics(text: str) -> dict[str, Any] | None:
    """Minimal VEVENT reader: start, end, tz, summary, organizer, location."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text or "")
    event: dict[str, Any] = {}
    inside = False
    for line in unfolded.splitlines():
        if line.startswith("BEGIN:VEVENT"):
            inside = True
            continue
        if line.startswith("END:VEVENT"):
            break
        if not inside or ":" not in line:
            continue
        name, params, value = _ics_value(line)
        if name in ("DTSTART", "DTEND"):
            event["start" if name == "DTSTART" else "end"] = _ics_time(value, params)
            if name == "DTSTART":
                event["tz"] = params.get("TZID", "UTC" if value.endswith("Z") else None)
        elif name == "SUMMARY":
            event["summary"] = value.replace("\\,", ",").replace("\\n", " ").strip()
        elif name == "ORGANIZER":
            m = re.search(r"mailto:([^\s;]+)", value, re.I) or re.search(r"mailto:([^\s;]+)", line, re.I)
            event["organizer"] = m.group(1).lower() if m else None
        elif name == "LOCATION":
            event["location"] = cu.one_line(value.replace("\\,", ","), 200)
    return event or None


def extract_body(payload: dict) -> tuple[str, dict[str, Any]]:
    plain: list[str] = []
    html: list[str] = []
    info: dict[str, Any] = {"has_calendar": False, "has_attachments": False, "calendar": None}

    def walk(part: dict) -> None:
        if part.get("parts"):
            for child in part["parts"]:
                walk(child)
            return
        mime = (part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        if mime in ("text/calendar", "application/ics") or filename.lower().endswith(".ics"):
            info["has_calendar"] = True
            if body.get("data") and not info["calendar"]:
                info["calendar"] = parse_ics(_decode_part(part))
            return
        if filename or body.get("attachmentId"):
            info["has_attachments"] = True
            return
        if mime == "text/plain":
            plain.append(_decode_part(part))
        elif mime == "text/html":
            html.append(_decode_part(part))

    walk(payload or {})
    text = "\n".join(p for p in plain if p.strip())
    info["html"] = "\n".join(html)                 # for job-alert links; never stored
    info["from_html"] = not text.strip() and bool(html)
    if info["from_html"]:
        text = jc.html_to_text(cu.strip_hidden_html(info["html"]))
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, info


# --------------------------------------------------------------------------- job-alert HTML: links and cards
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr"})
_BLOCK = frozenset({"address", "article", "aside", "blockquote", "center", "dd", "div", "dl", "dt", "footer",
                    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
                    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul"})
_SKIP = frozenset({"script", "style", "head", "title", "template", "noscript"})
_TOP = frozenset({"#root", "html", "body"})
# A start tag that closes an open element of the same kind (HTML's implied end tags), up to the scope
# boundary: an unclosed <tr>, <td> or <li> must not swallow the next row, cell or item.
_IMPLIED = {"tr": ({"tr"}, {"table", "tbody", "thead", "tfoot"}),
            "td": ({"td", "th"}, {"tr", "table"}), "th": ({"td", "th"}, {"tr", "table"}),
            "li": ({"li"}, {"ul", "ol"})}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "jobs")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Any] = []
        self.jobs: set[str] = set()


class _Tree(HTMLParser):
    """A forgiving DOM for mail HTML: an end tag closes up to its nearest open match; a stray one is ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("#root", {}, None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IMPLIED:
            closes, scope = _IMPLIED[tag]
            open_ = self.cur
            while open_ is not self.root and open_.tag not in scope:
                if open_.tag in closes:
                    self.cur = open_.parent
                    break
                open_ = open_.parent
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.children.append(_Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag: str) -> None:
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        self.cur.children.append(data)


def _render(node: _Node, out: list[str]) -> None:
    """Text of the subtree with a line break around every block; iterative, so no nesting depth can stop
    a harvest."""
    stack: list[Any] = [iter(node.children)]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            continue
        if isinstance(child, str):
            out.append(child)
        elif child.tag == "br":
            out.append("\n")
        elif child.tag not in _SKIP:
            block = "\n" if child.tag in _BLOCK else ""
            out.append(block)
            stack.append(iter([*child.children, block]))


def _lines(node: _Node) -> list[str]:
    """The visible text of a node, one line per block (paragraphs, list items and table cells)."""
    out: list[str] = []
    _render(node, out)
    text = "".join(out).replace("\u00a0", " ")
    return [ln for ln in (re.sub(r"[ \t\r\f\v]+", " ", x).strip() for x in text.split("\n")) if ln]


def alert_html(html: str, platform: str) -> tuple[list[str], list[dict[str, str]], bool]:
    """(text lines, links, links_truncated) of a job-alert HTML part, hidden elements removed first. Each
    visible http(s) link is {href, text}, each pair once (a repeat adds only a card its first lacked); the
    first link of each job card also carries `card`: the lines of
    the largest element around it whose links point to that one job (job_alerts.match_job_url, the
    platform's rules), which is how the email itself lays the card out."""
    import job_alerts                                    # the job-link rules live with the parser

    tree = _Tree()
    tree.feed(cu.strip_hidden_html(html))
    tree.close()
    anchors: list[tuple[_Node, str, str | None]] = []
    stack: list[Any] = [iter(tree.root.children)]          # document order, iterative
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            continue
        if isinstance(child, _Node) and child.tag not in _SKIP:
            href = child.attrs.get("href", "").strip() if child.tag == "a" else ""
            if re.match(r"(?i)https?://", href):
                link = job_alerts.match_job_url(href, platform)
                anchors.append((child, href, link.canonical if link else None))
            stack.append(iter(child.children))
    for node, _, canonical in anchors:
        up: _Node | None = node
        while canonical and up is not None:
            up.jobs.add(canonical)
            up = up.parent
    links: list[tuple[bool, dict[str, str]]] = []
    first: dict[tuple[str, str], int] = {}
    carded: set[int] = set()
    for node, href, canonical in anchors:
        entry = {"href": href[:HREF_CAP], "text": "\n".join(_lines(node))[:LINK_TEXT_CAP]}
        if canonical:
            card = node
            while card.parent is not None and card.parent.tag not in _TOP and card.parent.jobs == {canonical}:
                card = card.parent
            lines = _lines(card)
            if id(card) not in carded and lines:
                carded.add(id(card))
                entry["card"] = "\n".join(lines)[:CARD_CAP]
        key = (entry["href"], entry["text"])
        if key in first:                                  # each (href, text) once, at its first place
            kept = links[first[key]][1]
            if "card" in entry and "card" not in kept:
                kept["card"] = entry["card"]
            continue
        first[key] = len(links)
        links.append((bool(canonical), entry))
    # Job links take the cap first; boilerplate links (navigation, social, unsubscribe) fill what is left,
    # so a link-heavy digest never loses a job. Document order is kept.
    job_idx = [i for i, (is_job, _) in enumerate(links) if is_job][:LINKS_CAP]
    other_idx = [i for i, (is_job, _) in enumerate(links) if not is_job][:max(0, LINKS_CAP - len(job_idx))]
    keep = sorted(job_idx + other_idx)
    return _lines(tree.root), [links[i][1] for i in keep], len(keep) < len(links)


def alert_platforms(root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """targets/platforms.yaml, whose `alert_senders` mark job-alert mail, and its state (ok, missing or
    unreadable: missing is not empty, so the harvest summary says which)."""
    import job_alerts

    path = job_alerts.platforms_path(root)
    if not path.is_file():
        return {}, "missing"
    try:
        platforms = job_alerts.load_platforms_file(path)
    except Exception as exc:  # noqa: BLE001 - a broken registry must not stop the mail harvest
        return {}, f"unreadable ({str(exc).splitlines()[0] if str(exc) else type(exc).__name__})"
    return platforms, f"ok ({sum(1 for p in platforms.values() if p.get('alert_senders'))} platforms with alert senders)"


def extract_record(msg: dict, account_email: str | None = None, harvested_at: str | None = None,
                   platforms: dict[str, dict[str, Any]] | None = None,
                   alert_body_cap: int = ALERT_BODY_CAP) -> dict[str, Any]:
    """Gmail API message (format=full) -> the harvest record committed to state/harvest/. `platforms` is the
    targets/platforms.yaml registry: mail from one of its alert senders keeps `alert_body_cap` characters and
    its `links` (alert_html); other mail keeps BODY_CAP and no links."""
    payload = msg.get("payload") or {}
    headers = _part_headers(payload)
    get = lambda name: cu.decode_header_value(headers.get(name.lower(), "")).strip()  # noqa: E731
    from_raw = get("From")
    from_name, from_email = cu.parse_address(from_raw)
    accounts = {a.lower() for a in cu.ACCOUNT_EMAILS} | ({account_email.lower()} if account_email else set())
    direction = "out" if from_email in accounts else "in"
    text, info = extract_body(payload)
    platform = None
    if platforms and direction == "in":
        import job_alerts
        platform = job_alerts.platform_for({"from_email": from_email, "from": from_raw}, platforms)
    links: list[dict[str, str]] | None = None
    links_truncated = False
    if platform:
        links = []
        if info["html"].strip():
            lines, links, links_truncated = alert_html(info["html"], platform)
            if info["from_html"]:
                text = "\n".join(lines)
    cap = alert_body_cap if platform else BODY_CAP
    truncated = len(text) > cap
    internal = msg.get("internalDate")
    record = {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "history_id": str(msg.get("historyId") or ""),
        "internal_date": cu.to_utc_iso(int(internal)) if internal else cu.to_utc_iso(get("Date")),
        "date": get("Date"),
        "from": from_raw,
        "from_name": from_name,
        "from_email": from_email,
        "to": get("To"),
        "cc": get("Cc"),
        "reply_to": get("Reply-To"),
        "subject": get("Subject"),
        "message_id": get("Message-ID"),
        "in_reply_to": get("In-Reply-To"),
        "references": get("References"),
        "list_id": get("List-Id"),
        "list_unsubscribe": get("List-Unsubscribe")[:300],
        "precedence": get("Precedence"),
        "auto_submitted": get("Auto-Submitted"),
        "label_ids": list(msg.get("labelIds") or []),
        "snippet": _html.unescape(msg.get("snippet") or "")[:SNIPPET_CAP],
        "body": text[:cap] if direction == "in" else "",
        "body_truncated": truncated if direction == "in" else False,
        "has_calendar": info["has_calendar"],
        "calendar": info["calendar"],
        "has_attachments": info["has_attachments"],
        "direction": direction,
        "harvested_at": harvested_at or jc.utc_now_iso(),
    }
    if links is not None:
        record["links"], record["links_truncated"] = links, links_truncated
    return record


def alert_sender_query(platforms: dict[str, dict[str, Any]]) -> str | None:
    """Gmail search terms for every alert sender in the registry, e.g. `from:(glassdoor.com OR indeed.com)`;
    None when the registry names none (missing is not empty: the caller refuses)."""
    senders = sorted({str(x).strip().lower() for entry in platforms.values()
                      for x in entry.get("alert_senders") or [] if str(x).strip()})
    return f"from:({' OR '.join(senders)})" if senders else None


# --------------------------------------------------------------------------- listing / fetching
def build_query(since: date, extra: str | None = None) -> str:
    """Gmail `after:` is day-granular and timezone-fuzzy, so query one day early and filter locally."""
    q = f"after:{since - timedelta(days=1):%Y/%m/%d} {BASE_QUERY}"
    return f"{q} {extra.strip()}" if extra and extra.strip() else q


def iter_message_refs(service: Any, query: str, budget: QuotaBudget, sleep: Callable[[float], None],
                      page_size: int = LIST_PAGE_SIZE) -> Iterable[dict]:
    token = None
    while True:
        budget.spend(UNIT_COST["messages.list"])
        kwargs = {"userId": "me", "q": query, "maxResults": page_size, "includeSpamTrash": False}
        if token:
            kwargs["pageToken"] = token
        resp = _execute(service.users().messages().list(**kwargs), sleep)
        yield from resp.get("messages") or []
        token = resp.get("nextPageToken")
        if not token:
            return


def iter_history_refs(service: Any, start_history_id: str, budget: QuotaBudget,
                      sleep: Callable[[float], None]) -> tuple[list[dict], str | None]:
    refs: list[dict] = []
    token = None
    latest = None
    while True:
        budget.spend(UNIT_COST["history.list"])
        kwargs = {"userId": "me", "startHistoryId": start_history_id, "historyTypes": ["messageAdded"]}
        if token:
            kwargs["pageToken"] = token
        resp = _execute(service.users().history().list(**kwargs), sleep)
        latest = resp.get("historyId") or latest
        for record in resp.get("history") or []:
            for added in record.get("messagesAdded") or []:
                if added.get("message"):
                    refs.append(added["message"])
        token = resp.get("nextPageToken")
        if not token:
            return refs, latest


def fetch_messages(service: Any, ids: list[str], budget: QuotaBudget, sleep: Callable[[float], None],
                   batch_size: int | None = None,
                   on_batch: Callable[[list[dict]], None] | None = None) -> tuple[list[dict], list[str]]:
    """messages.get(format=full) for every id, in batches of <= 50, retrying rate limits. Each batch's messages go
    to `on_batch` as soon as they land (the harvest appends them at once, so a run that dies keeps them). Returns
    (messages, residue): the residue is the ids still rate-limited after MAX_RETRIES rounds, left for the next run
    rather than failing this one."""
    batch_size = batch_size or BATCH_SIZE
    results: dict[str, dict] = {}
    pending = list(ids)
    delay = 1.0
    failed: list[str] = []
    for attempt in range(MAX_RETRIES + 1):
        failed = []
        for start in range(0, len(pending), batch_size):
            chunk = pending[start:start + batch_size]
            budget.spend(UNIT_COST["messages.get"] * len(chunk))
            errors: dict[str, BaseException] = {}

            def callback(request_id, response, exception, _errors=errors):
                if exception is not None:
                    _errors[request_id] = exception
                else:
                    results[request_id] = response

            if hasattr(service, "new_batch_http_request"):
                batch = service.new_batch_http_request(callback=callback)
                for mid in chunk:
                    batch.add(service.users().messages().get(userId="me", id=mid, format="full"), request_id=mid)
                batch.execute()
            else:  # pragma: no cover - every real and fake service supports batches
                for mid in chunk:
                    try:
                        callback(mid, service.users().messages().get(userId="me", id=mid, format="full").execute(), None)
                    except Exception as exc:
                        callback(mid, None, exc)
            if on_batch is not None:
                landed = [results[mid] for mid in chunk if mid in results]
                if landed:
                    on_batch(landed)
            for mid, exc in errors.items():
                if is_retryable(exc):
                    failed.append(mid)
                elif http_status(exc) == 404:
                    continue                              # deleted between list and get
                else:
                    raise exc
        if not failed or attempt == MAX_RETRIES:
            break
        sleep(delay)
        delay *= 2
        pending = failed
    return [results[i] for i in ids if i in results], failed


# --------------------------------------------------------------------------- state
def state_path(roots: cu.Roots) -> Path:
    return roots.harvest / "gmail.state.json"


def load_state(roots: cu.Roots) -> dict[str, Any]:
    p = state_path(roots)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def save_state(roots: cu.Roots, state: dict[str, Any]) -> None:
    p = state_path(roots)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


HARVEST_ONLY = frozenset({"links", "links_truncated"})   # harvest fields a fresh record may lack


def _rewrite_rows(path: Path, fresh: dict[str, dict[str, Any]], keep_extra: bool) -> int:
    """Replace, in place and atomically, the rows of `path` whose id has a fresh record; every other line is
    kept byte for byte (a malformed one too). keep_extra keeps a row's own non-harvest fields (a classified
    row's `classification`). Returns how many rows were replaced."""
    out: list[str] = []
    replaced = 0
    text = path.read_text(encoding="utf-8")
    # split on "\n" only: str.splitlines() also breaks at U+2028, U+0085 and friends, which JSON written with
    # ensure_ascii=False keeps raw inside strings, and would cut an untouched row in pieces
    for line in (text[:-1] if text.endswith("\n") else text).split("\n"):
        try:
            row = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            row = None
        new = fresh.get(str(row.get("id"))) if isinstance(row, dict) else None
        if new is None:
            out.append(line)
            continue
        merged = dict(new)
        if keep_extra:
            merged.update({k: v for k, v in row.items() if k not in new and k not in HARVEST_ONLY})
        out.append(json.dumps(merged, ensure_ascii=False, sort_keys=True))
        replaced += 1
    if replaced:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return replaced


def replace_rows(roots: cu.Roots, records: list[dict[str, Any]]) -> dict[str, int]:
    """--refresh: the fresh records replace their rows in every harvest file and its classified twin.
    Returns {file name: rows replaced}."""
    fresh = {str(r["id"]): r for r in records}
    counts: dict[str, int] = {}
    for path in sorted(roots.harvest.glob("gmail-*.jsonl")) if roots.harvest.is_dir() else []:
        n = _rewrite_rows(path, fresh, keep_extra=path.name.endswith(".classified.jsonl"))
        if n:
            counts[path.name] = n
    return counts


def existing_ids(roots: cu.Roots) -> set[str]:
    ids: set[str] = set()
    for p in sorted(roots.harvest.glob("gmail-*.jsonl")) if roots.harvest.is_dir() else []:
        if p.name.endswith(".classified.jsonl"):
            continue
        for row in jc.read_jsonl(p):
            if row.get("id"):
                ids.add(str(row["id"]))
    return ids


# --------------------------------------------------------------------------- harvest
def _sort_tail(path: Path, start: int) -> None:
    """Sort the rows this run appended (from byte `start`) by internal date, atomically; earlier rows untouched."""
    if not path.is_file() or path.stat().st_size <= start:
        return
    data = path.read_bytes()
    head, tail = data[:start], data[start:].decode("utf-8")
    rows = [line for line in tail.split("\n") if line.strip()]

    def key(line: str) -> tuple[str, str]:
        try:
            row = json.loads(line)
            return str(row.get("internal_date") or ""), str(row.get("id") or "")
        except json.JSONDecodeError:
            return "", line
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(head + ("\n".join(sorted(rows, key=key)) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def harvest(service: Any, roots: cu.Roots, *, since: date | None = None, query: str | None = None,
            today: date | None = None, budget: QuotaBudget | None = None,
            sleep: Callable[[float], None] = time.sleep, account_email: str | None = None,
            refresh: bool = False, alert_body_cap: int = ALERT_BODY_CAP) -> dict[str, Any]:
    """One harvest run. `refresh` (needs `since`) fetches listed messages again even when already harvested
    and replaces their rows (replace_rows). A run with `query` or `refresh` leaves the incremental cursor."""
    if refresh and since is None:
        raise ValueError("--refresh needs --since: it re-reads a date window, never the history cursor")
    today = today or jc.today()
    budget = budget or QuotaBudget(sleep=sleep)
    state = load_state(roots)
    if not account_email:
        budget.spend(UNIT_COST["getProfile"])
        account_email = _execute(service.users().getProfile(userId="me"), sleep).get("emailAddress")
    seen = existing_ids(roots)

    mode = "date"
    used_query = None
    refs: list[dict] = []
    if since is None and state.get("last_history_id"):
        mode = "history"
        try:
            refs, _ = iter_history_refs(service, str(state["last_history_id"]), budget, sleep)
        except Exception as exc:
            if http_status(exc) != 404:
                raise
            mode = "history->date"
            since = cu.local_date(state.get("last_internal_date")) or backlog_start(today)
    if mode != "history":
        since = since or backlog_start(today)
        used_query = build_query(since, query)
        refs = list(iter_message_refs(service, used_query, budget, sleep))

    wanted: list[str] = []
    for ref in refs:
        mid = str(ref.get("id"))
        if (mid in seen and not refresh) or mid in wanted or SKIP_LABELS & set(ref.get("labelIds") or []):
            continue
        wanted.append(mid)
    cutoff = datetime.combine(since, datetime.min.time(), tzinfo=cu.DEPT_TZ) if since and mode != "history" else None
    now = jc.run_stamp(today)
    platforms, registry = alert_platforms(roots.js)
    out = roots.harvest / f"gmail-{today:%Y-%m-%d}.jsonl"
    start = out.stat().st_size if out.is_file() else 0          # this run's rows start here (sorted at the end)
    records: list[dict] = []
    history_ids: list[int] = []

    def land(batch: list[dict]) -> None:
        """Append a fetched batch at once (records, not memory, carry a run that dies)."""
        for msg in batch:
            if str(msg.get("historyId") or "").isdigit():
                history_ids.append(int(msg["historyId"]))
            if SKIP_LABELS & set(msg.get("labelIds") or []):
                continue
            rec = extract_record(msg, account_email=account_email, harvested_at=now, platforms=platforms,
                                 alert_body_cap=alert_body_cap)
            if cutoff and rec["internal_date"] and cu.parse_ts(rec["internal_date"]) < cutoff:
                continue
            records.append(rec)
            if rec["id"] not in seen:
                jc.append_jsonl(out, rec)

    _, residue = fetch_messages(service, wanted, budget, sleep, on_batch=land)
    _sort_tail(out, start)

    fresh = [r for r in records if r["id"] in seen]            # --refresh only: rows already harvested
    added = [r for r in records if r["id"] not in seen]
    refreshed = replace_rows(roots, fresh) if fresh else {}

    # A filtered or refresh run listed only part of the mailbox: moving the cursor past what it did not
    # list would make the next incremental run skip that mail.
    # Mail still rate-limited (the residue) was listed but not fetched: the cursor waits for it too.
    moves_cursor = not refresh and not (query and query.strip()) and not residue
    if moves_cursor:
        if history_ids:
            state["last_history_id"] = str(max(history_ids + [int(state.get("last_history_id") or 0)]))
        dates = [r["internal_date"] for r in records if r["internal_date"]]
        if dates and max(dates) > str(state.get("last_internal_date") or ""):
            state["last_internal_date"] = max(dates)
    state["account"] = account_email
    runs = list(state.get("runs") or [])
    runs.append({"at": now, "mode": mode, "query": used_query, "listed": len(refs), "new": len(added),
                 "refreshed": sum(refreshed.values()), "units": budget.spent, "file": out.name,
                 "cursor_moved": moves_cursor, "residue": len(residue)})
    state["runs"] = runs[-20:]
    save_state(roots, state)
    return {"mode": mode, "query": used_query, "listed": len(refs), "new": len(added), "residue": len(residue),
            "skipped_existing": len(refs) - len(wanted), "file": str(out) if added else None,
            "refreshed": sum(refreshed.values()), "refreshed_files": refreshed, "cursor_moved": moves_cursor,
            "state": str(state_path(roots)), "units": budget.spent,
            "alert_registry": f"targets/platforms.yaml {registry}",
            "alerts_with_links": sum(1 for r in records if "links" in r),
            "alerts_body_truncated": sum(1 for r in records if "links" in r and r["body_truncated"])}


# --------------------------------------------------------------------------- fake Gmail
class FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError closely enough for our error handling."""

    def __init__(self, status: int, reason: str = ""):
        super().__init__(f"<HttpError {status} {reason}>")
        self.status_code = status
        self.resp = SimpleNamespace(status=status, reason=reason)


class _Req:
    def __init__(self, fn: Callable[[], Any]):
        self._fn = fn

    def execute(self) -> Any:
        return self._fn()


class _Batch:
    def __init__(self, svc: "FakeGmailService", callback: Callable | None):
        self.svc, self.callback, self.requests = svc, callback, []

    def add(self, request: _Req, request_id: str | None = None, callback: Callable | None = None) -> None:
        self.requests.append((request, request_id or str(len(self.requests)), callback or self.callback))

    def execute(self) -> None:
        self.svc.batch_sizes.append(len(self.requests))
        for request, rid, cb in self.requests:
            try:
                response, exc = request.execute(), None
            except Exception as err:  # noqa: BLE001 - delivered to the callback like the real client
                response, exc = None, err
            if cb:
                cb(rid, response, exc)


class FakeGmailService:
    """In-memory stand-in for the googleapiclient Gmail resource (used by tests and --fake).

    Supports users.getProfile, messages.list (with `after:` filtering and paging), messages.get,
    history.list (messageAdded, 404 when `history_expired`), drafts.create and batch requests.
    It has no send method at all."""

    def __init__(self, messages: list[dict], *, email: str = "me@example.com", page_size: int | None = None,
                 fail_first_gets: int = 0, history_expired: bool = False, rate_limited_ids: set | None = None,
                 broken_ids: dict | None = None):
        self.store: dict[str, dict] = {}
        self.email, self.page_size = email, page_size
        self.fail_gets, self.history_expired = fail_first_gets, history_expired
        self.rate_limited_ids = set(rate_limited_ids or ())       # always 429 (a residue)
        self.broken_ids = dict(broken_ids or {})                   # id -> a non-retryable status (the run dies)
        self.calls: list[tuple[str, dict]] = []
        self.batch_sizes: list[int] = []
        self.created_drafts: list[dict] = []
        for m in messages:
            self.add_message(m)

    @classmethod
    def from_file(cls, path: Path) -> "FakeGmailService":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return cls(data)
        return cls(data.get("messages") or [], email=data.get("email", "me@example.com"))

    def add_message(self, msg: dict) -> None:
        self.store[str(msg["id"])] = msg

    # resource chain: service.users().messages().list(...).execute() ------
    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def getProfile(self, userId: str = "me"):
        self.calls.append(("getProfile", {}))
        top = max((int(m.get("historyId") or 0) for m in self.store.values()), default=0)
        return _Req(lambda: {"emailAddress": self.email, "messagesTotal": len(self.store), "historyId": str(top)})

    def new_batch_http_request(self, callback: Callable | None = None) -> _Batch:
        return _Batch(self, callback)

    def history(self):
        return _History(self)

    def drafts(self):
        return _Drafts(self)


class _Messages:
    def __init__(self, svc: FakeGmailService):
        self.svc = svc

    def _matching(self, q: str | None) -> list[dict]:
        rows = sorted(self.svc.store.values(), key=lambda m: int(m.get("internalDate") or 0), reverse=True)
        m = re.search(r"after:(\d{4})/(\d{2})/(\d{2})", q or "")
        if m:
            floor = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=cu.DEPT_TZ).timestamp() * 1000
            rows = [r for r in rows if int(r.get("internalDate") or 0) >= floor]
        if "-in:spam" in (q or ""):
            rows = [r for r in rows if not SKIP_LABELS & set(r.get("labelIds") or [])]
        m = re.search(r"from:\(([^)]*)\)|from:(\S+)", q or "")
        if m:
            terms = [t.lower() for t in re.split(r"\s+OR\s+", m.group(1) or m.group(2)) if t.strip()]
            rows = [r for r in rows if any(t in _part_headers(r.get("payload") or {}).get("from", "").lower()
                                           for t in terms)]
        return rows

    def list(self, userId: str = "me", q: str | None = None, maxResults: int = 100, pageToken: str | None = None,
             includeSpamTrash: bool = False):
        self.svc.calls.append(("messages.list", {"q": q, "pageToken": pageToken, "maxResults": maxResults}))

        def run():
            rows = self._matching(q)
            size = self.svc.page_size or maxResults
            start = int(pageToken or 0)
            page = rows[start:start + size]
            resp: dict[str, Any] = {"messages": [{"id": r["id"], "threadId": r["threadId"]} for r in page],
                                    "resultSizeEstimate": len(rows)}
            if start + size < len(rows):
                resp["nextPageToken"] = str(start + size)
            return resp
        return _Req(run)

    def get(self, userId: str = "me", id: str = "", format: str = "full"):
        self.svc.calls.append(("messages.get", {"id": id, "format": format}))

        def run():
            if self.svc.fail_gets > 0:
                self.svc.fail_gets -= 1
                raise FakeHttpError(429, "rateLimitExceeded")
            if id in self.svc.rate_limited_ids:
                raise FakeHttpError(429, "rateLimitExceeded")
            if id in self.svc.broken_ids:
                raise FakeHttpError(self.svc.broken_ids[id], "forbidden")
            if id not in self.svc.store:
                raise FakeHttpError(404, "notFound")
            return json.loads(json.dumps(self.svc.store[id]))
        return _Req(run)


class _History:
    def __init__(self, svc: FakeGmailService):
        self.svc = svc

    def list(self, userId: str = "me", startHistoryId: str = "0", historyTypes: list[str] | None = None,
             pageToken: str | None = None):
        self.svc.calls.append(("history.list", {"startHistoryId": startHistoryId, "pageToken": pageToken}))

        def run():
            if self.svc.history_expired:
                raise FakeHttpError(404, "Requested entity was not found.")
            newer = sorted((m for m in self.svc.store.values() if int(m.get("historyId") or 0) > int(startHistoryId)),
                           key=lambda m: int(m["historyId"]))
            top = max((int(m.get("historyId") or 0) for m in self.svc.store.values()), default=0)
            return {"history": [{"id": m["historyId"], "messagesAdded": [{"message": {
                "id": m["id"], "threadId": m["threadId"], "labelIds": m.get("labelIds") or []}}]} for m in newer],
                "historyId": str(top)}
        return _Req(run)


class _Drafts:
    def __init__(self, svc: FakeGmailService):
        self.svc = svc

    def create(self, userId: str = "me", body: dict | None = None):
        self.svc.calls.append(("drafts.create", {"body": body}))

        def run():
            draft_id = f"r-fake-{len(self.svc.created_drafts) + 1:04d}"
            message = dict((body or {}).get("message") or {})
            thread = message.get("threadId") or f"t-fake-{len(self.svc.created_drafts) + 1:04d}"
            self.svc.created_drafts.append({"id": draft_id, "message": message})
            return {"id": draft_id, "message": {"id": f"m-{draft_id}", "threadId": thread, "labelIds": ["DRAFT"]}}
        return _Req(run)


# --------------------------------------------------------------------------- CLI
def _service(args: argparse.Namespace, kind: str) -> Any:
    """harvest -> the READ token only; draft -> the COMPOSE token only (never the other way round)."""
    if args.fake:
        return FakeGmailService.from_file(Path(args.fake))
    return gmail_auth.build_service(kind)


def _roots(args: argparse.Namespace) -> cu.Roots:
    return cu.Roots(js=Path(args.root) if args.root else jc.JS_ROOT,
                    os=Path(args.os_root) if getattr(args, "os_root", None) else
                    (jc.os_root_for(Path(args.root)) if args.root else jc.OS_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("harvest", "draft", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", help="job-search root (default: this department)")
        p.add_argument("--os-root", help="runtime root (default: derived from --root)")
        p.add_argument("--fake", help="JSON mailbox for the in-memory fake Gmail (tests, demos)")
        p.add_argument("--today", help="override today's date (YYYY-MM-DD)")
        if name == "harvest":
            p.add_argument("--since", help=f"first day to harvest (default: incremental, or {backlog_start()})")
            p.add_argument("--query", help="extra Gmail search terms, e.g. 'label:inbox'")
            p.add_argument("--units-per-minute", type=int, default=DEFAULT_UNITS_PER_MINUTE)
            p.add_argument("--no-throttle", action="store_true", help="disable the quota budget (fake only)")
            p.add_argument("--alert-senders", action="store_true",
                           help="list only mail from targets/platforms.yaml alert_senders (added to --query)")
            p.add_argument("--refresh", action="store_true",
                           help="fetch already-harvested messages again and replace their rows (needs --since)")
            p.add_argument("--alert-body-cap", type=int, default=ALERT_BODY_CAP,
                           help=f"characters kept per job-alert body (default {ALERT_BODY_CAP:,})")
        if name == "draft":
            p.add_argument("--approval", required=True)
            p.add_argument("--item", action="append", help="only these outbox ids (repeatable)")
    args = ap.parse_args(argv)
    roots = _roots(args)
    today = jc.parse_date(args.today) if args.today else jc.today()

    if args.cmd == "status":
        state = load_state(roots)
        print(json.dumps({"state": state, "harvest_files": sorted(p.name for p in roots.harvest.glob("gmail-*.jsonl"))}
                         if roots.harvest.is_dir() else {"state": state}, indent=2, default=str))
        return 0
    if args.cmd == "harvest":
        query = args.query
        if args.refresh and not args.since:
            print(json.dumps({"error": "--refresh needs --since (a date window to re-read)"}, indent=2))
            return 2
        if args.alert_body_cap < BODY_CAP:
            print(json.dumps({"error": f"--alert-body-cap must be at least {BODY_CAP}"}, indent=2))
            return 2
        if args.alert_senders:
            platforms, registry = alert_platforms(roots.js)
            senders = alert_sender_query(platforms)
            if not senders:
                print(json.dumps({"error": f"--alert-senders: targets/platforms.yaml {registry}, no alert senders"},
                                 indent=2))
                return 2
            query = f"{senders} {query}".strip() if query else senders
        try:
            service = _service(args, "read")
        except gmail_auth.AuthError as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        budget = QuotaBudget(None if args.no_throttle else args.units_per_minute)
        summary = harvest(service, roots, since=jc.parse_date(args.since) if args.since else None,
                          query=query, today=today, budget=budget, refresh=args.refresh,
                          alert_body_cap=args.alert_body_cap)
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == "draft":
        approval = Path(args.approval)
        problems = cu.approval_problems(cu.parse_approval(approval), today) if approval.is_file() else [
            f"no approval file at {approval}"]
        if problems:                    # checked before the compose token is even looked up
            print(json.dumps({"refused": problems}, indent=2))
            return 2
        try:
            service = _service(args, "compose")
        except gmail_auth.AuthError as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        try:
            created = create_drafts(service, roots, approval, today=today, item_ids=args.item)
        except DraftRefused as exc:
            print(json.dumps({"refused": str(exc)}, indent=2))
            return 2
        print(json.dumps(created, indent=2))
        return 2 if any(r.get("refused") for r in created) else 0
    return 1


# --------------------------------------------------------------------------- Gmail drafts
def build_reply_mime(meta: dict[str, Any], body: str, root: Path | None = None) -> EmailMessage:
    """RFC 2822 reply: same subject, In-Reply-To + References so Gmail keeps it in the thread.
    Approved attachments (paths relative to the department root) are attached as files."""
    msg = EmailMessage()
    msg["To"] = meta["to"]
    if meta.get("subject"):
        msg["Subject"] = meta["subject"]
    if meta.get("in_reply_to"):
        msg["In-Reply-To"] = meta["in_reply_to"]
    refs = meta.get("references") or meta.get("in_reply_to")
    if refs:
        msg["References"] = refs
    msg.set_content(cu.canonical_text(body) + "\n")
    for rel in meta.get("attachments") or []:
        path = Path(root or jc.JS_ROOT) / str(rel)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def create_drafts(service: Any, roots: cu.Roots, approval_path: Path, *, today: date | None = None,
                  item_ids: list[str] | None = None, budget: QuotaBudget | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Gmail drafts (never sends) for the ticked email items of an approved, unexpired approval whose
    texts still hash to what was approved. Idempotent: an item that has a draft id is skipped."""
    today = today or jc.today()
    budget = budget or QuotaBudget(sleep=sleep)
    appr = cu.parse_approval(Path(approval_path))
    problems = cu.approval_problems(appr, today)
    if problems:
        raise DraftRefused("; ".join(problems))
    results: list[dict[str, Any]] = []
    for item in appr.items:
        item_id = str(item.get("id"))
        if item.get("channel") != "email" or (item_ids and item_id not in item_ids):
            continue
        if not appr.decisions.get(item_id):
            results.append({"item": item_id, "skipped": "unticked"})
            continue
        path = roots.outbox / f"{item_id}.md"
        if not path.is_file():
            results.append({"item": item_id, "refused": "outbox draft missing"})
            continue
        meta, body = cu.read_record(path)
        if meta.get("status") in ("rejected", "expired", "superseded", "sent", "replied", "bounced"):
            results.append({"item": item_id, "skipped": meta.get("status")})
            continue
        current = cu.draft_content_hash(roots.js, meta, body)
        if current != item.get("content_hash"):
            results.append({"item": item_id, "refused": "content hash mismatch: the draft changed after approval"})
            continue
        if meta.get("gmail_draft_id"):
            results.append({"item": item_id, "draft_id": meta["gmail_draft_id"], "existing": True})
            continue
        mime = build_reply_mime(meta, body, roots.js)
        message: dict[str, Any] = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")}
        if meta.get("thread_id"):
            message["threadId"] = meta["thread_id"]
        budget.spend(UNIT_COST["drafts.create"])
        resp = _execute(service.users().drafts().create(userId="me", body={"message": message}), sleep)
        now = jc.run_stamp(today)
        meta.update({"gmail_draft_id": resp["id"], "gmail_draft_created_at": now})
        if meta.get("status") in ("drafted", "pending"):
            meta["status"] = "approved"
        cu.write_record(path, meta, body)
        thread = (resp.get("message") or {}).get("threadId") or meta.get("thread_id")
        cu.append_receipt(Path(approval_path), f"{now} gmail draft {resp['id']} created for {item_id} in thread "
                                               f"{thread} (not sent: the owner sends it from Gmail)")
        results.append({"item": item_id, "draft_id": resp["id"], "thread_id": thread})
    return results


if __name__ == "__main__":
    sys.exit(main())
