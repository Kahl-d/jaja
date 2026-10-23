#!/usr/bin/env python3
"""approvals.py - the one implementation of the approval rules (schemas/approval.yaml, templates/approval.md).

The queue lives under the RUNTIME root (scripts/agency_paths.py): <runtime>/work/approvals/A-YYYY-NNNN.md; receipts go to
<runtime>/logs/runs/sender/<date>.md. `--root R` points every command at another runtime folder.

content_hash ("Content hash" (templates/approval.md)): sha256 of the approval's `## Exact content` section, i.e.
the text after that heading line up to the next level-1 or level-2 heading outside a fenced code
block (or the end of the file), with line endings normalised to LF, trailing whitespace removed
from every line, and leading and trailing blank lines dropped; UTF-8; written `sha256:<64 hex>`.
A bare 64-hex value is read as the same digest. Matches the job-search outbox helper
(js_comms_util.sha256_text over section_text(body, "Exact content")).

Expiry ("Expiry" (templates/approval.md)): `expires` = `created` + 7 days, written when the file is requested; a
missing `expires` is computed that way. The approval is expired when today is after `expires`.

    python3 scripts/kernel/approvals.py hash <approval.md>
        print the content_hash the file's Exact content section has now (exit 2: no section)
    python3 scripts/kernel/approvals.py verify <approval.md> [--today YYYY-MM-DD] [--target T]
        check every gate the sender needs (status approved, decided_at set, not expired, hash
        matches, and the target when given); one OK/FAIL line per gate, then RESULT: GO or
        RESULT: REFUSE. Exit 0 GO, 1 REFUSE, 2 unreadable or no Exact content section.
    python3 scripts/kernel/approvals.py receipt <approval.md> --result sent --message-id <id>
            [--channel-log <path> | --no-channel-log] [--today YYYY-MM-DD] [--root R]
        after a send: append one receipt line (when, sent, action -> target, message id, the
        content hash, the time source) under the approval's `## Receipts` (the heading is created
        if absent) and a `## Run <ISO> · ok` section to logs/runs/sender/<date>.md
        (watchdog.py's receipt format). The time is the send time the channel itself logged: the
        last timestamped line carrying the message id in --channel-log, else in the default channel
        log <root>/channel/sent.jsonl (where the connector records each send: one JSON line with
        message_id and at, as send_stub.py writes it). The caller cannot supply a time. Exit 2 and
        write nothing when the file is unreadable, the log is missing, or no timestamped line in it
        carries the message id. --no-channel-log is the one escape, for a connector that keeps no
        send log (gmail-send over MCP): the time is the system clock when the receipt is written,
        and the receipt says "time source: clock".
    python3 scripts/kernel/approvals.py refuse <approval.md | A-YYYY-NNNN> [--reason TEXT]
            [--today YYYY-MM-DD] [--target T] [--root R]
        (also: receipt <approval> --result refused ...) log one refusal: a `## Run <ISO> · warn`
        section (sentinel NEEDS_YOU) with one `- refused <id>` line carrying the reason and every
        FAIL line verify gives for the same --today and --target, and, when the file exists, the
        same note under its `## Receipts`. A missing file is logged by id and never created. Exit 2
        and write nothing when every gate passes and no --reason is given.

    python3 scripts/kernel/approvals.py decide [A-YYYY-NNNN | <approval.md>] --ids ID [ID ...] --via chat
            --quote "<their words>" --decision yes|no [--session S] [--target T --line TEXT ...] [--today D]
        record the owner's decision in their exact words (charter §6, §7: the card or batch yes is the approval; nothing
        here sends). With a pending file (an outbox batch, for example): status approved or rejected, decided_at
        (the clock), decided_via, decided_quote, decided_session and approved_items are written, the Decisions
        checklist is ticked for exactly --ids (an id the file does not list is refused), and a decision line goes
        under ## Receipts; the Exact content is never touched, so content_hash holds and `outbox.py can-send` and
        `verify` pass. Without a file: one new decided file for the batch (one approval per batch, listing the
        items), its Exact content the --line texts. A decided or expired file is never re-decided. Exit 2 on a
        refusal, writing nothing.

--today is the run's day, as passed to verify: it names the sender log file (and the day the
gates are checked on); it never changes a recorded time. receipt and refuse append only below
`## Receipts`, so frontmatter and content_hash are never touched. hash and verify read only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontmatter as fm  # noqa: E402
import watchdog  # noqa: E402  (the one receipt format)

EXPIRY_DAYS = 7
SECTION = "exact content"
RECEIPTS = "receipts"
LOG_NAME = "sender"
CHANNEL_LOG = Path("channel") / "sent.jsonl"      # the connector's send log, under --root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_paths as _ap  # noqa: E402
DEFAULT_ROOT = _ap.RUNTIME_ROOT                     # work/approvals and logs/runs live under the runtime root
ID_RE = re.compile(r"A-\d{4}-\d{4}")
_FM_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*?)\s*$")


def load(path: str | Path) -> tuple[dict[str, Any], str]:
    return fm.load(path)


def canonical_text(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def exact_content(body: str) -> str | None:
    """The canonical text of the `## Exact content` section, or None when there is none."""
    fence: str | None = None
    capturing = False
    found = False
    captured: list[str] = []
    for line in body.splitlines(keepends=True):
        m = _FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
        elif m:
            fence = m.group(1)
        else:
            h = _HEADING_RE.match(line)
            if h:
                if capturing:
                    break
                if h.group(2).strip().lower() == SECTION:
                    capturing = found = True
                    continue
        if capturing:
            captured.append(line)
    return canonical_text("".join(captured)) if found else None


def content_hash_of_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def content_hash(path: str | Path) -> str | None:
    _, body = load(path)
    exact = exact_content(body)
    return None if exact is None else content_hash_of_text(exact)


def _digest(value: Any) -> str:
    s = str(value or "").strip().strip('"').lower()
    return s.split(":", 1)[1] if s.startswith("sha256:") else s


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def expires_on(meta: dict[str, Any]) -> dt.date | None:
    explicit = _date(meta.get("expires"))
    if explicit:
        return explicit
    created = _date(meta.get("created"))
    return created + dt.timedelta(days=EXPIRY_DAYS) if created else None


def gates(meta: dict[str, Any], body: str, today: dt.date, target: str | None = None) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    status = str(meta.get("status") or "")
    out.append((status == "approved", f"status {status or '(empty)'}" + ("" if status == "approved" else " is not approved")))
    decided = meta.get("decided_at")
    out.append((decided not in (None, ""), f"decided_at {decided}" if decided not in (None, "") else "decided_at is empty"))
    exp = expires_on(meta)
    if exp is None:
        out.append((False, "expires unknown (no expires and no created date)"))
    else:
        live = today <= exp
        out.append((live, f"expires {exp.isoformat()}" + ("" if live else f": expired (today {today.isoformat()} is after it)")))
    exact = exact_content(body)
    have = content_hash_of_text(exact) if exact is not None else None
    want = _digest(meta.get("content_hash"))
    ok = bool(want) and have is not None and _digest(have) == want
    out.append((ok, f"content_hash {have}" + ("" if ok else f" does not match the recorded {meta.get('content_hash') or '(empty)'}")))
    if target is not None:
        recorded = str(meta.get("target") or "").strip()
        same = recorded == target.strip()
        out.append((same, f"target {recorded}" + ("" if same else f" differs from the requested {target}")))
    return out


# --------------------------------------------------------------------------- #
# receipts and refusals (the sender's only writes)
# --------------------------------------------------------------------------- #
def _now() -> dt.datetime:
    """The system clock (aware, local, to the second). Tests patch this; callers cannot."""
    return dt.datetime.now().astimezone().replace(microsecond=0)


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def append_receipt_line(text: str, line: str) -> str:
    """`text` with `line` appended at the end of its `## Receipts` section (created at the end if absent)."""
    head = _FM_RE.match(text.replace("\r\n", "\n"))
    lines = text.splitlines(keepends=True)
    start = head.group(0).count("\n") if head else 0
    fence: str | None = None
    heading_at: int | None = None
    end = len(lines)
    for i in range(start, len(lines)):
        m = _FENCE_RE.match(lines[i])
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        h = _HEADING_RE.match(lines[i])
        if not h:
            continue
        if heading_at is not None:
            end = i
            break
        if h.group(2).strip().lower() == RECEIPTS:
            heading_at = i
    if heading_at is None:
        body = text if text.endswith("\n") or not text else text + "\n"
        return body + "\n## Receipts\n" + line + "\n"
    section = lines[heading_at + 1:end]
    while section and not section[-1].strip():
        section.pop()
    if section and not section[-1].endswith("\n"):
        section[-1] += "\n"
    tail = lines[end:]
    return "".join(lines[:heading_at + 1] + section + [line + "\n"] + (["\n"] if tail else []) + tail)


def _append_to_approval(path: Path, line: str) -> None:
    new = append_receipt_line(path.read_text(encoding="utf-8"), line)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new)
    os.replace(tmp, path)


def _stamp_of(line: str) -> dt.datetime | None:
    try:
        row = json.loads(line)
    except ValueError:
        row = None
    if isinstance(row, dict):
        for key in ("at", "sent_at", "time", "timestamp", "date"):
            if row.get(key):
                found = watchdog.parse_stamp(str(row[key]))
                if found:
                    return found
    return watchdog.parse_stamp(line)


def channel_time(log: Path, message_id: str) -> tuple[dt.datetime, int]:
    """(local time, 1-based line number) of the channel log's last line carrying `message_id`."""
    hit: tuple[dt.datetime, int] | None = None
    for n, line in enumerate(log.read_text(encoding="utf-8").splitlines(), start=1):
        if message_id in line:
            stamp = _stamp_of(line)
            if stamp:
                hit = (stamp, n)
    if hit is None:
        raise ValueError(f"no timestamped line in {log} carries message id {message_id}")
    return hit[0].astimezone().replace(microsecond=0), hit[1]


def _iso(when: dt.datetime) -> str:
    return when.astimezone().isoformat(timespec="seconds")


def _rel(root: Path, path: Path) -> str:
    return watchdog.rel(root.resolve(), path.resolve())


def write_sent(approval: Path, message_id: str, root: Path, day: dt.date | None,
               channel_log: Path | None = None, use_clock: bool = False) -> tuple[str, Path]:
    """Write the sent receipt. The time comes from `channel_log` (default <root>/channel/sent.jsonl);
    `use_clock` (--no-channel-log) takes the system clock and says so. Raises ValueError, writing nothing,
    when the log is missing or carries no timestamped line for `message_id`."""
    meta, body = load(approval)
    exact = exact_content(body)
    if exact is None:
        raise ValueError(f"{approval} has no '## Exact content' section")
    if use_clock:
        when, source = _now(), "clock (--no-channel-log: the connector keeps no send log; when this receipt was written)"
    else:
        log_path = channel_log if channel_log is not None else root / CHANNEL_LOG
        if not log_path.is_file():
            raise ValueError(f"no channel log at {log_path}: the send time comes from the connector's log; pass "
                             "--channel-log <its log>, or --no-channel-log only when the connector keeps none")
        when, n = channel_time(log_path, message_id)
        source = f"channel log {_rel(root, log_path)} line {n}"
    have = content_hash_of_text(exact)
    same = _digest(have) == _digest(meta.get("content_hash"))
    aid = _one_line(meta.get("id")) or approval.stem
    what = f"{_one_line(meta.get('action')) or '?'} → {_one_line(meta.get('target')) or '?'}"
    content = f"content {have}" + (" (matches content_hash)" if same else " (DOES NOT match content_hash)")
    line = f"- {_iso(when)} · sent · {what} · message id {message_id} · {content} · time source: {source}"
    _append_to_approval(approval, line)
    note = f"sent {aid} ({_rel(root, approval)}) · {what} · message id {message_id} · {content} · time source: {source}"
    log = watchdog.write_receipt(root, LOG_NAME, when, status="ok", notes=[note], day=day)
    return line, log


def write_refusal(ref: str, root: Path, today: dt.date, reason: str | None,
                  target: str | None) -> tuple[str | None, str, Path]:
    """(approval note or None, log line, log path). Raises ValueError when there is nothing to refuse for."""
    reason = _one_line(reason) or None
    path = root / "work" / "approvals" / f"{ref}.md" if ID_RE.fullmatch(ref) else Path(ref)
    aid, where, fails, readable = path.stem, _rel(root, path), [], False
    if not path.is_file():
        fails.append(f"no approval file at {where}")
    else:
        try:
            meta, body = load(path)
            readable = True
        except (OSError, fm.FrontmatterError) as exc:
            fails.append(f"unreadable approval {where}: {_one_line(exc)}")
        if readable:
            aid = _one_line(meta.get("id")) or aid
            if exact_content(body) is None:
                fails.append("no '## Exact content' section")
            fails += ["FAIL " + t for ok, t in gates(meta, body, today, target) if not ok]
    if not fails and not reason:
        raise ValueError(f"{aid} passes every gate for {today.isoformat()}: a refusal needs --reason")
    parts = ([f"reason: {reason}"] if reason else []) + [f for f in fails if not reason or f not in reason]
    text = "; ".join(parts) + " · nothing was sent"
    when = _now()
    log_line = f"refused {aid} ({where}): {text}"
    log = watchdog.write_receipt(root, LOG_NAME, when, status="warn", notes=[log_line], sentinel="NEEDS_YOU", day=today)
    note = None
    if readable:
        note = f"- {_iso(when)} · refused · {text}"
        _append_to_approval(path, note)
    return note, "- " + log_line, log


# --------------------------------------------------------------------------- #
# decide: the owner's yes or no, recorded with their exact words (charter §6, §7)
# --------------------------------------------------------------------------- #
VIAS = ("chat", "desktop", "phone")
_TICK_RE = re.compile(r"^(\s*[-*]\s*\[)( |x|X)(\]\s+)(\S+)(.*)$")


def split_ids(values: list[str] | None) -> list[str]:
    """--ids a b,c -> [a, b, c], order kept, duplicates once."""
    out: list[str] = []
    for v in values or []:
        out += [p.strip() for p in str(v).split(",") if p.strip()]
    return list(dict.fromkeys(out))


def _approvals_dir(root: Path) -> Path:
    return root / "work" / "approvals"


def _resolve(root: Path, ref: str) -> Path:
    return _approvals_dir(root) / f"{ref}.md" if ID_RE.fullmatch(ref) else Path(ref)


def _file_item_ids(meta: dict[str, Any], body: str) -> list[str]:
    """The ids an approval file lists: its `items:` (the outbox's), else its Decisions lines."""
    ids = [str(i.get("id")) for i in meta.get("items") or [] if isinstance(i, dict) and i.get("id")]
    if ids:
        return ids
    found: list[str] = []
    for line in body.splitlines():
        m = _TICK_RE.match(line)
        if m:
            found.append(m.group(4))
    return found


def _retick(body: str, approved: set[str]) -> str:
    """Tick exactly the approved ids in the checklist (outside the Exact content, so the hash holds)."""
    out = []
    for line in body.splitlines(keepends=True):
        m = _TICK_RE.match(line.rstrip("\n"))
        if m:
            nl = "\n" if line.endswith("\n") else ""
            line = f"{m.group(1)}{'x' if m.group(4) in approved else ' '}{m.group(3)}{m.group(4)}{m.group(5)}{nl}"
        out.append(line)
    return "".join(out)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _new_file(folder: Path, year: int, render) -> tuple[str, Path]:
    """folder/A-<year>-NNNN.md with the next free number, never overwriting (O_EXCL)."""
    folder.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"^A-{year}-(\d{{4}})\.md$")
    n = max([0, *(int(m.group(1)) for p in folder.iterdir() if (m := pat.match(p.name)))]) + 1
    while True:
        aid = f"A-{year}-{n:04d}"
        path = folder / f"{aid}.md"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render(aid))
        return aid, path


def decide(root: Path, ids: list[str], via: str, quote: str, decision: str, *, approval: str | None = None,
           action: str = "submit-form", target: str | None = None, lines: list[str] | None = None,
           requested_by: str = "role:lane-recruiter", session: str | None = None, project: str | None = None,
           today: dt.date | None = None) -> tuple[str, Path, str]:
    """Record the owner's decision. With `approval` (an id or a path): that file's status, decided_at, decided_via, his
    quote, the session and the approved items are written and its checklist ticked for exactly `ids`; the Exact
    content is never touched, so the content hash still holds. Without it: one new approval file for the batch, its
    Exact content the `lines` (one per item, the thing that will be done), decided at once. Returns (id, path, the
    receipt line). Raises ValueError, writing nothing, on a bad input or a file that cannot take the decision."""
    ids = split_ids(ids)
    quote = str(quote or "").strip()
    if decision not in ("yes", "no"):
        raise ValueError(f"--decision must be yes or no, not {decision!r}")
    if via not in VIAS:
        raise ValueError(f"--via must be one of {', '.join(VIAS)}")
    if not quote:
        raise ValueError("--quote is required: the owner's exact words, as written")
    if not ids:
        raise ValueError("--ids is required: the items their words cover")
    when = _now()
    today = today or when.date()
    status = "approved" if decision == "yes" else "rejected"
    sess = _one_line(session or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
                     or "unstated")
    record = {"status": status, "decided_at": _iso(when), "decided_via": via, "decided_quote": quote,
              "decided_session": sess, "approved_items": ids if decision == "yes" else []}
    line = (f"- {_iso(when)} · decided {decision} · via {via} · session {sess} · items {', '.join(ids)} · "
            f"owner's words: \"{_one_line(quote)}\"")
    if approval:
        path = _resolve(root, approval)
        if not path.is_file():
            raise ValueError(f"no approval file at {path}")
        meta, body = load(path)
        aid = _one_line(meta.get("id")) or path.stem
        if exact_content(body) is None:
            raise ValueError(f"{aid} has no '## Exact content' section")
        if str(meta.get("status") or "pending") != "pending":
            raise ValueError(f"{aid} is {meta.get('status')}, not pending: a decided file is never re-decided; "
                             "a new request is a new file")
        exp = expires_on(meta)
        if exp is not None and today > exp:
            raise ValueError(f"{aid} expired on {exp.isoformat()}: re-request it as a new file")
        known = _file_item_ids(meta, body)
        unknown = [i for i in ids if known and i not in known]
        if unknown:
            raise ValueError(f"{aid} has no item {', '.join(unknown)} (its items: {', '.join(known)})")
        meta.update(record)
        if known:
            body = _retick(body, set(ids) if decision == "yes" else set())
        _write_atomic(path, append_receipt_line(fm.dump(meta, body), line))
        return aid, path, line
    if not target or not lines:
        raise ValueError("a new approval needs --target and one --line per item (the Exact content)")
    exact = "\n".join(_one_line(x) for x in lines if _one_line(x))
    created = today

    def render(aid: str) -> str:
        meta = {"type": "approval", "id": aid, "action": action, "requested_by": requested_by, "ticket": None,
                "target": _one_line(target), "draft_ref": [], "diff": "inline: see Exact content", "risk": "low",
                "status": record["status"], "content_hash": content_hash_of_text(exact)}
        meta.update((k, v) for k, v in record.items() if k != "status")
        meta.update(created=created.isoformat(), expires=(created + dt.timedelta(days=EXPIRY_DAYS)).isoformat())
        if project:
            meta["project"] = project
        ticks = "\n".join(f"- [{'x' if decision == 'yes' else ' '}] {i}" for i in ids)
        body = (f"# {action} → {_one_line(target)}\n\n"
                "<!-- Written by scripts/kernel/approvals.py decide: the owner's decision in their own words, recorded "
                "when it was given. Contract: templates/approval.md. -->\n\n"
                f"## Decisions\n{ticks}\n\n## Exact content\n{exact}\n\n"
                "## Why this is being sent\nThe owner's batch answer on the morning cards (charter §3, §7): one yes "
                "covers exactly the items listed.\n\n## Receipts\n" + line + "\n")
        return fm.dump(meta, body)

    aid, path = _new_file(_approvals_dir(root), created.year, render)
    return aid, path, line


def _run_decide(args: argparse.Namespace) -> int:
    root = Path(args.root)
    today = _date(args.today) if args.today else None
    if args.today and today is None:
        print(f"error: --today {args.today!r} is not YYYY-MM-DD")
        return 2
    try:
        aid, path, line = decide(root, args.ids, args.via, args.quote, args.decision, approval=args.approval,
                                 action=args.action, target=args.target, lines=args.line, session=args.session,
                                 project=args.project, today=today)
    except (OSError, fm.FrontmatterError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"{aid} ({_rel(root, path)}): {line[2:]}")
    return 0


def _run_writes(args: argparse.Namespace) -> int:
    root = Path(args.root)
    today = _date(args.today) if args.today else None
    if args.today and today is None:
        print(f"error: --today {args.today!r} is not YYYY-MM-DD")
        return 2
    try:
        if args.cmd == "refuse" or args.result == "refused":
            note, line, log = write_refusal(args.approval, root, today or _now().date(), args.reason, args.target)
            if note:
                print(f"{args.approval} ## Receipts: {note}")
            print(f"{_rel(root, log)}: {line}")
            return 0
        if not args.message_id or not _one_line(args.message_id):
            print("error: --result sent needs --message-id (the id the connector printed)")
            return 2
        if args.target or args.reason:
            print("error: --target and --reason are for refusals; a sent receipt takes its target from the approval")
            return 2
        line, log = write_sent(Path(args.approval), _one_line(args.message_id), root, today,
                               Path(args.channel_log) if args.channel_log else None, args.no_channel_log)
    except (OSError, fm.FrontmatterError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"{args.approval} ## Receipts: {line}")
    print(f"{_rel(root, log)}: appended a run section (status ok)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compute and verify approval content hashes and gates.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hash", help="print the content_hash of the Exact content section")
    h.add_argument("approval")
    v = sub.add_parser("verify", help="check the sender's gates; exit 0 GO, 1 REFUSE, 2 error")
    v.add_argument("approval")
    v.add_argument("--today", help="YYYY-MM-DD (default: the system date)")
    v.add_argument("--target", help="the target named in the run request; must equal the approval's")
    r = sub.add_parser("receipt", help="after a send (or a refusal): write the approval receipt and the sender log")
    f = sub.add_parser("refuse", help="log a refusal to the sender log and the approval's Receipts")
    for sp in (r, f):
        sp.add_argument("approval", help="the approval file (refuse also takes an id, A-YYYY-NNNN)")
        sp.add_argument("--today", help="the run's day as passed to verify (default: the system date)")
        sp.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
        sp.add_argument("--reason", help="why it is refused (required when every gate passes)")
        sp.add_argument("--target", help="refusals: the target from the run request, checked like verify")
    r.add_argument("--result", required=True, choices=("sent", "refused"))
    r.add_argument("--message-id", help="sent: the id the connector printed")
    source = r.add_mutually_exclusive_group()
    source.add_argument("--channel-log", help="sent: the connector's log; its line for the message id gives the time "
                        "(default: <root>/channel/sent.jsonl)")
    source.add_argument("--no-channel-log", action="store_true",
                        help="sent: only for a connector that keeps no send log; the time is the clock, recorded as such")
    d = sub.add_parser("decide", help="record the owner's yes or no with their exact words (one file per batch)")
    d.add_argument("approval", nargs="?", default=None, help="the pending file to decide (A-YYYY-NNNN or a path); "
                   "omit it to write one new decided file for a batch (--target and --line)")
    d.add_argument("--ids", nargs="+", required=True, help="the items their words cover (space or comma separated)")
    d.add_argument("--via", required=True, choices=VIAS)
    d.add_argument("--quote", required=True, help="the owner's exact words")
    d.add_argument("--decision", required=True, choices=("yes", "no"))
    d.add_argument("--session", default=None, help="the session it was said in (default: $CLAUDE_SESSION_ID)")
    d.add_argument("--action", default="submit-form", help="new file: the action (schema enum; default submit-form)")
    d.add_argument("--target", default=None, help="new file: the target (for example 'mass batch C-2026-10-06-061')")
    d.add_argument("--line", action="append", default=None, help="new file: one Exact content line per item")
    d.add_argument("--project", default=None)
    d.add_argument("--today", help="YYYY-MM-DD (default: the system date)")
    d.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.cmd == "decide":
        return _run_decide(args)
    if args.cmd in ("receipt", "refuse"):
        if args.cmd == "refuse":
            args.result = "refused"
        return _run_writes(args)
    try:
        meta, body = load(args.approval)
    except (OSError, fm.FrontmatterError) as exc:
        print(f"error: cannot read {args.approval}: {exc}")
        return 2
    if exact_content(body) is None:
        print(f"error: {args.approval} has no '## Exact content' section")
        return 2
    if args.cmd == "hash":
        print(content_hash_of_text(exact_content(body) or ""))
        return 0
    today = _date(args.today) if args.today else dt.date.today()
    if today is None:
        print(f"error: --today {args.today!r} is not YYYY-MM-DD")
        return 2
    results = gates(meta, body, today, args.target)
    for ok, text in results:
        print(("OK " if ok else "FAIL ") + text)
    go = all(ok for ok, _ in results)
    print("RESULT: GO" if go else "RESULT: REFUSE")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
