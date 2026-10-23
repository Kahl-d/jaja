#!/usr/bin/env python3
"""Render the job-search dashboard: DASHBOARD.md (for the owner and HOME) + state/dashboard.json (machines).

Sections (design §10): Needs the owner (pending job-search approvals in work/approvals, counting the outbox drafts
they hold; mail waiting for review: state/review/mail-<date>.md items whose `decision:` is blank and
state/mail-events.jsonl messages whose latest row says decision review or queued), Agency backlog (the roles' own
work, never the owner's: jobs with gate: review that fit-scorer settles; open outbox drafts outside the owner's pending
approvals: unfiled, a missing approval file, approved but not synced, or an approval no longer pending), Backlog CRM (prospects by status/priority, follow-ups due), Pipeline (applications by
projected stage), Fresh fits (discovered <= 72 h with fit_verdict apply/priority; scoring queue),
Interviews next 7 days, Visa timers (legal/timers.md frontmatter), Metrics (tracker metrics +
discovery counts). The top lines are the 90-day job clock (clock.yaml through scripts/clock.py):
"Job clock: day N of 90 · D days left · ends YYYY-MM-DD (provisional|confirmed)", then week, phase and
unemployment days.

Status (design §8, R9: the one status; TRACKER.md, plan.md and progress.md retire into hub/archive/): the first
section after the clock lines. Funnel (tracker.folder_counts: folders = applications + package-only + untagged, then
stages reached and ended from tracker metrics), Next actions due (open applications whose next_action_at is today or
earlier; the header counts the past-due ones and the oldest due date, and says when applying is paused), Waiting on the owner (approvals, offers, mail
review: when mail-triage last ran from its receipt, or that it has not run and why, and the last Gmail pull; never a
bare "no data"), Agency backlog (fit-scorer's gate reviews), Waiting on companies (applications at
applied to final, by days since the last activity), Waiting on people (open pursuits). Lists stop at STATUS_CAP lines.
The Recruiter reads it with --status; HOME gets one "Job search: N due today, M waiting on companies" today row
("N next actions due (K past due)" when some are late; "while applying is paused" while holds).

FOCUS next: after the clock lines, the top 10 of the ranked list from next_up.focus_for (rank, state, role, rate,
age, why), recalculated from the records on every write (age decay, warmth, STALE parked; see scripts/next_up.py). build()
adds it only when asked (focus=True: main without --status, and pipeline.py), because ranking takes about 10 s. Refresh
alone: `python3 scripts/next_up.py --write --focus`.

Role hubs: every write (not --dry-run) also refreshes the per-role record hubs through scripts/role_hub.py
(applications/<slug>/HUB.md and the index state/role-hubs.md), so hubs are current whenever DASHBOARD.md is;
each active pipeline line names its hub.

Pursuits: one line per open pursuit (pursuits/<slug>/pursuit.md, scripts/pursuits.py): stage, title,
what it waits on, the next action and date, approvals waiting on its outbox items, its HUB.md; closed ones are
counted. Every write also refreshes the pursuit hubs (scripts/pursuit_hub.py). In the HOME feed a pursuit waiting
on the owner is a needs_you item, and a pursuit's next action due within 7 days is a (soft) deadline.

Presence: one line from state/presence/portals.yaml through scripts/presence_check.py
(surfaces by profile state, unwatched, flags); while any flag stands, the HOME feed carries one needs_you item. A second
line carries the presence KPIs from state/profiles/tools/presence_kpis.py --json (portal coverage, flags, alert
cards on target title per portal against 70%, responses by source); absent or failing, it says "not measured".

LinkedIn digests: today's "<Name> just messaged you" and "You have N new messages" mails in
state/harvest/ raise ONE needs_you item ("check the LinkedIn inbox"), naming the people; the text itself sits in LinkedIn.

HOME feed: state/home-feed.json, read by the OS sweep (scripts/kernel/sweep.py), contract
{"project", "generated_at", "needs_you": [{"title","why","link"}], "deadlines": [{"title","date","hard","link"}],
"today": [{"title","link"}]} with repo-relative links. needs_you: offers awaiting the owner, mail
review, "Confirm the last working day" while clock.yaml is unconfirmed, a missing or unreadable clock.
deadlines: the clock end (hard), the last working day while it is ahead, offer deadlines (offer.md
`deadline`, else the application's next_action_at), interviews within 14 days, pending approvals expiring
within 2 days. today: the clock line, interviews today, follow-ups due. Pending approvals themselves are
not repeated: the sweep lists every pending approval file already.

Absent is not empty: in the JSON a missing source folder/file is null, a present one with no
records is an empty list/zero count. The markdown says "no data yet" for both, and "None right
now." when records exist but nothing matches. Never hand-edit the outputs.

CLI:  python dashboard.py [--root R] [--os-root OS] [--dry-run | --status] [--today YYYY-MM-DD]
      --status prints the clock line and the Status section only and writes nothing.

--today (default: the system date) is the day the dashboard is for: the "Updated" header, approval
ages, follow-ups due, fresh fits, "Interviews next 7 days", visa-timer day counts, discovery windows
and the pipeline projection are all computed as of that day (12:00 UTC when it is not today).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import clock as job_clock  # noqa: E402
import mail_classify as mc  # noqa: E402
import presence_check  # noqa: E402
import build_graph  # noqa: E402
import pursuit_hub  # noqa: E402
import pursuits as pursuit_records  # noqa: E402
import role_hub  # noqa: E402
import tracker  # noqa: E402
from fetch_ats import iso_utc, load_jobs, parse_ts  # noqa: E402
from js_common import os_root_for, JS_ROOT, OS_ROOT, cli_date, dept_day, fm, iter_records, parse_date, read_jsonl  # noqa: E402

def _who() -> str:
    """How the dashboard addresses its reader in section titles: identity.owner_label (default "you")."""
    return str(cfg.get("identity.owner_label", "you"))


def _zone() -> ZoneInfo:
    """The reader's time zone (identity.timezone, default UTC) for the mail pass times."""
    try:
        return ZoneInfo(str(cfg.get("identity.timezone", "UTC")))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _tz_label() -> str:
    return str(cfg.get("identity.timezone_label", "UTC"))


HEADER = "<!-- GENERATED by scripts/dashboard.py — do not edit -->"
NO_DATA = "no data yet"
OPEN_OUTBOX = {"", "none", "draft", "drafted", "pending", "awaiting-approval", "ready"}
CLOSED_PROSPECT = {"closed", "do_not_contact"}
INTERVIEW_STAGES = ("screen", "oa", "interviewing", "final")
ACTIVE_STAGES = ("applied", "confirmation", "screen", "oa", "interviewing", "final", "offer")
FRESH_HOURS = 72
FEED_INTERVIEW_DAYS = 14
FEED_APPROVAL_DAYS = 2
FEED_PURSUIT_DAYS = 7
PROJECT = "job-search"


def _records(folder: Path) -> list[tuple[Path, dict[str, Any], str]] | None:
    return list(iter_records(folder)) if folder.is_dir() else None


def _day(value: Any) -> date | None:
    return parse_date(value)


# ------------------------------------------------------------------------------ sections
def approvals(os_root: Path, now: datetime) -> list[dict[str, Any]] | None:
    recs = _records(Path(os_root) / "work" / "approvals")
    if recs is None:
        return None
    out = []
    for path, meta, _ in recs:
        if str(meta.get("project") or "") != "job-search" or str(meta.get("status") or "") != "pending":
            continue
        created = parse_ts(meta.get("created"))
        expires = _day(meta.get("expires"))
        age_h = int((now - created).total_seconds() // 3600) if created else None
        out.append({"id": meta.get("id") or path.stem, "action": meta.get("action"), "target": meta.get("target"),
                    "created": str(meta.get("created") or ""), "age_hours": age_h,
                    "urgent": bool(age_h is not None and age_h > 24),
                    "expires": expires.isoformat() if expires else None,
                    "expired": bool(expires and expires < dept_day(now)), "path": str(path.name)})
    return sorted(out, key=lambda a: a["created"])


def outbox(root: Path) -> list[dict[str, Any]] | None:
    recs = _records(Path(root) / "outbox")
    if recs is None:
        return None
    return [{"id": meta.get("id") or path.stem, "channel": meta.get("channel"),
             "prospect": meta.get("prospect") or meta.get("to"), "status": meta.get("status") or "draft",
             "created": str(meta.get("created") or ""), "approval": meta.get("approval") or None}
            for path, meta, _ in recs if str(meta.get("status") or "").lower() in OPEN_OUTBOX]


REVIEW_LIST = Path("hub") / "plans" / "APPROVALS-REVIEW.md"      # the owner's one-pass approvals list


def approval_records(os_root: Path) -> dict[str, dict[str, Any]]:
    """{approval id: frontmatter} for every approval file (any project, any status)."""
    recs = _records(Path(os_root) / "work" / "approvals") or []
    return {str(meta.get("id") or path.stem): meta for path, meta, _ in recs}


def approval_statuses(os_root: Path) -> dict[str, str]:
    """{approval id: status} for every approval file (any project, any status)."""
    return {k: str(m.get("status") or "") for k, m in approval_records(os_root).items()}


REDRAFT = re.compile(r"redraft requested", re.I)


def after_approval(root: Path, records: dict[str, dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Approved outbox items not yet out, and drafts sent back for a redraft.
    An approved item counts only while its approval is approved, a job-search approval and not expired; otherwise it is
    messenger's to re-request or re-file. LinkedIn items (no sent_at) are the owner's to paste and send (the
    linkedin-assist hand-off), split by what can be done today: profile edits to apply, connection requests, messages
    ready now; a message to someone whose connection request is still unsent waits on the accept, and one whose checks
    carry a `trigger:` line waits on that trigger. Approved email is the sender's; any other channel is its own row. A
    rejected draft whose approval says "redraft requested" and that no newer draft supersedes is messenger's."""
    recs = _records(Path(root) / "outbox")
    if recs is None:
        return None
    superseded: set[str] = set()
    for _, meta, _ in recs:
        sup = meta.get("supersedes")
        superseded |= {str(x) for x in (sup if isinstance(sup, list) else [sup]) if x}
    out: dict[str, Any] = {"ready": [], "waiting": [], "earliest_expiry": None, "email": [], "other": [],
                           "expired": [], "foreign": [], "redraft": []}
    approved = []
    for path, meta, _ in recs:
        st = str(meta.get("status") or "").lower()
        row = {"id": str(meta.get("id") or path.stem), "channel": str(meta.get("channel") or ""),
               "prospect": meta.get("prospect") or meta.get("to"), "approval": meta.get("approval") or None,
               "checks": [str(c) for c in meta.get("checks") or []]}
        appr = records.get(str(row["approval"] or "")) or {}
        if st == "approved" and not meta.get("sent_at"):
            exp = _day(appr.get("expires"))
            if str(appr.get("status")) != "approved" or str(appr.get("project") or "") != "job-search":
                out["foreign"].append(row)
            elif exp and exp < today:
                out["expired"].append({**row, "expires": exp.isoformat()})
            else:
                approved.append({**row, "expires": exp.isoformat() if exp else None})
        elif st == "rejected" and row["id"] not in superseded:
            if str(appr.get("status")) == "rejected" and REDRAFT.search(str(appr.get("decided_via") or "")):
                out["redraft"].append(row)
    connects = {str(r["prospect"]) for r in approved if r["channel"] == "linkedin-connect"}
    for r in approved:
        ch = r["channel"]
        if ch == "linkedin-profile-edit":
            out["ready"].append({**r, "bucket": "profile edit"})
        elif ch == "linkedin-connect":
            out["ready"].append({**r, "bucket": "connection request"})
        elif ch.startswith("linkedin"):
            trig = next((c for c in r["checks"] if c.lower().startswith("trigger:")), None)
            if str(r["prospect"]) in connects:
                out["waiting"].append({**r, "bucket": "message", "waits_on": "an accepted connection"})
            elif trig:
                out["waiting"].append({**r, "bucket": "message", "waits_on": trig[:120]})
            else:
                out["ready"].append({**r, "bucket": "message"})
        elif ch == "email":
            out["email"].append(r)
        else:
            out["other"].append(r)
    exps = [r["expires"] for r in out["ready"] + out["waiting"] if r.get("expires")]
    out["earliest_expiry"] = min(exps) if exps else None
    for k in ("ready", "waiting", "email", "other", "expired", "foreign", "redraft"):
        out[k].sort(key=lambda r: r["id"])
    return out


def linkedin_summary(li: dict[str, Any]) -> tuple[str, int, int]:
    """("1 profile edit to apply, 26 connection requests, 9 messages ready now; 29 messages wait (...)", ready, waiting)."""
    n = lambda b: sum(1 for r in li["ready"] if r["bucket"] == b)   # noqa: E731
    plural = lambda k, w, ws=None: f"{k} {w if k == 1 else (ws or w + 's')}"   # noqa: E731
    parts = [p for p in (plural(n("profile edit"), "profile edit") + " to apply" if n("profile edit") else "",
                         plural(n("connection request"), "connection request") if n("connection request") else "",
                         plural(n("message"), "message") + " ready now" if n("message") else "") if p]
    text = ", ".join(parts) or "nothing ready now"
    w = li["waiting"]
    if w:
        acc = sum(1 for r in w if r["waits_on"] == "an accepted connection")
        why = ", ".join(x for x in ((f"{acc} on an accepted connection" if acc else ""),
                                    (f"{len(w) - acc} on a trigger" if len(w) - acc else "")) if x)
        text += f"; {plural(len(w), 'message')} wait ({why})"
    return text, len(li["ready"]), len(w)


def review_list(root: Path, pending: set[str]) -> str | None:
    """The approvals review list, only when it names at least one approval still pending (a list of decided
    approvals is not the owner's to read again)."""
    try:
        text = (Path(root) / REVIEW_LIST).read_text(encoding="utf-8")
    except OSError:
        return None
    full = set(re.findall(r"\bA-\d{4}-\d{4}\b", text))
    short = set(re.findall(r"\bA-(\d{4})\b(?!-)", text))           # the list writes "A-0032"
    named = {p for p in pending if p in full or p.rsplit("-", 1)[-1] in short}
    return REVIEW_LIST.as_posix() if named else None


def split_drafts(drafts: list[dict[str, Any]] | None, statuses: dict[str, str],
                 pending: set[str]) -> dict[str, Any] | None:
    """The owner decides approvals, not drafts. An open draft inside one of the owner's pending job-search
    approvals (`pending`, the ids approvals() returns) is already in front of them: counted, never listed again. The
    rest are the roles' own work: no approval named (messenger files it); a named approval file that is missing
    (messenger re-files); an approved approval whose draft is still open (the sender's; apply-decisions has not
    synced it); any other status, or a pending approval of another project (messenger re-files or closes)."""
    if drafts is None:
        return None
    out: dict[str, Any] = {"in_approvals": 0, "unfiled": [], "missing": [], "approved_unsynced": [], "not_pending": []}
    for d in drafts:
        appr = str(d.get("approval") or "")
        st = statuses.get(appr)
        if appr and appr in pending:
            out["in_approvals"] += 1
        elif not appr:
            out["unfiled"].append(d)
        elif st is None:
            out["missing"].append(d)
        elif st == "approved":
            out["approved_unsynced"].append(d)
        else:
            out["not_pending"].append({**d, "approval_status": "pending in another project" if st == "pending" else st})
    return out


_DECISION = re.compile(r"\bdecision:(?P<value>[^|\n]*)", re.I)
_UNDECIDED = {"", "-", "_", "__", "?", "tbd", "pending", "(blank)", "blank"}
OPEN_MAIL_DECISIONS = {"review", "queued"}


def mail_review(root: Path) -> dict[str, Any] | None:
    """Mail waiting for the owner or the Recruiter: review-file items with a blank `decision:` and mail-events rows
    whose latest line per message says review or queued. None when neither source exists."""
    folder, events = Path(root) / "state" / "review", Path(root) / "state" / "mail-events.jsonl"
    files = sorted(folder.glob("mail-*.md")) if folder.is_dir() else []
    if not files and not events.is_file():
        return None
    out: list[dict[str, Any]] = []
    for path in files:
        open_items = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                         for m in [_DECISION.search(line)] if m and m.group("value").strip().lower() in _UNDECIDED)
        if open_items:
            out.append({"file": f"state/review/{path.name}", "open": open_items})
    if events.is_file():
        latest: dict[str, str] = {}
        for row in read_jsonl(events):
            key = str(row.get("message_id") or row.get("thread_id") or "")
            if key:
                latest[key] = str(row.get("decision") or "").strip().lower()
        waiting = [k for k, d in latest.items() if d in OPEN_MAIL_DECISIONS]
        if waiting:
            out.append({"file": "state/mail-events.jsonl", "open": len(waiting), "message_ids": waiting})
    return {"open": sum(f["open"] for f in out), "files": out}


MAIL_ROUTINE = "js-mail-triage"


MORNING = "js-morning-brief"


def morning_pass(root: Path, now: datetime | None) -> dict[str, Any] | None:
    """: the morning brief runs the gmail-sync read-and-record pass once a day. Read from the routine spec
    (routines/js-morning-brief.yaml: enabled, a daily cron, and a gmail-sync step in the spec or in the installed prompt
    its header names, "prompt in <path>"). Returns {"time": "HH:MM", "next": "YYYY-MM-DD HH:MM"} or None."""
    path = Path(root) / "routines" / f"{MORNING}.yaml"
    try:
        text = path.read_text(encoding="utf-8")
        spec = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(spec, dict) or spec.get("enabled") is not True:
        return None
    prompt = re.search(r"prompt in\s*(?:#\s*)?(\S+?SKILL\.md)", text)     # the header may wrap the path
    try:
        installed = Path(prompt.group(1)).expanduser().read_text(encoding="utf-8") if prompt else ""
    except OSError:
        installed = ""
    spec_text = yaml.safe_dump({k: v for k, v in spec.items() if k != "connectors"}) if spec else ""
    if "gmail-sync" not in spec_text and "gmail-sync" not in installed:
        return None
    cron = str(spec.get("schedule") or "").split()
    if len(cron) != 5 or cron[2:] != ["*", "*", "*"] or not (cron[0].isdigit() and cron[1].isdigit()):
        return None
    hh, mm = int(cron[1]), int(cron[0])
    out = {"time": f"{hh:02d}:{mm:02d}", "next": None}
    if now is not None:
        pt = now.astimezone(_zone())
        day = pt.date() if (pt.hour, pt.minute) < (hh, mm) else pt.date() + timedelta(days=1)
        out["next"] = f"{day.isoformat()} {hh:02d}:{mm:02d}"
    return out


def mail_run(root: Path, os_root: Path, now: datetime | None = None) -> dict[str, Any]:
    """When mail-triage last ran (its newest receipt, logs/runs/js-mail-triage/<date>.md), whether its hourly
    routine is on, and the last Gmail pull
    (state/harvest/gmail.state.json runs[-1]). Missing pieces are None, never guessed."""
    folder = Path(os_root) / "logs" / "runs" / MAIL_ROUTINE
    receipts = sorted(folder.glob("20[0-9][0-9]-[01][0-9]-[0-3][0-9]*.md")) if folder.is_dir() else []   # dated only
    out: dict[str, Any] = {"receipt": None, "last_run": None, "routine_enabled": None, "gmail_pulled": None,
                           "gmail_new": None, "morning": morning_pass(root, now),
                           "as_of": iso_utc(now) if now else None}
    if receipts:
        out["receipt"] = f"logs/runs/{MAIL_ROUTINE}/{receipts[-1].name}"
        out["last_run"] = receipts[-1].stem[:10]
    try:
        spec = yaml.safe_load((Path(root) / "routines" / f"{MAIL_ROUTINE}.yaml").read_text(encoding="utf-8")) or {}
        flag = spec.get("enabled") if isinstance(spec, dict) else None
        out["routine_enabled"] = flag if isinstance(flag, bool) else None          # a missing key is not "off"
    except (OSError, yaml.YAMLError):
        pass
    try:
        runs = json.loads((Path(root) / "state" / "harvest" / "gmail.state.json").read_text(encoding="utf-8")).get("runs")
        last = runs[-1] if isinstance(runs, list) and runs else None
        if isinstance(last, dict) and last.get("at"):
            out["gmail_pulled"], out["gmail_new"] = str(last["at"]), last.get("new")
    except (OSError, ValueError, AttributeError):
        pass
    return out


def missed_runs(last_run: str, mp: dict[str, Any], now: datetime) -> tuple[int, str, str] | None:
    """Daily slots (the morning pass's HH:MM in your zone) since the last receipt's day that left no receipt: (count, first, last)."""
    pt = now.astimezone(_zone())
    hh, mm = (int(x) for x in mp["time"].split(":"))
    last_slot = pt.date() if (pt.hour, pt.minute) >= (hh, mm) else pt.date() - timedelta(days=1)
    first = date.fromisoformat(last_run) + timedelta(days=1)
    n = (last_slot - first).days + 1
    return (n, first.isoformat(), last_slot.isoformat()) if n > 0 else None


def mail_line(open_: int | None, run: dict[str, Any] | None, now: datetime | None = None) -> str:
    """The mail part of "Waiting on the owner": never a bare "no data"."""
    run = run or {}
    now = now or (parse_ts(run["as_of"]) if run.get("as_of") else None)
    mp = run.get("morning")
    daily = (f"the morning brief runs it daily at {mp['time']} {_tz_label()} (gmail-sync, read and record only)"
             if mp else "")
    if run.get("last_run"):
        text = (f"mail review {open_} open" if open_ is not None else "mail review: no review file yet") + \
            f": mail-triage last ran {run['last_run']} ({run['receipt']})"
        gap = missed_runs(run["last_run"], mp, now) if mp and now else None
        if gap:
            text += f", missed {gap[0]} daily run{'s' if gap[0] != 1 else ''} ({gap[1]} to {gap[2]})" if gap[0] > 1 \
                else f", missed the daily run of {gap[1]}"
        text += f"; {daily}" if daily else ""
    elif mp and not open_:
        text = (f"mail review: the mail pass has not run yet (no receipt in logs/runs/{MAIL_ROUTINE}/); {daily}"
                + (f", next {mp['next']} {_tz_label()}" if mp.get("next") else ""))
    elif open_:
        text = (f"mail review {open_} open (state/review, state/mail-events.jsonl); no mail-triage receipt yet"
                + (f"; {daily}" if daily else ""))
    else:
        why = (f"the hourly routine is off, so it runs when {_who()} starts the daily mail check"
               if run.get("routine_enabled") is False else
               "its hourly routine is on and has not left a receipt" if run.get("routine_enabled") else
               f"the routine's state is not on record (routines/{MAIL_ROUTINE}.yaml)")
        text = f"mail review: mail-triage has not run yet (no receipt in logs/runs/{MAIL_ROUTINE}/; {why})"
    if run.get("gmail_pulled"):
        ts = parse_ts(run["gmail_pulled"])
        pt = ts.astimezone(_zone()) if ts else None
        when = pt.strftime("%Y-%m-%d %H:%M ") + _tz_label() if pt else run["gmail_pulled"]
        text += f"; Gmail last pulled {when}"
        if run.get("gmail_new") is not None:
            text += f": {run['gmail_new']} new messages harvested"
            if pt and (not run.get("last_run") or run["last_run"] < pt.date().isoformat()):
                text += ", not yet triaged"            # no mail-triage receipt on or after the pull's day
    return text


def gate_reviews(jobs: dict[str, dict] | None) -> list[dict[str, Any]] | None:
    if jobs is None:
        return None
    return [{"id": j["id"], "company": j.get("company"), "title": j.get("title"),
             "reasons": [r for r in j.get("gate_reasons") or [] if not str(r).startswith("note:")]}
            for j in jobs.values() if j.get("gate") == "review" and j.get("status") in ("fit_checked", "discovered")]


def backlog(root: Path, today: date) -> dict[str, Any] | None:
    recs = _records(Path(root) / "prospects")
    if recs is None:
        return None
    by_status: Counter = Counter()
    by_priority: Counter = Counter()
    due = []
    for path, meta, _ in recs:
        status = str(meta.get("status") or "unknown")
        by_status[status] += 1
        by_priority[str(meta.get("priority") or "unset")] += 1
        nxt = _day(meta.get("next_touch_at"))
        if nxt and nxt <= today and status not in CLOSED_PROSPECT:
            due.append({"id": meta.get("id") or path.stem, "name": meta.get("name") or path.stem,
                        "company": meta.get("company"), "priority": meta.get("priority"),
                        "next_touch_at": nxt.isoformat(), "status": status})
    due.sort(key=lambda d: (str(d["priority"] or "P9"), d["next_touch_at"]))
    return {"records": len(recs), "by_status": dict(sorted(by_status.items())),
            "by_priority": dict(sorted(by_priority.items())), "follow_ups_due": due}


def applications(root: Path, now: datetime) -> list[dict[str, Any]] | None:
    if not tracker.app_dir(root).is_dir():
        return None
    out = []
    for slug, meta, body in tracker.iter_applications(root) or []:
        proj = tracker.project(tracker.parse_events(body), now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        out.append({"application": slug, "company": meta.get("company"), "title": meta.get("title"),
                    "stage": proj["stage"], "next_action": meta.get("next_action"),
                    "next_action_at": str(_day(meta.get("next_action_at")) or "") or None,
                    "last_activity": proj["last_activity"].date().isoformat() if proj.get("last_activity") else None})
    return out


def pipeline(apps: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if apps is None:
        return None
    return {"records": len(apps), "by_stage": dict(sorted(Counter(a["stage"] for a in apps).items())),
            "active": [a for a in apps if a["stage"] in ACTIVE_STAGES]}


def interviews(apps: list[dict[str, Any]] | None, today: date, days: int = 7) -> list[dict[str, Any]] | None:
    if apps is None:
        return None
    horizon = today + timedelta(days=days)
    return sorted((a for a in apps if a["stage"] in INTERVIEW_STAGES and a["next_action_at"]
                   and today <= date.fromisoformat(a["next_action_at"]) <= horizon),
                  key=lambda a: a["next_action_at"])


def offers(root: Path, apps: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Applications at stage offer, waiting for the owner's decision; the deadline comes from the negotiator's
    applications/<slug>/offer.md `deadline`, else the application's next_action_at."""
    if apps is None:
        return None
    out = []
    for a in apps:
        if a["stage"] != "offer":
            continue
        offer_md = tracker.app_dir(root) / a["application"] / "offer.md"
        deadline = _day(fm.load(offer_md)[0].get("deadline")) if offer_md.is_file() else None
        source = "offer.md" if deadline else None
        if not deadline and a["next_action_at"]:
            deadline, source = date.fromisoformat(a["next_action_at"]), "next_action_at"
        out.append({"application": a["application"], "company": a["company"], "title": a["title"],
                    "deadline": deadline.isoformat() if deadline else None, "deadline_source": source,
                    "path": f"applications/{a['application']}/" + ("offer.md" if offer_md.is_file() else "application.md")})
    return sorted(out, key=lambda o: (o["deadline"] or "9999", o["application"]))


def clock_status(root: Path, today: date) -> dict[str, Any] | None:
    """The job clock as of today (scripts/clock.py); None when clock.yaml is absent, {"error": ...} when it
    cannot be read."""
    try:
        cfg = job_clock.load(root)
    except job_clock.ClockError as exc:
        return {"error": str(exc)}
    return None if cfg is None else job_clock.status(cfg, today)


def fresh_fits(jobs: dict[str, dict] | None, now: datetime) -> list[dict[str, Any]] | None:
    if jobs is None:
        return None
    out = []
    for j in jobs.values():
        seen = parse_ts(j.get("first_seen"))
        if not seen or (now - seen).total_seconds() > FRESH_HOURS * 3600:
            continue
        if j.get("fit_verdict") not in ("apply", "priority") or j.get("status") in ("closed", "skipped"):
            continue
        out.append({"id": j["id"], "company": j.get("company"), "title": j.get("title"),
                    "fit_score": j.get("fit_score"), "fit_verdict": j.get("fit_verdict"),
                    "location": j.get("location"), "sponsorship": j.get("sponsorship"),
                    "hours_since_seen": int((now - seen).total_seconds() // 3600), "url": j.get("apply_url") or j.get("source_url")})
    return sorted(out, key=lambda f: (f["fit_verdict"] != "priority", -(f["fit_score"] or 0)))


PRESENCE_KPIS = Path("state") / "profiles" / "tools" / "presence_kpis.py"
KPI_TIMEOUT_S = 90


def presence_kpis(root: Path) -> dict[str, Any]:
    """PROFILES' HUNT B9 KPIs (read-only tool, --json): {coverage, flags, precision, response}, or {"error": why}."""
    import subprocess
    tool = Path(root) / PRESENCE_KPIS
    if not tool.is_file():
        return {"error": f"{PRESENCE_KPIS} absent"}
    try:
        out = subprocess.run([sys.executable, str(tool), "--json"], cwd=Path(root), capture_output=True, text=True,
                             timeout=KPI_TIMEOUT_S, check=False)
        if out.returncode != 0:
            return {"error": f"presence_kpis.py failed (exit {out.returncode})"}
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return {"error": f"presence_kpis.py failed ({type(exc).__name__})"}


def kpi_line(k: dict[str, Any] | None) -> str:
    if not k or "error" in k:
        return f"- KPIs: not measured ({(k or {}).get('error', 'no data')})"
    cov = k.get("coverage") or {}
    prec = ", ".join(f"{p} {round(100 * v['share'])}% ({v['on_title']}/{v['cards']})"
                     for p, v in sorted((k.get("precision") or {}).items()) if isinstance(v, dict) and v.get("cards"))
    resp = ", ".join(f"{s} {v['responded']}/{v['applied']}" for s, v in sorted((k.get("response") or {}).items()))
    return (f"- KPIs: portals complete {cov.get('complete', '--')} of {cov.get('wanted', '--')} · presence flags "
            f"{k.get('flags', '--')} · alert cards on target title: {prec or NO_DATA}, target 70% · responses: "
            f"{resp or NO_DATA}")


def hunt(root: Path) -> dict[str, Any] | None:
    """The latest HUNT fresh-roles review (scripts/hunt_review.py, state/hunt/review-latest.json); absent -> None."""
    path = Path(root) / "state" / "hunt" / "review-latest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    counts = data.get("counts") or {}
    return {"reviewed_at": data.get("now"), "cut": data.get("cut"), "new": counts.get("new"),
            "listed": counts.get("listed"), "freshness": data.get("freshness") or {},
            "coverage": data.get("coverage") or {},
            "top": [{k: r.get(k) for k in ("hunt_score", "company", "title", "action", "also", "url")}
                    for r in (data.get("rows") or [])[:5]]}


def visa_timers(root: Path, today: date) -> dict[str, Any] | None:
    path = Path(root) / "legal" / "timers.md"
    if not path.is_file():
        return None
    meta, _ = fm.load(path)
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if key in ("title", "type", "owner", "links", "updated", "created"):
            continue
        d = _day(value) if isinstance(value, (str, date)) and not isinstance(value, bool) else None
        if d and isinstance(value, (date, str)) and len(str(value)) >= 8:
            out[key] = {"date": d.isoformat(), "days_left": (d - today).days}
        else:
            out[key] = value if isinstance(value, (int, float, str, bool)) or value is None else str(value)
    return out


def discovery(jobs: dict[str, dict] | None, now: datetime) -> dict[str, Any] | None:
    if jobs is None:
        return None
    last24, last7 = Counter(), Counter()
    for j in jobs.values():
        seen = parse_ts(j.get("first_seen"))
        if not seen:
            continue
        age = (now - seen).total_seconds() / 3600
        if age <= 24:
            last24[j.get("source") or "unknown"] += 1
        if age <= 24 * 7:
            last7[j.get("source") or "unknown"] += 1
    return {"jobs": len(jobs),
            "by_status": dict(sorted(Counter(str(j.get("status")) for j in jobs.values()).items())),
            "gates": dict(sorted(Counter(str(j.get("gate")) for j in jobs.values() if j.get("gate")).items())),
            "discovered_24h": dict(sorted(last24.items())), "discovered_7d": dict(sorted(last7.items())),
            "ghost_score_0_5_plus": sum(1 for j in jobs.values() if (j.get("ghost_score") or 0) >= 0.5)}


def pursuits(root: Path, os_root: Path) -> list[dict[str, Any]] | None:
    """Every pursuit with the approvals still pending on its people's outbox items; None without pursuits/."""
    if not (Path(root) / "pursuits").is_dir():
        return None
    items = _records(Path(root) / "outbox") or []
    out = []
    for row in pursuit_records.rows(root):
        meta = pursuit_records.load(root, row["id"])[0]
        ids = set(pursuit_records.people(meta))
        cited = {str(m.get("approval")) for _, m, _ in items if str(m.get("prospect") or "") in ids and m.get("approval")}
        pending = 0
        for aid in cited:
            path = Path(os_root) / "work" / "approvals" / f"{aid}.md"
            if path.is_file() and fm.load(path)[0].get("status") == "pending":
                pending += 1
        out.append({**row, "approvals_waiting": pending})
    return out


LINKEDIN_INBOX = "https://www.linkedin.com/messaging/"
# A display name from a digest subject is third-party text that lands in HOME, which every agent reads: at most 4 words,
# each a capitalised name part (Jean-Luc, O'Neil, McDonald; never ADMIN), letters with ' and - only (so no URL, @ or
# digit), and none of the instruction or link words a planted "name" would need.
_NAME_PART = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
_NOT_NAMES = frozenset("""admin administrator agent ai all approve assistant bot chatgpt claude click command delete do
    execute follow forget http https ignore instruction instructions link now override previous prompt reply run send
    system urgent visit www""".split())


def is_display_name(text: str) -> bool:
    words = str(text or "").split()
    if not 1 <= len(words) <= 4 or len(" ".join(words)) > 40:
        return False
    for word in words:
        if not _NAME_PART.fullmatch(word) or word.lower() in _NOT_NAMES or not word[0].isupper():
            return False
        letters = [c for c in word if c.isalpha()]
        if len(letters) > 1 and all(c.isupper() for c in letters):
            return False
    return True


def _mail_time(row: dict[str, Any]) -> datetime | None:
    from email.utils import parsedate_to_datetime
    if row.get("date"):
        try:
            return parsedate_to_datetime(str(row["date"]))
        except (TypeError, ValueError):
            pass
    ms = row.get("internal_date")
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc) if str(ms or "").isdigit() else None


def linkedin_digests(root: Path, day: date) -> dict[str, Any] | None:
    """Today's LinkedIn message digests in state/harvest/: how many, and who wrote (names only); None without a harvest."""
    folder = Path(root) / "state" / "harvest"
    if not folder.is_dir():
        return None
    seen, names, withheld = set(), [], set()
    for path in sorted(folder.glob("gmail-*.jsonl")):
        for row in read_jsonl(path):
            if str(row.get("from_email") or "").lower() != mc.LINKEDIN_DIGEST_SENDER:
                continue
            subject = str(row.get("subject") or "")
            at = _mail_time(row)
            key = row.get("id") or row.get("message_id") or f"{subject}|{row.get('date')}"
            if not mc.LINKEDIN_DIGEST_SUBJECT.search(subject) or not at or dept_day(at) != day or key in seen:
                continue
            seen.add(key)
            m = re.match(r"^\s*(.+?)\s+just messaged you", subject, re.I)
            name = m.group(1).strip() if m else ""
            if name and is_display_name(name) and name not in names:
                names.append(name)
            elif name and not is_display_name(name):
                withheld.add(name)                                   # counted, never printed: a sender is not hidden
    return {"notices": len(seen), "names": names, "withheld": len(withheld)}


STATUS_CAP = 10
WAITING_ON_COMPANY = ("applied", "confirmation", "screen", "oa", "interviewing", "final")
NOT_QUEUED = frozenset(tracker.TERMINAL) | {"accepted", "on_hold"}
FUNNEL_REACHED = ("tailored", "applied", "screen", "interviewing", "final", "offer")


def _applying(root: Path) -> str:
    """'paused' or 'open', from hub/NEXT-UP-overrides.yaml."""
    try:
        import next_up
        return next_up.load_overrides(Path(root))["applying"]
    except Exception:  # noqa: BLE001 - unreadable reads as the default, paused
        return "paused"


def status(data: dict[str, Any], apps: list[dict[str, Any]] | None, root: Path, today: date) -> dict[str, Any]:
    """The one status (design §8): funnel with folder counts, today's queue (next actions due today or earlier,
    open applications only), waiting on the owner / companies / people, the clock line. None = no source."""
    nk, ck = data["needs_owner"], data.get("clock")
    funnel = queue = companies = None
    if apps is not None:
        m = data["metrics"]["applications"] or {}
        reached, ended = m.get("funnel") or {}, m.get("outcomes") or {}
        funnel = {**tracker.folder_counts(root), **{k: reached.get(k, 0) for k in FUNNEL_REACHED},
                  **{k: ended.get(k, 0) for k in tracker.TERMINAL}}
        queue = sorted(({"application": a["application"], "company": a["company"], "title": a["title"],
                         "stage": a["stage"], "next_action": a["next_action"], "due": a["next_action_at"]}
                        for a in apps if a["next_action_at"] and a["next_action_at"] <= today.isoformat()
                        and a["stage"] not in NOT_QUEUED), key=lambda q: (q["due"], str(q["company"])))
        companies = sorted(({"application": a["application"], "company": a["company"], "title": a["title"],
                             "stage": a["stage"], "days_since_activity":
                                 (today - date.fromisoformat(a["last_activity"])).days if a["last_activity"] else None}
                            for a in apps if a["stage"] in WAITING_ON_COMPANY),
                           key=lambda c: (-(c["days_since_activity"] or 0), str(c["company"])))
    count = lambda v: None if v is None else len(v)   # noqa: E731
    pr = data.get("pursuits")
    return {
        "clock": clock_lines(ck)[0],
        "funnel": funnel,
        "today": today.isoformat(),
        "queue": queue,
        "applying": _applying(root),
        # gate reviews are the fit-scorer's work, never the owner's
        "agency_backlog": {k: count((data.get("agency_backlog") or {}).get(k))
                           for k in ("gate_reviews", "drafts_unfiled", "drafts_missing", "drafts_approved_unsynced",
                                     "drafts_not_pending", "email_approved_unsent", "other_approved_unsent",
                                     "approved_expired", "approved_not_job_search", "drafts_to_redraft")},
        "waiting_on": {
            "owner": {"approvals": count(nk["approvals"]), "review_list": data.get("review_list"),
                       "linkedin": nk.get("linkedin"),
                       "offers": count(nk.get("offers")),
                       "mail_review": None if nk.get("mail_review") is None else nk["mail_review"]["open"],
                       "mail_run": data.get("mail_run")},
            "companies": companies,
            "people": None if pr is None else [
                {"title": p["title"], "waiting_on": p["waiting_on"], "next_action_at": p["next_action_at"]}
                for p in pr if p["stage"] not in pursuit_records.CLOSED],
        },
    }


def _capped(rows: list[str]) -> list[str]:
    return rows[:STATUS_CAP] + ([f"  - and {len(rows) - STATUS_CAP} more"] if len(rows) > STATUS_CAP else [])


def render_status(st: dict[str, Any]) -> list[str]:
    L = ["## Status", ""]
    f = st["funnel"]
    L.append(f"- Funnel: {NO_DATA}" if f is None else
             f"- Funnel: folders {f['folders']} (applications {f['applications']}, package-only {f['package_only']}, "
             f"untagged {f['untagged']}) · reached " + ", ".join(f"{k} {f[k]}" for k in FUNNEL_REACHED)
             + " · ended " + ", ".join(f"{k} {f[k]}" for k in tracker.TERMINAL))
    q = st["queue"]
    # "the queue" is hub/NEXT-UP.md (generated, one ranked list); this line is the records' next
    # actions that fall due; while applying is paused the steps before applying wait, and the line says so
    paused = st.get("applying") != "open"
    note = " (applying is paused: steps before applying wait)" if paused else ""
    if q is None:
        L.append(f"- Next actions due: {NO_DATA}")
    elif not q:
        L.append("- Next actions due: nothing due.")
    else:
        today = st.get("today") or ""
        late = [i for i in q if i["due"] < today]
        L.append(f"- Next actions due ({len(q)}: {len(late)} past due, oldest {late[0]['due']}; "
                 f"{len(q) - len(late)} due today){note}:" if late else f"- Next actions due ({len(q)}){note}:")
        L += _capped([f"  - due {i['due']} · {i['company']}: {i['title']} · {i['stage']} · {i['next_action'] or '--'}"
                      f" · `applications/{i['application']}/`" for i in q])
    k = st["waiting_on"]["owner"]
    show = lambda v: NO_DATA if v is None else v   # noqa: E731
    apr = f"{show(k['approvals'])}" + (f" ({k['review_list']})" if k.get("review_list") and k["approvals"] else "")
    lk = k.get("linkedin") or {}
    _, nr, nw = linkedin_summary(lk) if lk else ("", 0, 0)
    li = (f" · LinkedIn to apply or send now {nr}" + (f" ({nw} more waiting; approvals expire from "
          f"{lk['earliest_expiry']})" if nw and lk.get("earliest_expiry") else f" ({nw} more waiting)" if nw else
          f" (approvals expire from {lk['earliest_expiry']})" if lk.get("earliest_expiry") else "")) if nr or nw else ""
    L.append(f"- Waiting on {_who()}: approvals {apr}{li} · offers {show(k['offers'])} · "
             f"{mail_line(k['mail_review'], k.get('mail_run'))}")
    ab = st.get("agency_backlog") or {}
    gr, du = ab.get("gate_reviews"), ab.get("drafts_unfiled")
    dm, da_, dn = ab.get("drafts_missing") or 0, ab.get("drafts_approved_unsynced") or 0, ab.get("drafts_not_pending") or 0
    eu, rd = (ab.get("email_approved_unsent") or 0) + (ab.get("other_approved_unsent") or 0), ab.get("drafts_to_redraft") or 0
    ax = (ab.get("approved_expired") or 0) + (ab.get("approved_not_job_search") or 0)
    plural = lambda n, w: f"{n} {w}{'s' if n != 1 else ''}"   # noqa: E731
    parts = [f"fit-scorer gate reviews {NO_DATA} (no jobs/)" if gr is None else
             f"fit-scorer {plural(gr, 'gate review')} to settle"]
    if du is None:
        parts.append(f"messenger drafts {NO_DATA} (no outbox/)")
    elif du or dm or dn or rd or ax:
        parts.append("messenger " + ", ".join(
            ([f"{plural(du, 'draft')} not yet filed for approval"] if du else []) +
            ([f"{plural(dm, 'draft')} naming a missing approval file"] if dm else []) +
            ([f"{plural(dn, 'draft')} whose approval is no longer pending"] if dn else []) +
            ([f"{rd} to redraft"] if rd else []) +
            ([f"{ax} approved to re-request or re-file"] if ax else [])))
    if da_ or eu:
        parts.append("sender " + ", ".join(
            ([f"{eu} approved, not sent"] if eu else []) +
            ([f"{plural(da_, 'approved draft')} not yet synced (run apply-decisions)"] if da_ else [])))
    L.append(f"- Agency backlog (the roles' own work, nothing for {_who()} to do): " + "; ".join(parts))
    c = st["waiting_on"]["companies"]
    if c is None:
        L.append(f"- Waiting on companies: {NO_DATA}")
    elif not c:
        L.append("- Waiting on companies: none.")
    else:
        L.append(f"- Waiting on companies ({len(c)}):")
        L += _capped([f"  - {i['company']}: {i['title']} · {i['stage']} · "
                      + (f"{i['days_since_activity']} day{'' if i['days_since_activity'] == 1 else 's'} since last activity" if i["days_since_activity"] is not None
                         else "last activity unknown") for i in c])
    pp = st["waiting_on"]["people"]
    if pp is None:
        L.append(f"- Waiting on people (pursuits): {NO_DATA}")
    elif not pp:
        L.append("- Waiting on people (pursuits): none open.")
    else:
        L.append(f"- Waiting on people (pursuits) ({len(pp)}):")
        L += _capped([f"  - {p['title']} · waiting on {p['waiting_on'] or '--'} · next {p['next_action_at'] or '--'}"
                      for p in pp])
    return L + [""]


def focus_next(root: Path, now: datetime) -> dict[str, Any]:
    """: the "FOCUS next" top 10, from next_up.focus_for (the same function `next_up.py --focus` prints): ranked afresh
    from the records on every call (about 10 s on the live tree, so only the writing run asks for it). A failure is
    said in the block, never a blank."""
    try:
        import next_up
        return next_up.focus_for(Path(root), now)
    except Exception as exc:  # noqa: BLE001 - the block says why it is missing
        return {"error": f"{type(exc).__name__}: {exc}"}


def build(root: Path, os_root: Path, now: datetime, focus: bool = False) -> dict[str, Any]:
    jobs = load_jobs(root) if (Path(root) / "jobs").is_dir() else None
    apps = applications(root, now)
    pending_appr = approvals(os_root, now)
    pending_ids = {str(a["id"]) for a in pending_appr or []}
    appr_recs = approval_records(os_root)
    drafts = split_drafts(outbox(root), {k: str(m.get("status") or "") for k, m in appr_recs.items()},
                          pending_ids)                                       # approvals are the owner's, drafts not
    after = after_approval(root, appr_recs, dept_day(now))                   # approved, not yet out
    data = {
        "generated_at": iso_utc(now),
        "clock": clock_status(root, dept_day(now)),
        "needs_owner": {"approvals": pending_appr, "mail_review": mail_review(root),
                         "linkedin": None if after is None else {k: after[k] for k in ("ready", "waiting",
                                                                                     "earliest_expiry")},
                         "offers": offers(root, apps)},
        "agency_backlog": {"gate_reviews": gate_reviews(jobs),     # fit-scorer's work, never the owner's
                           **{f"drafts_{k}": None if drafts is None else drafts[k]
                              for k in ("unfiled", "missing", "approved_unsynced", "not_pending")},
                           "email_approved_unsent": None if after is None else after["email"],
                           "other_approved_unsent": None if after is None else after["other"],
                           "approved_expired": None if after is None else after["expired"],
                           "approved_not_job_search": None if after is None else after["foreign"],
                           "drafts_to_redraft": None if after is None else after["redraft"]},
        "drafts_in_approvals": None if drafts is None else drafts["in_approvals"],
        "mail_run": mail_run(root, os_root, now),
        "review_list": review_list(root, pending_ids),
        "pursuits": pursuits(root, os_root),
        "presence": presence_check.summary(root, dept_day(now)),
        "presence_kpis": presence_kpis(root),
        "linkedin_digests": linkedin_digests(root, dept_day(now)),
        "backlog": backlog(root, dept_day(now)),
        "pipeline": pipeline(apps),
        "fresh_fits": fresh_fits(jobs, now),
        "hunt": hunt(root),
        "scoring_queue": None if jobs is None else sum(
            1 for j in jobs.values() if j.get("status") == "fit_checked" and j.get("fit_verdict") in (None, "", "unscored")),
        "interviews_next_7_days": interviews(apps, dept_day(now)),
        "interviews_next_14_days": interviews(apps, dept_day(now), FEED_INTERVIEW_DAYS),
        "visa_timers": visa_timers(root, dept_day(now)),
        "metrics": {"applications": tracker.metrics(root, now) if apps else None, "discovery": discovery(jobs, now)},
    }
    data["status"] = status(data, apps, root, dept_day(now))
    if focus:
        data["focus_next"] = focus_next(root, now)
    return data


# ------------------------------------------------------------------------------ markdown
def _list(items: list[str] | None, empty_source: bool, none_msg: str = "None right now.") -> list[str]:
    if empty_source:
        return [f"- {NO_DATA}"]
    return [f"- {i}" for i in items] if items else [f"- {none_msg}"]


def _counts(d: dict[str, Any]) -> str:
    return ", ".join(f"{k} {v}" for k, v in d.items()) or NO_DATA


def render(data: dict[str, Any]) -> str:
    L = [HEADER, "# Job search dashboard", "", *clock_lines(data.get("clock")),
         f"Updated {data['generated_at']}. Source: `scripts/dashboard.py`.", ""]
    L += render_status(data["status"])
    fn = data.get("focus_next")
    if fn is not None:
        L += ["## FOCUS next (top 10 by expected interviews per hour; age decays, STALE is parked)", ""]
        if fn.get("error"):
            L.append(f"- Not computed: {fn['error']}")
        else:
            cut = lambda t, n: t if len(t) <= n else t[:n - 1].rstrip() + "…"   # noqa: E731
            L += [f"{x['rank']}. **{x['state']}** {cut(x['role'], 100)} · {x['per_hour']:.1%}/h · age {x['age']} · "
                  f"{cut(x['why'], 130)}" for x in fn["rows"]] or ["- Nothing ranked."]
            L.append(f"- {fn['stale']} STALE rows are parked in hub/NEXT-UP.md. Refresh: "
                     "`python3 scripts/next_up.py --write --focus`.")
        L.append("")
    nk = data["needs_owner"]
    L += [f"## Needs {_who()}", ""]
    of = nk.get("offers")
    if of:
        L.append(f"- Offers awaiting your decision: {len(of)}")
        L += [f"  - {o['company']}: {o['title']} · deadline {o['deadline'] or 'unknown'} · `{o['path']}`" for o in of]
    ap = nk["approvals"]
    mr = nk.get("mail_review")
    if ap is None:
        L.append(f"- Approvals: {NO_DATA}")
    elif not ap:
        L.append("- Approvals: none pending." if mr and mr["open"] else "- Approvals: nothing waiting.")
    else:
        held = data.get("drafts_in_approvals")
        note = [f"they hold {held} outbox draft{'s' if held != 1 else ''}"] if held else []
        note += [f"One-pass list for {_who()}: {data['review_list']}"] if data.get("review_list") else []
        L.append(f"- Approvals waiting: {len(ap)}" + (f" ({'; '.join(note)})" if note else ""))
        L += _capped([f"  - {a['id']} · {a['action']} · {a['target'] or ''} · created {a['created']}"
                      + (" · URGENT (over 24 h)" if a["urgent"] else "")
                      + (" · EXPIRED, re-request" if a["expired"] else "") for a in ap])
    lk = nk.get("linkedin")
    if lk and (lk["ready"] or lk["waiting"]):
        text, _, _ = linkedin_summary(lk)
        L.append(f"- LinkedIn for {_who()} to paste and send (the linkedin-assist hand-off): {text}"
                 + (f"; the approvals expire from {lk['earliest_expiry']}" if lk.get("earliest_expiry") else ""))
        L += _capped([f"  - {o['id']} · {o['channel']} · {o['prospect'] or 'his profile'}" for o in lk["ready"]])
        if lk["waiting"]:
            L.append(f"  - waiting ({len(lk['waiting'])}): " + ", ".join(
                f"{o['id']} ({o['prospect']}, on {o['waits_on'].split(':', 1)[0] if o['waits_on'].startswith('trigger') else o['waits_on']})"
                for o in lk["waiting"][:STATUS_CAP]) + (f" and {len(lk['waiting']) - STATUS_CAP} more"
                                                        if len(lk["waiting"]) > STATUS_CAP else ""))
    if mr is None:
        line = mail_line(None, data.get("mail_run"))
        L.append(f"- {line[0].upper()}{line[1:]}")
    elif not mr["open"]:
        L.append("- Mail review: nothing waiting.")
    else:
        L.append(f"- Mail review waiting: {mr['open']} (label or application match needs your call)")
        L += [f"  - {f['file']}: {f['open']} " + ("queued for review" if f["file"].endswith(".jsonl")
                                                  else "without a decision") for f in mr["files"]]
    L.append("")
    gr = (data.get("agency_backlog") or {}).get("gate_reviews")
    L += [f"## Agency backlog (the roles' own work, nothing for {_who()} to do)", ""]
    L.append(f"- Gate reviews for fit-scorer: {NO_DATA}" if gr is None else
             ("- Gate reviews for fit-scorer: none." if not gr else
              f"- Gate reviews for fit-scorer to settle (jobs with gate: review): {len(gr)}"))
    L += _capped([f"  - {g['company']}: {g['title']} ({'; '.join(g['reasons']) or 'see job file'}) `jobs/{g['id']}.md`"
                  for g in gr or []])
    ab = data.get("agency_backlog") or {}
    du = ab.get("drafts_unfiled")
    L.append(f"- Drafts for messenger to file for approval: {NO_DATA} (no outbox/)" if du is None else
             ("- Drafts for messenger to file for approval: none." if not du else
              f"- Drafts for messenger to file for approval: {len(du)}"))
    L += _capped([f"  - {o['id']} · {o['channel']} · {o['prospect']} · {o['status']} `outbox/{o['id']}.md`"
                  for o in du or []])
    row = lambda o: f"  - {o['id']} · {o['channel']} · {o['prospect']} · approval {o['approval']}"   # noqa: E731
    for key, label in (("drafts_missing", "Drafts naming a missing approval file (messenger: re-file)"),
                       ("drafts_approved_unsynced", "Approved drafts the outbox has not synced (sender; run "
                                                    "apply-decisions)")):
        if ab.get(key):
            L.append(f"- {label}: {len(ab[key])}")
            L += _capped([row(o) for o in ab[key]])
    for key, label in (("email_approved_unsent", "Approved email the sender has not sent"),
                       ("other_approved_unsent", "Approved items on another channel the sender has not sent"),
                       ("approved_expired", "Approved items whose approval expired (messenger: re-request)"),
                       ("approved_not_job_search", "Approved items whose approval is not a job-search approval "
                                                   "(messenger: re-file)"),
                       ("drafts_to_redraft", "Drafts rejected for redraft (messenger: redraft and file again)")):
        if ab.get(key):
            L.append(f"- {label}: {len(ab[key])}")
            L += _capped([row(o) for o in ab[key]])
    dn = ab.get("drafts_not_pending") or []
    for st_ in sorted({o["approval_status"] for o in dn}):
        rows = [o for o in dn if o["approval_status"] == st_]
        L.append(f"- Drafts whose approval is no longer pending ({st_}; messenger: re-file or close): {len(rows)}")
        L += _capped([row(o) for o in rows])
    L.append("")
    pr = data.get("pursuits")
    L += ["## Pursuits", ""]
    if pr is None:
        L.append(f"- {NO_DATA}")
    else:
        live = [p for p in pr if p["stage"] not in pursuit_records.CLOSED]
        L += [(f"- {p['stage']} · {p['title']} · waiting on {p['waiting_on']} · next: {p['next_action'] or '--'}"
               f" {p['next_action_at'] or ''}").rstrip() + f" · approvals waiting {p['approvals_waiting']} · "
              f"`pursuits/{p['id']}/{pursuit_hub.HUB}`" for p in live] or ["- None open right now."]
        closed = Counter(p["stage"] for p in pr if p["stage"] in pursuit_records.CLOSED)
        if closed:
            L.append(f"- Closed: {sum(closed.values())} (" + ", ".join(f"{k} {v}" for k, v in sorted(closed.items())) + ")")
    L.append("")
    ps = data.get("presence")
    L += ["## Presence", ""]
    L.append(f"- {NO_DATA}" if ps is None else
             f"- Surfaces {ps['surfaces']} · current {ps['current']} · stale {ps['stale']} · contradicts {ps['contradicts']} · "
             f"unwatched {ps['unwatched']} · flags {ps['flags']} (`python3 scripts/presence_check.py` lists them)")
    L.append(kpi_line(data.get("presence_kpis")))
    L.append("")
    bl = data["backlog"]
    L += ["## Backlog CRM", ""]
    if not bl or not bl["records"]:
        L.append(f"- {NO_DATA}")
    else:
        L.append(f"- Prospects: {bl['records']} · by status: {_counts(bl['by_status'])}")
        L.append(f"- By priority: {_counts(bl['by_priority'])}")
        L.append(f"- Follow-ups due today: {len(bl['follow_ups_due'])}")
        L += [f"  - {d['priority'] or '--'} · {d['name']} ({d['company'] or 'unknown'}) · due {d['next_touch_at']}"
              for d in bl["follow_ups_due"]] or ["  - None right now."]
    L.append("")
    pl = data["pipeline"]
    L += ["## Pipeline", ""]
    if not pl or not pl["records"]:
        L.append(f"- {NO_DATA}")
    else:
        L.append(f"- Applications: {pl['records']} · " + ", ".join(f"{k}: {v}" for k, v in pl["by_stage"].items()))
        L += [(f"  - {a['stage']} · {a['company']}: {a['title']} · next: {a['next_action'] or '--'}"
               f" {a['next_action_at'] or ''}").rstrip() + f" · `applications/{a['application']}/{role_hub.HUB}`"
              for a in pl["active"]]
    L.append("")
    ff = data["fresh_fits"]
    L += ["## Fresh fits", ""]
    if ff is None:
        L.append(f"- {NO_DATA}")
    else:
        L += [f"- {f['fit_verdict']} {f['fit_score'] if f['fit_score'] is not None else '--'} · {f['company']}: "
              f"{f['title']} · {f['location'] or 'location?'} · sponsorship {f['sponsorship'] or 'unknown'} · "
              f"seen {f['hours_since_seen']} h ago · {f['url']}" for f in ff] or ["- None right now."]
        if data["scoring_queue"]:
            L.append(f"- Waiting for fit-scorer: {data['scoring_queue']} gated postings (fit_verdict unscored)")
    L.append("")
    hu = data.get("hunt")
    L += ["## Fresh roles (hunt)", ""]
    if hu is None:
        L.append(f"- {NO_DATA} (scripts/hunt_review.py writes it)")
    else:
        f, c = hu["freshness"], hu["coverage"]
        L.append(f"- Review {hu['reviewed_at']}: {hu['new']} new, {hu['listed']} at the cut {hu['cut']}; freshness "
                 f"median {f.get('median_hours')} h (target {f.get('target_hours')} h); {c.get('scored')} of "
                 f"{c.get('open')} open jobs scored · state/review/fresh-roles-<date>.md")
        L += [f"- {r['hunt_score']} · {r['company']}: {r['title']} · {r['action']}"
              + (f" (also {', '.join(r['also'])})" if r.get("also") else "") for r in hu["top"]]
    L.append("")
    iv = data["interviews_next_7_days"]
    L += ["## Interviews next 7 days", ""]
    L += _list([f"{i['next_action_at']} · {i['stage']} · {i['company']}: {i['title']} · {i['next_action'] or ''}".rstrip(" ·")
                for i in iv or []], iv is None)
    L.append("")
    vt = data["visa_timers"]
    L += ["## Visa timers", ""]
    if not vt:
        L.append(f"- {NO_DATA}")
    else:
        for k, v in vt.items():
            L.append(f"- {k}: {v['date']} ({v['days_left']} days)" if isinstance(v, dict) else f"- {k}: {v}")
    L.append("")
    L += ["## Metrics", ""]
    am, dm = data["metrics"]["applications"], data["metrics"]["discovery"]
    if dm is None:
        L.append(f"- Discovery: {NO_DATA}")
    else:
        L.append(f"- Jobs tracked: {dm['jobs']} · by status: {_counts(dm['by_status'])}")
        L.append(f"- Discovered in 24 h: {_counts(dm['discovered_24h'])} · 7 days: {_counts(dm['discovered_7d'])}")
        L.append(f"- Gates: {_counts(dm['gates'])} · ghost score >= 0.5: {dm['ghost_score_0_5_plus']}")
    if not am:
        L.append(f"- Applications: {NO_DATA}")
    else:
        L.append("- Funnel: " + ", ".join(f"{k} {v}" for k, v in am["funnel"].items() if k != "discovered" or v))
        L.append("- Outcomes: " + _counts(am["outcomes"]))
        rs = am["response_rate_by_source"]
        L.append("- Response rate by source: " + (", ".join(f"{k} {v['responded']}/{v['applied']}" for k, v in rs.items()) or NO_DATA))
        lat = am["apply_latency_hours"]
        L.append(f"- Apply latency: median {lat['median'] if lat['median'] is not None else '--'} h; "
                 f"{lat['within_72h']}/{lat['measured']} within 72 h (target: all)")
    L.append("")
    return "\n".join(L)


def clock_lines(ck: dict[str, Any] | None) -> list[str]:
    if ck is None:
        return ["Job clock: no data yet (clock.yaml is missing)", ""]
    if "error" in ck:
        return [f"Job clock: unreadable ({ck['error']})", ""]
    return [ck["line"], "", job_clock.detail_line(ck), ""]


# ------------------------------------------------------------------------------ HOME feed
def _os_link(root: Path, os_root: Path, rel: str) -> str:
    """A repo-relative link (<vault prefix>/...) when the department sits inside the workspace."""
    try:
        return (Path(root).resolve() / rel).relative_to(Path(os_root).resolve()).as_posix()
    except ValueError:
        return Path(rel).as_posix()


def feed_day(data: dict[str, Any]) -> date:
    """The department's day of a dashboard build, from its UTC `generated_at`."""
    return dept_day(parse_ts(data["generated_at"]))


def home_feed(data: dict[str, Any], root: Path, os_root: Path) -> dict[str, Any]:
    """The HOME feed for scripts/kernel/sweep.py, built from dashboard data (contract in the module doc)."""
    today = feed_day(data)
    link = lambda rel: _os_link(root, os_root, rel)  # noqa: E731
    needs: list[dict[str, Any]] = []
    deadlines: list[dict[str, Any]] = []
    today_rows: list[dict[str, Any]] = []
    ck = data.get("clock")
    if ck is None:
        needs.append({"title": "Job clock missing", "why": "clock.yaml not found: the 90-day deadline is not tracked",
                      "link": link("clock.yaml")})
    elif "error" in ck:
        needs.append({"title": "Job clock unreadable", "why": ck["error"], "link": link("clock.yaml")})
    else:
        today_rows.append({"title": ck["line"], "link": link("DASHBOARD.md")})
        at = f" at {ck['employer']}" if ck.get("employer") else ""
        tag = "" if ck["employment_end_confirmed"] else " (provisional)"
        if not ck["employment_end_confirmed"]:
            needs.append({"title": f"Confirm the last working day{at}",
                          "why": f"clock.yaml has {ck['employment_end']} (provisional); the 90-day clock and the visa "
                                 f"duties start the day after it",
                          "link": link("clock.yaml")})
        if ck["state"] != "new-job":
            deadlines.append({"title": f"90-day job clock ends: new job needed{tag}", "date": ck["clock_end"],
                              "hard": True, "link": link("clock.yaml")})
        if ck["employment_end"] >= today.isoformat():
            deadlines.append({"title": f"Last working day{at}{tag}", "date": ck["employment_end"], "hard": True,
                              "link": link("clock.yaml")})
    nk = data["needs_owner"]
    for o in nk.get("offers") or []:
        why = f"offer awaiting your decision by {o['deadline']}" if o["deadline"] else \
            "offer awaiting your decision (deadline unknown: ask the negotiator)"
        needs.append({"title": f"Offer from {o['company']}: {o['title']}", "why": why, "link": link(o["path"])})
        if o["deadline"]:
            deadlines.append({"title": f"Offer deadline: {o['company']}, {o['title']}", "date": o["deadline"],
                              "hard": True, "link": link(o["path"])})
    lk = nk.get("linkedin")
    if lk and lk["ready"]:
        needs.append({"title": f"LinkedIn: {len(lk['ready'])} approved items to apply or send"
                               + (f" (approvals expire from {lk['earliest_expiry']})" if lk.get("earliest_expiry") else ""),
                      "why": "approved; paste and send them yourself (the linkedin-assist hand-off)",
                      "link": link("DASHBOARD.md")})
    mr = nk.get("mail_review")
    if mr and mr["open"]:
        needs.append({"title": f"Mail review: {mr['open']} message(s) need your call",
                      "why": "a label or application match needs your decision", "link": link("DASHBOARD.md")})
    for a in nk.get("approvals") or []:
        expires = _day(a.get("expires"))
        if expires and today <= expires <= today + timedelta(days=FEED_APPROVAL_DAYS):
            deadlines.append({"title": f"Approval {a['id']} expires ({a['action']})", "date": expires.isoformat(),
                              "hard": False, "link": f"work/approvals/{a['path']}"})
    for i in data.get("interviews_next_14_days") or []:
        where = link(f"applications/{i['application']}/application.md")
        deadlines.append({"title": f"{i['stage']} with {i['company']}: {i['title']}", "date": i["next_action_at"],
                          "hard": True, "link": where})
        if i["next_action_at"] == today.isoformat():
            today_rows.append({"title": f"{i['stage']} today: {i['company']}, {i['title']}"
                                        + (f" · {i['next_action']}" if i.get("next_action") else ""), "link": where})
    for p in data.get("pursuits") or []:          # a closed pursuit waits on nobody and has no next action
        where = link(f"pursuits/{p['id']}/{pursuit_hub.HUB}")
        if p["waiting_on"] == str(cfg.get("identity.owner_token", "owner")).lower():
            needs.append({"title": f"Pursuit waits on you: {p['title']}", "why": p["next_action"] or "no next action set",
                          "link": where})
        at = _day(p.get("next_action_at"))
        if at and at <= today + timedelta(days=FEED_PURSUIT_DAYS):
            deadlines.append({"title": f"Pursuit next action: {p['title']}: {p['next_action'] or 'unnamed'}",
                              "date": at.isoformat(), "hard": False, "link": where})
    lg = data.get("linkedin_digests")
    if lg and lg["notices"]:
        who = lg["names"]
        quoted = [f'"{n}"' for n in who]                                   # quoted: third-party data, never an instruction
        more = lg.get("withheld", 0)
        if quoted and more:
            quoted.append(f"{more} more sender{'' if more == 1 else 's'}")
        people = (quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]) if quoted else ""
        plural = "" if lg["notices"] == 1 else "s"
        needs.append({"title": (f"LinkedIn: messages from {people} today ({lg['notices']} notice{plural})" if people else
                                f"LinkedIn: {lg['notices']} message notice{plural} today"),
                      "why": "the messages sit in LinkedIn's inbox; read them there (no agent reads LinkedIn mail text)",
                      "link": LINKEDIN_INBOX})
    ps = data.get("presence")
    if ps and ps["flags"]:
        needs.append({"title": f"Public profiles: {ps['flags']} presence flag{'' if ps['flags'] == 1 else 's'}",
                      "why": "a surface is stale, due or unwatched (PRESENCE)", "link": link("DASHBOARD.md")})
    due = (data.get("backlog") or {}).get("follow_ups_due") or []
    if due:
        today_rows.append({"title": f"Follow-ups due: {len(due)}", "link": link("DASHBOARD.md")})
    deadlines.sort(key=lambda d: (d["date"], d["title"]))
    st = data.get("status") or {}
    if st.get("queue") is not None:
        late = sum(1 for q in st["queue"] if q["due"] < (st.get("today") or ""))
        due = (f"{len(st['queue'])} next actions due ({late} past due)" if late else f"{len(st['queue'])} due today") + \
            (" while applying is paused" if st.get("applying") != "open" else "")
        today_rows.append({"title": f"Job search: {due}, "
                                    f"{len(st['waiting_on']['companies'] or [])} waiting on companies",
                           "link": link("DASHBOARD.md")})
    return {"project": PROJECT, "generated_at": data["generated_at"], "needs_you": needs, "deadlines": deadlines,
            "today": today_rows}


def write(root: Path, data: dict[str, Any], os_root: Path | None = None,
          graph: dict[str, Any] | None = None) -> dict[Path, str | None]:
    """Write DASHBOARD.md, state/dashboard.json, state/home-feed.json, refresh the role and pursuit hubs (returned:
    the hub files that changed) and the tracking graph graph/ (scripts/build_graph.py; its report is put in
    `graph` when a dict is given)."""
    root = Path(root)
    (root / "DASHBOARD.md").write_text(render(data), encoding="utf-8")
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "dashboard.json").write_text(json.dumps(data, indent=2, default=str, sort_keys=False) + "\n",
                                          encoding="utf-8")
    feed = home_feed(data, root, os_root if os_root is not None else os_root_for(root))
    (state / "home-feed.json").write_text(json.dumps(feed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    changes = role_hub.refresh(root)
    changes.update(pursuit_hub.refresh(root, os_root if os_root is not None else os_root_for(root)))
    report = build_graph.refresh(root, dept_day(parse_ts(data["generated_at"])),
                                 os_root if os_root is not None else os_root_for(root))
    if graph is not None:
        graph.update(report)
    return changes


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--os-root", type=Path, default=None, help="runtime root (default: derived from --root)")
    ap.add_argument("--dry-run", action="store_true", help="print DASHBOARD.md; write nothing")
    ap.add_argument("--status", action="store_true",
                    help="print only the clock line and the status section (the Recruiter's brief); write nothing")
    ap.add_argument("--today", type=cli_date, default=None,
                    help="the day the dashboard is for, YYYY-MM-DD (default: the system date)")
    args = ap.parse_args(argv)
    now = tracker.run_now(args.today, now)
    if args.os_root is None:
        args.os_root = os_root_for(Path(args.root))
    data = build(args.root, args.os_root, now, focus=not args.status)
    if args.status:
        print("\n".join([data["status"]["clock"], "", *render_status(data["status"])]).rstrip())
    elif args.dry_run:
        print(render(data))
    else:
        graph: dict[str, Any] = {}
        hubs = write(args.root, data, args.os_root, graph)
        ph = [p for p in hubs if "pursuits" in Path(p).relative_to(args.root).parts[:1]]
        print(f"wrote {args.root / 'DASHBOARD.md'}, {args.root / 'state' / 'dashboard.json'} and "
              f"{args.root / 'state' / 'home-feed.json'}; role hubs: {len(hubs) - len(ph)} changed "
              f"(scripts/role_hub.py, index {role_hub.INDEX}); pursuit hubs: {len(ph)} changed (scripts/pursuit_hub.py)")
        print(build_graph.summary(graph))
    return 0


if __name__ == "__main__":
    sys.exit(main())
