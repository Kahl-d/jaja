#!/usr/bin/env python3
"""follow_ups.py - the one daily follow-up list the recruiter lane runs.

THE CADENCE: a person gets a human nudge 4 business days after our message and a
second 9 business days after it (PERSON_GAPS: 4 after the message, 5 after the first nudge), then nothing; an
application with no reply 5 business days after its last company activity gets one follow-up of another kind: find a
person at the company and reach them (APP_PERSON_DAYS; that message then runs on the person cadence). This list applies
the cadence; tracker.py and prospects.py keep their own next-action fields, which this list does not read for the due
day (a prospect's next_touch_at later than the old automatic +5 is a deferral and is kept).

Sources, one list by due date:
  application  scripts/tracker.py: open applications at applied or confirmation with no reply APP_PERSON_DAYS business
               days after the last company activity and no follow-up logged since: "find a person at the company and
               reach them". One logged follow-up (tracker.py event <slug> follow_up) moves it to "No follow-ups left".
  prospect     people who replied and are waiting on our nudge (prospects.py records: status replied, our message
               last): due on the person cadence, at most two nudges (fewer when the record's max_follow_ups says so),
               never after a referral window closes; plus the exhausted ones and referral windows closing within the
               warning days (prospects.py due).

  portal-thread: touches/*.jsonl threads that carry a `portal` (LinkedIn, Wellfound, Indeed ...): our last sent
               message got no reply, on the person cadence. Two follow-ups since their last message: "No follow-ups
               left". A thread whose prospect already has a prospect nudge only gives that row its portal (listed once).
  portal-message: inbound mail of the last 30 days (state/harvest/*.classified.jsonl) whose label needs an answer
               (recruiter, founder, hiring-manager outreach, referral intro, interview invite or scheduling, assessment,
               offer) with no later message of ours in the thread (an outbound mail row or an out touch): an answer is
               owed. Direct bulk mail (List-Unsubscribe or List-Id, no portal) and calendar notices (Canceled event,
               Appointment booked, Accepted, Declined) owe none; a portal relay keeps its item (LinkedIn InMail carries
               List-Unsubscribe). Tagged by its portal (the record's `portal`, else resolved from targets/platforms.yaml now); a
               job-board, ATS or scheduling sender the registry does not know yet shows "unknown sender: confirm on first
               alert"; a direct email shows "email". Scam mail is never listed.
The list is grouped by portal ("By portal: ..."); rows without one are "no portal".

It drafts nothing and sends nothing: the messenger drafts each nudge into outbox/ with an approval file
(/follow-up-sweep), and the owner approves. Logging a follow-up that went out: `tracker.py event <slug> follow_up`
(applications) or `outbox.py mark-sent` (prospects).

CLI:  python3 scripts/follow_ups.py [--root R] [--today D] [--json] [--write]
      --write also saves state/follow-ups/<date>.md (atomic). Exit 0 always (a list, not a gate).
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402
import mail_classify as mc  # noqa: E402
import prospects as pr  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import JS_ROOT, cli_date, dept_day, fm  # noqa: E402

ANSWER_LABELS = frozenset(mc.OUTREACH_LABELS | {"interview_invite", "interview_scheduling", "ats_assessment", "offer"})
MAIL_WINDOW_DAYS = 30
UNKNOWN_SENDER = "unknown sender: confirm on first alert"
NOTICE_SUBJECT_RE = re.compile(r"^(canceled event|cancelled event|appointment booked|accepted|declined|tentatively accepted)\b", re.I)
VENDOR_DOMAINS = (mc.ATS_DOMAINS | mc.ASSESSMENT_DOMAINS | mc.SCHEDULING_DOMAINS | mc.ESIGN_DOMAINS
                  | mc.JOB_BOARD_DOMAINS | mc.BOARD_MESSAGE_DOMAINS | mc.BOARD_NOTICE_DOMAINS | {"linkedin.com"})
PERSON_GAPS = pr.FOLLOW_UP_GAPS  # business days after our latest message: 4 after it, then 5 after the first nudge (9)
MAX_PERSON_NUDGES = len(PERSON_GAPS)
APP_PERSON_DAYS = tr.FOLLOW_UP_OFFSETS[0]   # 5: the tracker's first follow-up day is the find-a-person day
APP_PERSON_NEXT = ("find a person at the company (the hiring manager, a recruiter or an engineer on the team) and "
                   "reach them; the messenger drafts the note (outbox + approval)")
NONE = "no portal"
NOTE = ("This list drafts nothing and sends nothing: the messenger drafts each nudge into outbox/ with an approval "
        "file (/follow-up-sweep); the owner approves.")


def applications(root: Path, today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now = tr.run_now(today)
    due, done = [], []
    for slug, meta, body in tr.iter_applications(root) or []:
        proj = tr.project(tr.parse_events(body), now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        action, when = tr.next_action_for(proj, now)
        base = {"kind": "application", "who": slug, "company": meta.get("company") or "",
                "title": meta.get("title") or "", "stage": proj["stage"],
                "contacts": list(meta.get("prospects") or [])}
        sent = proj.get("follow_ups_since_activity", 0)
        if action == tr.FOLLOW_UPS_DONE or (action == tr.FOLLOW_UP and sent >= 1):
            done.append({**base, "sent": sent})         # one find-a-person follow-up, then the person cadence
        elif action == tr.FOLLOW_UP and when and when <= today.isoformat():
            contacts = ("; contacts on record: " + ", ".join(base["contacts"])) if base["contacts"] else ""
            due.append({**base, "due": when, "nudge": None, "days_overdue": (today - date.fromisoformat(when)).days,
                        "channel": "", "next": APP_PERSON_NEXT + contacts})
    return due, done


def person_due(last_out: date, sent: int) -> date:
    """The day the next nudge to a person is due: PERSON_GAPS business days after our latest message."""
    return tr.add_business_days(last_out, PERSON_GAPS[min(sent, len(PERSON_GAPS) - 1)])


def prospects(root: Path, today: date) -> dict[str, list[dict[str, Any]]]:
    if not (root / "prospects").is_dir():
        return {"due": [], "exhausted": [], "windows": []}
    roots = cu.Roots(js=root, os=root.parent)
    res = pr.due(roots, today)                           # the referral windows (and the records' own rules)
    conv = lambda m, **extra: {"kind": "prospect", "who": m.get("id"), "company": m.get("company") or "",   # noqa: E731
                               "title": m.get("name") or "", "stage": "replied", "contacts": [], **extra}
    due, exhausted = [], []
    for _, m in pr.iter_metas(roots):                    # prospects.due's own exemptions, on the person cadence
        if m.get("status") != "replied" or not m.get("last_outbound_at"):
            continue
        if m.get("last_inbound_at") and pr._s(m["last_inbound_at"]) > pr._s(m["last_outbound_at"]):
            continue                                     # they wrote last: an answer, not a nudge
        out_day = cu.local_date(m["last_outbound_at"])
        sent = int(m.get("follow_ups_sent") or 0)
        cap = min(int(m.get("max_follow_ups") or MAX_PERSON_NUDGES), MAX_PERSON_NUDGES)
        when = person_due(out_day, sent)
        stored = jc.parse_date(m.get("next_touch_at"))
        if stored and stored > tr.add_business_days(out_day, max(pr.LEGACY_AUTO_DAYS, PERSON_GAPS[min(sent, MAX_PERSON_NUDGES - 1)])):
            when = stored                                # set later than any automatic day: a deferral
        window = jc.parse_date(m.get("referral_window_ends"))
        if window and window >= out_day:
            when = min(when, window)                     # never due after the referral window closes
        if when > today:
            continue
        if sent >= cap:
            exhausted.append(conv(m, sent=sent))
        else:
            due.append(conv(m, due=when.isoformat(), nudge=sent + 1, days_overdue=(today - when).days,
                            channel=(m.get("reply_context") or {}).get("channel") or
                            ("email" if m.get("emails") else "linkedin-message"),
                            next="/follow-up-sweep drafts a human nudge (outbox + approval)"))
    due.sort(key=lambda r: (r["due"], str(r["who"])))
    exhausted.sort(key=lambda r: str(r["who"]))
    return {"due": due, "exhausted": exhausted, "windows": res["windows"]}


def touches(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "touches").glob("*.jsonl")) if (root / "touches").is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def portal_threads(root: Path, today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """(due thread rows, exhausted thread rows, prospect -> portal of its latest portal touch)."""
    threads: dict[tuple[str, str], list[dict[str, Any]]] = {}
    latest: dict[str, tuple[str, str]] = {}
    for t in touches(root):
        portal, thread, at = t.get("portal"), t.get("thread_id"), str(t.get("at") or "")
        if not portal or not at:
            continue
        who = str(t.get("prospect") or "")
        if who and (who not in latest or at > latest[who][0]):
            latest[who] = (at, str(portal))
        if thread:
            threads.setdefault((str(portal), str(thread)), []).append(t)
    due, done = [], []
    for (portal, thread), rows in sorted(threads.items()):
        rows.sort(key=lambda r: str(r.get("at")))
        last_in = max((str(r["at"]) for r in rows if r.get("direction") == "in"), default="")
        ours = [r for r in rows if r.get("direction") == "out" and r.get("status", "sent") == "sent" and str(r["at"]) > last_in]
        if not ours:
            continue                                    # they wrote last, or we never wrote: no follow-up owed
        last = ours[-1]
        sent = sum(1 for r in ours if r.get("kind") == "follow_up")
        base = {"kind": "portal-thread", "who": str(last.get("prospect") or ""), "company": "", "title": "",
                "stage": "", "contacts": [], "portal": portal, "thread": thread}
        if sent >= MAX_PERSON_NUDGES:
            done.append({**base, "sent": sent})
            continue
        when = person_due(date.fromisoformat(str(last["at"])[:10]), sent)
        if when <= today:
            due.append({**base, "due": when.isoformat(), "nudge": sent + 1, "days_overdue": (today - when).days,
                        "channel": str(last.get("channel") or ""),
                        "next": "messenger drafts the nudge in the same portal thread (outbox + approval)"})
    return due, done, {who: portal for who, (_, portal) in latest.items()}


# Mail the classifier labels as outreach or scheduling but that no email reply can answer (calendar invitations,
# booking confirmations, digests, no-reply senders). Left in, these classes bury the recruiters who really wrote. Each
# is counted under its reason (`no_answer_owed`), never dropped silently. An ATS asking the owner to act ("Schedule
# your interview") stays.
CALENDAR_SUBJECT_RE = re.compile(r"^\s*((updated )?invitation( from an unknown sender)?|reminder|new event|updated event)"
                                 r"\s*:", re.I)
BOOKING_SUBJECT_RE = re.compile(r"\bbetween\b.+\band\b", re.I)
DIGEST_SUBJECT_RE = re.compile(r"\b(new (roles|jobs|openings) (at|for)\b|jobs? (you might|for you)\b|recommended (jobs|roles)\b|"
                               r"opportunities need to be filled\b|job alert\b)", re.I)
NO_REPLY_RE = re.compile(r"(^|[-_.])(no-?reply|do-?not-?reply)([-_.@]|$)", re.I)
PERSON_RELAYS = frozenset({"inmail-hit-reply@linkedin.com", "messages-noreply@linkedin.com", "hit-reply@linkedin.com"})
LINKEDIN_NOTICE_SENDERS = frozenset({"invitations@linkedin.com", "messaging-digest-noreply@linkedin.com",
                                     "notifications-noreply@linkedin.com"})


def no_answer_reason(row: dict[str, Any], frm: str, label: str | None, portal: str | None = None) -> str | None:
    """Why this inbound message owes no email reply, or None when a reply (or an action) is owed. Each rule needs the
    sign of a machine, never the subject alone (a recruiter's "Reminder: still interested?" stays)."""
    subject = str(row.get("subject") or "")
    frm = str(frm or "").lower()
    host = frm.split("@")[-1]
    domain = cu.registrable_domain(cu.email_domain(frm))
    calendar = bool(row.get("has_calendar")) or frm in mc.CALENDAR_SENDERS or domain in mc.SCHEDULING_DOMAINS
    if calendar and CALENDAR_SUBJECT_RE.match(subject):
        return "calendar invitation or reminder"                     # answered on the calendar, never by mail
    if domain in mc.SCHEDULING_DOMAINS and BOOKING_SUBJECT_RE.search(subject):
        return "booking confirmation"                                # a booked meeting, already on the calendar
    if frm in LINKEDIN_NOTICE_SENDERS:
        return "LinkedIn connection notice"                          # accepted in LinkedIn, not answered by mail
    alerts_host = host.startswith("alerts.") and host != domain       # alerts.jobot.com; never alerts.ai or alerts.co.uk
    person_relay = (frm in PERSON_RELAYS or domain in mc.BOARD_MESSAGE_DOMAINS
                    or host.startswith("conversations."))             # a person's message through a portal
    bulk = not person_relay and (bool(row.get("list_unsubscribe") or row.get("list_id")) or bool(portal)
                                 or domain in VENDOR_DOMAINS)
    if alerts_host or (bulk and DIGEST_SUBJECT_RE.search(subject)):
        return "digest or blast"
    if label in mc.OUTREACH_LABELS and not portal and NO_REPLY_RE.search(frm.split("@")[0]):
        return "no-reply sender"                                     # nobody reads a reply to it; a portal relay
    return None                                                      # (LinkedIn's messages-noreply) is a person


def portal_messages(root: Path, today: date, left: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Inbound mail that needs an answer and has none yet (see the module docstring). `left` (when given) counts the
    messages that owe no reply, by reason."""
    folder = root / "state" / "harvest"
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.classified.jsonl")) if folder.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row                   # the newest file wins for a message seen twice
    if not rows:
        return []
    accounts = {a.lower() for a in cu.ACCOUNT_EMAILS}
    ours: dict[str, str] = {}                                # thread -> our latest message time
    for row in rows.values():
        frm = (row.get("from_email") or cu.parse_address(row.get("from"))[1] or "").lower()
        if row.get("thread_id") and (row.get("direction") == "out" or frm in accounts):
            at = str(row.get("internal_date") or "")
            ours[str(row["thread_id"])] = max(ours.get(str(row["thread_id"]), ""), at)
    for t in touches(root):
        if t.get("direction") == "out" and t.get("thread_id"):
            ours[str(t["thread_id"])] = max(ours.get(str(t["thread_id"]), ""), str(t.get("at") or ""))
    cache: dict[str, dict[str, str]] = {}

    def resolve_portal(row: dict[str, Any]) -> str | None:
        if row.get("portal"):
            return str(row["portal"])
        if "index" not in cache:
            cache["index"] = mc.portal_index(root)
        return mc.portal_for(row.get("from_email") or row.get("from"), cache["index"])
    since = (today - timedelta(days=MAIL_WINDOW_DAYS)).isoformat()
    latest: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    for row in rows.values():
        label = (row.get("classification") or {}).get("label")
        at = str(row.get("internal_date") or "")
        thread = str(row.get("thread_id") or row["id"])
        day = (cu.local_date(at) or date.fromisoformat(at[:10])).isoformat() if at else ""   # in the owner's time zone
        if row.get("direction") == "out" or label not in ANSWER_LABELS or not at or day < since or day > today.isoformat():
            continue
        if NOTICE_SUBJECT_RE.match(str(row.get("subject") or "")):
            continue                                         # calendar notices owe no answer
        if (row.get("list_unsubscribe") or row.get("list_id")) and not resolve_portal(row):
            continue                                         # direct bulk mail owes none; portal relays (InMail) still do
        if ours.get(thread, "") > at:
            continue                                         # we wrote after it: answered
        why = no_answer_reason(row, row.get("from_email") or cu.parse_address(row.get("from"))[1] or "", label,
                               resolve_portal(row))
        if why:                                              # judged per message, before the thread's newest is picked
            skipped.setdefault(thread, why)                  # (an auto-reply never hides a person's message)
            continue
        if thread not in latest or at > str(latest[thread].get("internal_date")):
            latest[thread] = row
    if left is not None:
        for thread, why in skipped.items():
            if thread not in latest:
                left[why] = left.get(why, 0) + 1
    out = []
    for thread, row in latest.items():
        frm = row.get("from_email") or cu.parse_address(row.get("from"))[1] or ""
        portal = resolve_portal(row)
        if not portal:
            reg = cu.registrable_domain(cu.email_domain(str(frm).lower()))
            portal = UNKNOWN_SENDER if reg in VENDOR_DOMAINS else "email"
        received = (cu.local_date(row["internal_date"]) or date.fromisoformat(str(row["internal_date"])[:10])).isoformat()
        # the owner's local day, not UTC: a late-afternoon message is that day's
        label = (row.get("classification") or {}).get("label")
        name = cu.parse_address(row.get("from"))[0] or ""   # one relay address carries many people (InMail)
        out.append({"kind": "portal-message", "who": str(frm), "name": name, "portal": portal, "thread": thread,
                    "company": ((row.get("classification") or {}).get("identity") or {}).get("company") or "",
                    "title": cu.one_line(row.get("subject"), 80), "stage": label, "contacts": [],
                    "due": received, "nudge": None, "days_overdue": (today - date.fromisoformat(received)).days,
                    "channel": "", "next": f"answer owed ({label}): the messenger drafts the reply (outbox + approval)"})
    return out


def held(root: Path) -> tuple[set[str], set[str], set[str]]:
    """(ids, emails, display names) of prospects on do-not-contact, so no list ever asks to answer them
    (portal messages are found by mail thread, not by prospect, so a held sender would otherwise still show as an
    answer owed). A relay address (InMail) carries many people, so a relay row is matched on its display name."""
    ids: set[str] = set()
    emails: set[str] = set()
    names: set[str] = set()
    if not (root / "prospects").is_dir():
        return ids, emails, names
    for _p, meta in pr.iter_metas(cu.Roots(js=root, os=root.parent)):
        if not str(meta.get("do_not_contact_reason") or "").strip():
            continue
        ids.add(str(meta.get("id") or _p.stem))
        emails.update(str(e).strip().lower() for e in meta.get("emails") or [] if str(e).strip())
        if str(meta.get("name") or "").strip():
            names.add(str(meta["name"]).strip().lower())
    return ids, emails, names


def _is_held(r: dict[str, Any], ids: set[str], emails: set[str], names: set[str]) -> bool:
    if r.get("kind") == "portal-message":
        who = str(r.get("who") or "").lower()
        name = str(r.get("name") or "").strip().lower()
        return who in emails or (bool(name) and name in names)
    return str(r.get("who") or "") in ids


def collect(root: Path, today: date) -> dict[str, Any]:
    app_due, app_done = applications(root, today)
    pros = prospects(root, today)
    t_due, t_done, portal_of = portal_threads(root, today)
    for r in app_due + app_done:
        r.setdefault("portal", None)
    for r in pros["due"] + pros["exhausted"]:
        r["portal"] = portal_of.get(r["who"])
    listed = {r["who"] for r in pros["due"] + pros["exhausted"]}
    t_due = [r for r in t_due if r["who"] not in listed]
    t_done = [r for r in t_done if r["who"] not in listed]
    left: dict[str, int] = {}
    ids, emails, names = held(root)
    due = []
    for r in app_due + pros["due"] + t_due + portal_messages(root, today, left):
        if _is_held(r, ids, emails, names):
            left["on hold (do not contact)"] = left.get("on hold (do not contact)", 0) + 1
            continue
        due.append(r)
    due.sort(key=lambda r: (r["due"], r["kind"], r["who"]))
    return {"date": today.isoformat(), "due": due, "exhausted": app_done + pros["exhausted"] + t_done,
            "windows": pros["windows"], "no_answer_owed": left}


def _group(r: dict[str, Any]) -> str:
    return r.get("portal") or NONE


def render(res: dict[str, Any]) -> str:
    L = [f"Follow-ups due {res['date']}: {len(res['due'])}"]
    groups = sorted({_group(r) for r in res["due"]}, key=lambda g: (g in (NONE, UNKNOWN_SENDER, "email"), g))
    if groups:
        L.append("By portal: " + ", ".join(f"{g} {sum(_group(r) == g for r in res['due'])}" for g in groups))
    left = res.get("no_answer_owed") or {}
    if left:
        L.append(f"Owe no reply (left out): {sum(left.values())} ("
                 + ", ".join(f"{k} {v}" for k, v in sorted(left.items(), key=lambda kv: (-kv[1], kv[0]))) + ")")
    L.append("")
    for g in groups:
        L.append(f"[{g}]")
        for r in (r for r in res["due"] if _group(r) == g):
            L.append(f"- due {r['due']} ({r['days_overdue']} days overdue) · {r['kind']} · {r['who']}"
                     + (f" · {r['company']}" if r["company"] else "") + (f": {r['title']}" if r["title"] else "")
                     + (f" · thread {r['thread']}" if r.get("thread") else "")
                     + (f" · nudge {r['nudge']} of {MAX_PERSON_NUDGES}" if r.get("nudge") else "")
                     + (f" · {r['channel']}" if r["channel"] else "")
                     + (f" · contacts {', '.join(r['contacts'])}" if r["contacts"] else "") + f" · {r['next']}")
    if res["exhausted"]:
        L += ["", "No follow-ups left (two nudges to a person, one find-a-person note for an application; consider "
                  "closing or dormant):"]
        L += [f"- {r['kind']} · {r['who']}" + (f" · {r['company']}" if r["company"] else "")
              + (f" · [{r['portal']}] thread {r['thread']}" if r.get("thread") else "") for r in res["exhausted"]]
    if res["windows"]:
        L += ["", "Referral windows (apply through the invite before it ends; negative = lapsed):"]
        L += [f"- {w['id']} · {w.get('company') or ''} · ends {w['referral_window_ends']} ({w['days_left']} days)"
              for w in res["windows"]]
    return "\n".join(L + ["", NOTE]) + "\n"


def write(root: Path, res: dict[str, Any]) -> Path:
    path = root / "state" / "follow-ups" / f"{res['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"type": "follow-ups", "date": res["date"], "built_by": "scripts/follow_ups.py",
            "counts": {"due": len(res["due"]), "exhausted": len(res["exhausted"]), "windows": len(res["windows"])}}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(fm.dump(meta, "# " + render(res)))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--today", type=cli_date, default=None, help="YYYY-MM-DD (default: the department's today)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", action="store_true", help="also save state/follow-ups/<date>.md")
    args = p.parse_args(argv)
    today = args.today or dept_day(tr.wall_clock())
    res = collect(args.root, today)
    print(json.dumps(res, indent=2) if args.json else render(res), end="" if not args.json else "\n")
    if args.write:
        print(f"wrote {write(args.root, res).relative_to(args.root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
