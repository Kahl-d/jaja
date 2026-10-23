#!/usr/bin/env python3
"""Application tracker: append-only event log, stage projection, ghost flags, metrics.

Each application lives in applications/<slug>/ (jd.md, fit.md, application.md, outcome.md). The
body of application.md holds the append-only log under "## Events", one line per event:

    - <ISO> · <type> · <source> · conf=<x> · <evidence>

Stage projection (stage order = schemas/application.yaml):
  - events are replayed by their stated time (`at`), never by log order (ties keep log order); stage events
    move the stage forward (never backward);
  - rejected / ghosted / withdrawn are terminal. Only a stage event stated strictly AFTER the terminal event
    whose stage is the company re-engaging (REOPENS: screen, oa, interviewing, final, offer, accepted)
    re-opens one. Our own pre-response steps (discovered, fit_checked, tailored, applied, confirmation) never
    do: `new` stamps `discovered` at record creation, so a backlog record's discovered line can be the
    newest in time and must not revive a rejection (they still count toward max_progress);
  - on_hold pauses until a progression event stated strictly after it, other than `discovered`;
  - a pre-application step (discovered, fit_checked, tailored) replayed once the application is applied
    or terminal is not activity for ghosting (it is our bookkeeping, not the company's);
  - "override:<stage>" events (the owner's word) win: they set the stage outright, forward or
    backward, superseding everything before them; later events continue from there.
  - Ghosting is inferred, never logged: no activity for 90 days at applied/confirmation, 60 at
    screen/oa/interviewing, 45 at final (application `ghost_after_days` overrides). Activity =
    stage events, overrides and inbound events (email_in, reply, call, scheduled ...); our own
    follow-ups and notes do not reset the clock. `last_inbound_at` counts as activity.

Next action (STAGE_NEXT): `next_action` and `next_action_at` follow the projected stage. discovered: score fit;
fit_checked: tailor the resume; tailored: apply (the apply pack; the owner submits), all due the day the stage
began; applied and confirmation: with no reply 5 business days after the last activity, find a person at the company
and reach them, once (FOLLOW_UP_OFFSETS: never a nudge to a careers inbox; follow_up events are
ours, not activity; after it: "no further follow-up", the person's own schedule runs in follow_ups.py; a reply restarts
that schedule); screen, oa, interviewing, final: prep the round, and offer: negotiate, with no date (the round's
date and the offer deadline come from the invite and offer.md, never guessed); rejected, ghosted, withdrawn,
accepted and on_hold: none. The script rewrites both fields when the stage moves, and on any re-projection
while `next_action` is one of its own texts (a date added to a dateless one, such as the round's date, is
kept); a next action a role wrote in its own words is kept until the stage moves. A `requeued` event (evidence
required: why the item is still worth the work, e.g. the posting checked live) moves a discovered, fit_checked or
tailored item's due date to its own day; it is bookkeeping, never a stage or activity.

Subcommands:
  new --job <job id>        create the application folder from jobs/<id>.md (links both ways). The first event
                            (discovered or fit_checked) is stamped at the run clock, or with --discovered-at
                            <ISO date or timestamp> at the stated discovery time (a backlog record entered after
                            the fact; "discovered-at stated" in the evidence). --discovered-at must parse and not
                            be after the run clock (exit 2, nothing written); it is ignored when the record exists.
  event <slug> <type>       append one event, then re-project (--source --conf --evidence --at). `ghosted`
                            (and override:ghosted) is refused: ghosting is inferred by `project`; log it only
                            with --override "<the owner's words and date>", recorded in the evidence.
                            Every `applied` event (any source, a hand submission included) names the page that
                            went, so a rejection can be traced to the page it saw: --resume <PDF path under
                            resumes/, or its sha256 or a prefix of 8+ hex> writes resume_variant (the variant or
                            base folder), resume_pdf and resume_sha256 into application.md and the PDF into the
                            evidence; --resume none --reason "<why>" records resume_none_reason; --resume same
                            keeps the recorded page on a repeat applied event (a time correction). A hash that is
                            the same PDF in two folders is refused (pass the path). Without --resume, or with a
                            path or hash that names no PDF, exit 2 and nothing is written.
                            A manual `applied` event (type applied or submitted, source manual) takes --at as
                            the stated submission time (the confirmation's or the owner's date, time and
                            timezone) and records "time: stated" in the evidence. Without --at it warns on
                            stderr, stamps the run clock (now, or --today at 12:00 UTC) and records
                            "time: clock (no --at; submission time not stated)", so a clock stamp is never
                            mistaken for the submission time. --at must parse as an ISO timestamp (exit 2).
                            On a package-only folder (jd.md or fit.md, no application.md: work prepared before
                            any application record, or a manual apply) `event` first adopts the folder:
                            it writes application.md and outcome.md there from jd.md (and the job record, when
                            jobs/ has it), keeps the package, then logs the event. A slug with no folder is
                            refused (exit 2): create it with `new --job <id>`.
  tier <slug> dream|focused|mass --source "<why>" | tier <slug> --infer   [--dry-run]
                            record the application's tier and why; agency_status and strategy_review
                            read it before a batch sheet and before the inferred rule
  path <slug> --path person|cold|unknown [--via <prospect id>] --source "<evidence>"   [--dry-run]
                            record whether a person carried the application or it went cold: writes
                            path, via_prospect and path_source. person needs --via naming an existing
                            prospects/<id>.md; cold and unknown refuse --via and write via_prospect: null; --source
                            cites the evidence (a file path, an event, a decision). The same values again write
                            nothing; different values overwrite and the output names each field that changed
  merge FROM INTO [--dry-run]
                            one job, two folders (`new` can mint a second folder beside
                            an existing package): moves FROM's record into INTO. INTO with no application.md gets FROM's
                            application.md and outcome.md (id rewritten); INTO with a record gets only FROM's events
                            it lacks. The job's `application` link moves to INTO, INTO leaves state/package-only.yaml,
                            the stage is re-projected. FROM is never deleted: the plan names its files for removal by hand. --dry-run prints the plan and writes nothing. Refused (exit 2): no FROM record,
                            no INTO folder, or the two name different jobs.
  tag-package-only          list every application folder with no application.md in state/package-only.yaml
                            (the folders are read only for us; nothing is written inside one), so folder counts
                            match (folders = applications + package-only + untagged); a slug whose folder now has
                            application.md is dropped from the list (application.md always wins); a legacy KIND
                            marker inside a folder is still read and folded into the list, never deleted.
                            --dry-run previews.
  project [--force]         re-project every application (writes stage/outcome/applied_at); this is the ghost
                            sweep: it prints the ghost thresholds from GHOST_DAYS, each ghost flag with the
                            days since the last activity and its threshold, and the applications whose
                            next follow-up (+5, +10 or +15 business days) is due. --dry-run previews and writes nothing.

  Event lines that look like events (a bullet or a date under "## Events") but do not parse are
  never counted; every command reports each as `warning: applications/<slug>/application.md:<line>`
  (metrics --json also lists them under `warnings`). While such lines exist, `project` and `event`
  refuse to move an application's stage backwards (exit 3, the stage on file is kept) unless
  --force: fix the lines to the format above first.
  Every write (new, event, project) also sets the job's `application:` to "[[<slug>]]" when it names none,
  through save_job (the kernel frontmatter saver); a link to another record is never overwritten.
  metrics [--json]          funnel, response rate by source and applied_via, days in stage,
                            apply latency vs posted_at; by_path: applied, responded and reached screen
                            per recorded path, "missing" while an applied application has none
  reconcile [--dry-run] [--round <message_id>=screen|interviewing|final]
                            attach mail-triage's rows (state/mail-events.jsonl, the latest row per message_id)
                            as events, only on a confirmed match; everything else is held in
                            state/review/mail-<date>.md (blank `decision:`) and never applied. Per row:
                              - skipped: the message id is already in an application's "## Events" (a
                                correction: its mark `<message_id> correction <logged_at>` is already there);
                              - held: decision review; no or unknown application; suspected_scam; decision
                                auto unless the match is by thread (the row's thread_id is in the
                                application's thread_ids) or by req id / job URL / mail_intake.py's
                                company_title or company_sole with the row's confidence and match score at the
                                auto threshold (0.75) and a 0.20 lead over the runner-up; a stage earlier than the application's current (projected) stage,
                                which includes reviving a rejected, ghosted, withdrawn or on-hold one; an
                                interview_invite whose round (screen, interviewing, final) the evidence does
                                not name (pass --round);
                              - decision owner: attached as ruled (application must exist).
                            Label effects (roles/tracker-ops.md): ats_confirmation -> confirmation_received,
                            ats_assessment -> assessment_received, interview_invite -> screen | interviewing
                            | final, rejection -> rejection, interview_scheduling and offer -> an email_in
                            note (no stage change; an offer stage waits for the owner), outreach labels -> an
                            email_in note only in the application's own thread; job_alert, newsletter,
                            other -> no event. Evidence: "msg <id>; classifier: '<evidence>'", source email,
                            conf = the row's confidence, at = the message time. A correction of a message
                            already attached to another application or label appends a `note` naming the
                            superseded event; with decision owner the stage goes back by
                            `override:<stage>` quoting the owner's decision, else it is listed as needs the owner.
                            Exit 1 when the ledger is missing (absent is not empty).

  --today YYYY-MM-DD        (any subcommand; default: the system date) the day the run is for: ghost
                            flags, metrics, `updated` dates and the default event --at are computed as
                            of that day (12:00 UTC), or as of now when it is today.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from company_index import CompanyIndex  # noqa: E402
from fetch_ats import iso_utc, jd_text_from_body, load_jobs, parse_ts, save_job  # noqa: E402
from js_common import JS_ROOT, append_section_line, cli_date, dept_day, fm, read_jsonl, slugify, wrap_untrusted, write_record  # noqa: E402

STAGES = ["discovered", "fit_checked", "tailored", "applied", "confirmation", "screen", "oa", "interviewing",
          "final", "offer", "accepted", "rejected", "ghosted", "withdrawn", "on_hold"]
PROGRESS = STAGES[:11]
TERMINAL = ("rejected", "ghosted", "withdrawn")
# stages whose later event re-opens a terminal stage: the company re-engaging, never our own steps
REOPENS = frozenset(PROGRESS[PROGRESS.index("screen"):])
PRE_APPLICATION = frozenset(PROGRESS[:PROGRESS.index("applied")])
EVENT_STAGE = {
    "discovered": "discovered", "fit_checked": "fit_checked", "scored": "fit_checked",
    "tailored": "tailored", "draft": "tailored", "resume_ready": "tailored",
    "applied": "applied", "submitted": "applied",
    "confirmation": "confirmation", "confirmation_received": "confirmation", "acknowledged": "confirmation",
    "screen": "screen", "recruiter_screen": "screen", "phone_screen": "screen",
    "oa": "oa", "assessment": "oa", "assessment_received": "oa", "take_home": "oa",
    "interview": "interviewing", "interviewing": "interviewing", "onsite": "interviewing",
    "technical_interview": "interviewing",
    "final": "final", "final_round": "final",
    "offer": "offer", "accepted": "accepted",
    "rejected": "rejected", "rejection": "rejected", "ghosted": "ghosted", "withdrawn": "withdrawn",
    "on_hold": "on_hold", "paused": "on_hold",
}
INBOUND = {"email_in", "reply", "inbound", "call", "scheduled", "recruiter_reply", "message_in", "linkedin_in"}
GHOST_DAYS = {"applied": 90, "confirmation": 90, "screen": 60, "oa": 60, "interviewing": 60, "final": 45}
FOLLOW_UP_OFFSETS = (5,)    # business days after the last activity: find a person, once
FOLLOW_UP_TYPES = {"follow_up", "followup", "follow_up_sent", "nudge"}
# A live posting's overdue item is re-queued; bookkeeping only (never a stage or activity):
# the pre-application due date counts from the last requeue when it is later than the stage's own start
REQUEUE_TYPES = {"requeued", "requeue"}
MAX_REQUEUES = 2                   # per stage: a requeue buys one day, so a third means the item is stuck
PREP = "prep the round (prep-coach)"
FOLLOW_UP = "find a person at the company and reach them if no reply (prospector, then messenger)"
FOLLOW_UPS_DONE = "no further follow-up (a person was reached; their own schedule runs in follow_ups.py)"
STAGE_NEXT: dict[str, tuple[str, str | None]] = {       # stage: (next action, when it is due; see the docstring)
    "discovered": ("score fit (fit-scorer)", "entered"),
    "fit_checked": ("tailor the resume (resume-tailor)", "entered"),
    "tailored": ("apply: apply pack, the owner submits (apply-assist)", "entered"),
    "applied": (FOLLOW_UP, "follow_up"),
    "confirmation": (FOLLOW_UP, "follow_up"),
    "screen": (PREP, None), "oa": (PREP, None), "interviewing": (PREP, None), "final": (PREP, None),
    "offer": ("negotiate (negotiator)", None),
}
# Older wording the tracker used to write (three nudges): still the tracker's own, so a projection rewrites it
LEGACY_ACTIONS = {"follow up if no reply (messenger)", "no further follow-up (three sent, never a fourth)"}
TRACKER_ACTIONS = {text for text, _ in STAGE_NEXT.values()} | {FOLLOW_UPS_DONE} | LEGACY_ACTIONS
APPLIED_VIA = ["ats", "referral", "recruiter", "email", "other"]
RESPONDED = {"screen", "oa", "interviewing", "final", "offer", "accepted", "rejected"}
OUTCOME = {"offer": "offer", "accepted": "accepted", "rejected": "rejected", "ghosted": "ghosted",
           "withdrawn": "withdrawn"}
SEP = " · "
_EVENT_RE = re.compile(r"^- (?P<at>\S+) · (?P<type>.+?) · (?P<source>.+?) · conf=(?P<conf>[0-9.]+) ·(?: (?P<evidence>.*))?$")


class TrackerError(RuntimeError):
    """A command was refused; nothing was written."""


# An interview event's booking time is not its round's time, and a moved round would look like a new one.
# Two evidence tokens carry both, written by `event --round-at` and `--reschedule`
# and read by round_info (interviews.py counts, the dashboard's coming rounds).
ROUND_EVENT_STAGES = frozenset({"screen", "oa", "interviewing", "final"})
_ROUND_AT_RE = re.compile(r"(?:^|\s)round_at=(\S+)")
RESCHEDULE_TOKEN = "[reschedule]"            # only --reschedule writes it; the plain word in prose never counts


def round_info(evidence: Any) -> dict[str, Any]:
    """{'round_at': ISO or None, 'reschedule': bool} from an event's evidence text."""
    text = str(evidence or "")
    m = _ROUND_AT_RE.search(text)
    return {"round_at": m.group(1) if m else None, "reschedule": RESCHEDULE_TOKEN in text}


def round_evidence(type_: str, evidence: str, round_at: str | None, reschedule: bool) -> str:
    """The evidence with the round tokens appended; refused for an event that is not a round, or a time that does not
    parse (nothing is written)."""
    if not round_at and not reschedule:
        return evidence
    if EVENT_STAGE.get(str(type_).strip().lower().removeprefix("override:")) not in ROUND_EVENT_STAGES:
        raise TrackerError(f"refused: --round-at and --reschedule are for round events (screen, oa, interview, final), "
                           f"not {type_!r}; nothing was written")
    tokens = []
    if round_at:
        when = parse_ts(round_at)
        if when is not None and not re.search(r"(Z|[+-]\d{2}:?\d{2})$", str(round_at).strip()):
            from js_common import DEPT_TZ       # a time with no zone is in the department's timezone
            from datetime import datetime as _dt
            when = _dt.fromisoformat(str(round_at).strip()).replace(tzinfo=DEPT_TZ)
        if when is None or not re.search(r"T\d{2}:\d{2}", str(round_at)):
            raise TrackerError(f"refused: --round-at {round_at!r} is not a date and time (e.g. 2026-10-09T11:45:00-07:00); "
                               "nothing was written")
        tokens.append(f"round_at={iso_utc(when)}")
    if reschedule:
        tokens.append(RESCHEDULE_TOKEN)
    return " ".join([t for t in [str(evidence or "").strip()] if t] + tokens)


GHOST_TYPES = ("ghosted", "override:ghosted")
GHOST_REFUSAL = ("refused: ghosting is inferred by `tracker.py project` (no activity for 90/60/45 days by stage), "
                 "never logged. Run `tracker.py project --dry-run` and report the flag; log `ghosted` only with "
                 "--override \"<the owner's words and date>\".")


@dataclass
class Event:
    at: str
    type: str
    source: str
    conf: float
    evidence: str


def _clean(text: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def format_event(at: str, type_: str, source: str, conf: float, evidence: str) -> str:
    return (f"- {at}{SEP}{_clean(type_, 60).replace(SEP, ' ')}{SEP}{_clean(source, 40).replace(SEP, ' ')}"
            f"{SEP}conf={float(conf):.2f}{SEP}{_clean(evidence) or '-'}")


_EVENT_LIKE = re.compile(r"^(?:[-*+]\s|\d{4}-\d{2}-\d{2})")


def unparsed_event_lines(text: str) -> list[tuple[int, str]]:
    """(line number in `text`, line) for every event-looking line under "## Events" that does not parse."""
    out: list[tuple[int, str]] = []
    in_events = False
    for n, raw in enumerate((text or "").splitlines(), 1):
        s = raw.strip()
        if s.startswith("## "):
            in_events = s == "## Events"
            continue
        if in_events and s and _EVENT_LIKE.match(s) and not _EVENT_RE.match(s):
            out.append((n, s))
    return out


def event_warnings(root: Path, slug: str) -> list[dict[str, str]]:
    path = app_dir(root) / slug / "application.md"
    if not path.is_file():
        return []
    return [{"where": f"applications/{slug}/application.md:{n}", "line": line}
            for n, line in unparsed_event_lines(path.read_text(encoding="utf-8"))]


def print_warnings(warnings: list[dict[str, str]]) -> None:
    for w in warnings:
        print(f"warning: {w['where']}: unparseable event line (not counted): {w['line']}", file=sys.stderr)


def stage_rank(stage: Any) -> int:
    """Progress order; rejected, ghosted, withdrawn and on_hold rank above every progress stage."""
    if stage in PROGRESS:
        return PROGRESS.index(stage)
    return len(PROGRESS) if stage in STAGES else -1


def parse_events(body: str) -> list[Event]:
    out = []
    for line in (body or "").splitlines():
        m = _EVENT_RE.match(line.strip())
        if m:
            out.append(Event(m.group("at"), m.group("type").strip(), m.group("source").strip(),
                             float(m.group("conf")), (m.group("evidence") or "").strip()))
    return out


# ------------------------------------------------------------------------------ projection
def project(events: list[Event], now: datetime, ghost_after_days: int | None = None,
            last_inbound_at: Any = None) -> dict[str, Any]:
    ordered = sorted(enumerate(events), key=lambda ie: (_instant(ie[1].at) or now, ie[0]))
    idx, terminal, hold = -1, None, False
    paused_at: datetime | None = None                  # when the current terminal or on_hold began
    entered: dict[str, datetime] = {}
    transitions: list[tuple[str, datetime]] = []
    last_activity: datetime | None = None
    for _, e in ordered:
        at = _instant(e.at) or now
        typ = e.type.strip().lower()
        override = typ.startswith("override:")
        target = typ.split(":", 1)[1].strip() if override else EVENT_STAGE.get(typ)
        bookkeeping = (not override and target in PRE_APPLICATION
                       and (terminal is not None or idx >= PROGRESS.index("applied")))
        if (override or target is not None or typ in INBOUND) and not bookkeeping:
            last_activity = at
        if target is None or target not in STAGES:
            continue
        if target in TERMINAL:
            terminal, hold, paused_at = target, False, at
        elif target == "on_hold":
            hold, terminal, paused_at = True, None, at
        else:
            new_idx = PROGRESS.index(target)
            idx = new_idx if override else max(idx, new_idx)
            later = paused_at is None or at > paused_at
            if override or (terminal and target in REOPENS and later) \
                    or (hold and target != "discovered" and later):
                terminal, hold, paused_at = None, False, None
        current = terminal or ("on_hold" if hold else PROGRESS[max(idx, 0)])
        if not transitions or transitions[-1][0] != current:
            transitions.append((current, at))
        entered.setdefault(target, at)
    inbound = _instant(last_inbound_at)
    if inbound and (last_activity is None or inbound > last_activity):
        last_activity = inbound
    stage = terminal or ("on_hold" if hold else PROGRESS[idx] if idx >= 0 else "discovered")
    out = {"stage": stage, "ghosted": False, "ghost_from": None, "max_progress": PROGRESS[idx] if idx >= 0 else "discovered",
           "entered": entered, "transitions": transitions, "last_activity": last_activity,
           "applied_at": entered.get("applied"),
           "requeued_at": max((_instant(e.at) or now for e in events if e.type.strip().lower() in REQUEUE_TYPES),
                              default=None),
           "follow_ups_since_activity": sum(1 for e in events if e.type.strip().lower() in FOLLOW_UP_TYPES
                                            and (last_activity is None or (parse_ts(e.at) or now) > last_activity))}
    limit = ghost_after_days or GHOST_DAYS.get(stage)
    if stage in GHOST_DAYS and last_activity and (now - last_activity).total_seconds() / 86400 >= limit:
        out.update(stage="ghosted", ghosted=True, ghost_from=stage, ghost_limit=limit)
    return out


def ghost_thresholds_line() -> str:
    """The ghost thresholds as the code has them (GHOST_DAYS), so no role restates them from memory."""
    return ("ghost thresholds (days without activity, from tracker.py GHOST_DAYS; an application's ghost_after_days "
            "overrides): " + ", ".join(f"{stage} {days}" for stage, days in GHOST_DAYS.items()))


def ghost_flag(proj: dict[str, Any], now: datetime) -> str:
    last = proj.get("last_activity")
    days = int((now - last).total_seconds() // 86400) if last else None
    limit = proj.get("ghost_limit") or GHOST_DAYS.get(proj.get("ghost_from") or "")
    since = f"{days} days since the last activity" if days is not None else "no activity on record"
    return f"ghosted (inferred from {proj.get('ghost_from')}: {since}, threshold {limit})"


class DayStamp(datetime):
    """A date-only event stamp ("2026-09-28"): the same instant parse_ts gives it (00:00 UTC), so ordering and ghost
    arithmetic are unchanged, but marked so _day keeps its own date."""


def _instant(value: Any) -> datetime | None:
    """parse_ts, with a date-only value returned as a DayStamp."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return DayStamp(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        d = date.fromisoformat(value.strip())
        return DayStamp(d.year, d.month, d.day, tzinfo=timezone.utc)
    return parse_ts(value)


def _day(instant: datetime) -> date:
    """The YYYY-MM-DD an event names: a DayStamp keeps its own date; any other instant is the department's day
    (js_common.DEPT_TZ), so an evening submit is that local day, not the UTC morrow."""
    if isinstance(instant, DayStamp):
        return date(instant.year, instant.month, instant.day)
    return dept_day(instant)


def add_business_days(day: date, n: int) -> date:
    """`n` weekdays (Monday to Friday) after `day`; holidays are not known here."""
    while n > 0:
        day = date.fromordinal(day.toordinal() + 1)
        if day.weekday() < 5:
            n -= 1
    return day


def next_action_for(proj: dict[str, Any], now: datetime) -> tuple[str | None, str | None]:
    """(next_action, next_action_at as YYYY-MM-DD or None) for a projection (STAGE_NEXT)."""
    action, when = STAGE_NEXT.get(proj["stage"], (None, None))
    if action is None or when is None:
        return action, None
    entered = proj["transitions"][-1][1] if proj["transitions"] else now
    if when == "entered" and proj.get("requeued_at") and proj["requeued_at"] > entered:
        entered = proj["requeued_at"]
    if when == "follow_up":
        sent = proj.get("follow_ups_since_activity", 0)
        if sent >= len(FOLLOW_UP_OFFSETS):
            return FOLLOW_UPS_DONE, None
        since = proj["last_activity"] or entered
        return action, add_business_days(_day(since), FOLLOW_UP_OFFSETS[sent]).isoformat()
    return action, _day(entered).isoformat()


# ------------------------------------------------------------------------------ files
def app_dir(root: Path) -> Path:
    return Path(root) / "applications"


def load_application(root: Path, slug: str) -> tuple[Path, dict[str, Any], str]:
    path = app_dir(root) / slug / "application.md"
    props, body = fm.load(path)
    return path, props, body


def iter_applications(root: Path):
    folder = app_dir(root)
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*/application.md")):
        props, body = fm.load(path)
        yield path.parent.name, props, body


KIND_FILE = "KIND"                                  # legacy marker inside a folder: read, never written now
PACKAGE_ONLY = "package-only"
PACKAGE_ONLY_STATE = Path("state") / "package-only.yaml"


def package_only_slugs(root: Path) -> set[str]:
    """The slugs state/package-only.yaml lists as package-only folders (empty when the file is absent)."""
    import yaml
    p = Path(root) / PACKAGE_ONLY_STATE
    if not p.is_file():
        return set()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(s) for s in (data.get("slugs") or [])}


def _write_package_only(root: Path, slugs: list[str]) -> Path:
    import os
    import tempfile
    import yaml
    p = Path(root) / PACKAGE_ONLY_STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    head = ("# GENERATED by scripts/tracker.py tag-package-only: application folders with no application.md.\n"
            "# Read by tracker.folder_counts (metrics, dashboard). Do not edit by hand; re-run the command.\n")
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(head + yaml.safe_dump({"slugs": sorted(slugs)}, sort_keys=False))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return p


def iter_folders(root: Path) -> list[Path]:
    """Every application folder, with or without an application.md (dot and underscore folders skipped)."""
    folder = app_dir(root)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def _legacy_marker(folder: Path) -> bool:
    marker = Path(folder) / KIND_FILE
    return marker.is_file() and marker.read_text().strip() == PACKAGE_ONLY


def _marked_package_only(folder: Path, listed: set[str] | None = None) -> bool:
    folder = Path(folder)
    if listed is None:
        listed = package_only_slugs(folder.parent.parent)      # applications/<slug> -> the department root
    return folder.name in listed or _legacy_marker(folder)


def folder_kind(folder: Path, listed: set[str] | None = None) -> str:
    """application (application.md exists; it wins over any tag) | package-only (state/package-only.yaml or a legacy
    KIND marker says so) | untagged."""
    if (Path(folder) / "application.md").is_file():
        return "application"
    return PACKAGE_ONLY if _marked_package_only(folder, listed) else "untagged"


def folder_counts(root: Path) -> dict[str, int]:
    listed = package_only_slugs(root)
    kinds = [folder_kind(f, listed) for f in iter_folders(root)]
    return {"folders": len(kinds), "applications": kinds.count("application"),
            "package_only": kinds.count(PACKAGE_ONLY), "untagged": kinds.count("untagged")}


def tag_package_only(root: Path, dry_run: bool = False) -> dict[str, Any]:
    """List folders with no application.md in state/package-only.yaml. Returns the newly tagged slugs, the slugs
    dropped (their folder now has application.md), stale legacy KIND markers, legacy markers folded in, and the
    counts after. Writes only the state file; nothing inside an application folder."""
    listed = package_only_slugs(root)
    tagged, stale, legacy, keep = [], [], [], []
    for folder in iter_folders(root):
        has_app = (folder / "application.md").is_file()
        if has_app:
            if _legacy_marker(folder):
                stale.append(folder.name)
            continue
        keep.append(folder.name)
        if folder.name not in listed:
            (legacy if _legacy_marker(folder) else tagged).append(folder.name)
    present = {f.name for f in iter_folders(root)}
    dropped = sorted(s for s in listed if s in present and s not in keep)
    gone = sorted(s for s in listed if s not in present)                  # folder moved or archived: keep listed
    if not dry_run and (tagged or legacy or dropped or not (Path(root) / PACKAGE_ONLY_STATE).is_file()):
        _write_package_only(root, keep + gone)
    counts = folder_counts(root)
    if dry_run:
        counts = {**counts, "package_only": counts["package_only"] + len(tagged),
                  "untagged": counts["untagged"] - len(tagged)}
    return {"tagged": tagged, "stale": stale, "dropped": dropped, "legacy": legacy, **counts}


def _link_target(value: Any) -> str | None:
    m = re.match(r"^\[\[([^\]|#]+)", str(value or "").strip())
    return m.group(1) if m else (str(value).strip() or None if value else None)


def refresh(root: Path, slug: str, now: datetime, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """Re-project one application. With unparseable event lines present, a backwards stage move is held
    (proj["held"] = {"from", "to"}; nothing written) unless force."""
    path, props, body = load_application(root, slug)
    proj = project(parse_events(body), now, props.get("ghost_after_days"), props.get("last_inbound_at"))
    proj["warnings"] = event_warnings(root, slug)
    proj["held"] = None
    if proj["warnings"] and not force and stage_rank(proj["stage"]) < stage_rank(props.get("stage")):
        proj["held"] = {"from": props.get("stage"), "to": proj["stage"]}
        return proj
    if proj["stage"] != props.get("stage") or props.get("next_action") in TRACKER_ACTIONS:
        action, due = next_action_for(proj, now)
        if due is None and proj["stage"] == props.get("stage") and props.get("next_action") == action:
            due = props.get("next_action_at")          # a round date or deadline someone added is kept
        props["next_action"], props["next_action_at"] = action, due
    props["stage"] = proj["stage"]
    props["outcome"] = OUTCOME.get(proj["stage"], "pending")
    if proj["applied_at"]:
        props["applied_at"] = _day(proj["applied_at"]).isoformat()   # the local day, not the UTC date
    props["ghosted_inferred"] = proj["ghosted"] or None
    props["updated"] = dept_day(now).isoformat()
    if not dry_run:
        write_record(path, props, body)
        job_id = _link_target(props.get("job"))
        jobs = load_jobs(root)
        if job_id in jobs:
            job, changed = jobs[job_id], False
            if proj["max_progress"] in PROGRESS[PROGRESS.index("applied"):] \
                    and job.get("status") in ("discovered", "fit_checked", "queued", "tailoring"):
                job["status"], changed = "applied", True
            changed = link_job(job, slug) or changed
            if changed:
                save_job(root, job)
    return proj


def link_job(job: dict[str, Any], slug: str) -> bool:
    """Set the job's `application:` to "[[slug]]" when it names none (the first record of a job keeps the link;
    a duplicate never takes it over). True when it changed."""
    if _link_target(job.get("application")):
        return False
    job["application"] = f"[[{slug}]]"
    return True


APPLIED_TIME_STATED = "time: stated"
APPLIED_TIME_CLOCK = "time: clock (no --at; submission time not stated)"


HEX = re.compile(r"^[0-9a-f]{8,64}$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_folder(rel: str) -> str | None:
    """resumes/variants/<name>/... or resumes/bases/<name>/... -> that folder; else None."""
    parts = rel.split("/")
    return "/".join(parts[:3]) if len(parts) >= 4 and parts[0] == "resumes" and parts[1] in ("variants", "bases") \
        else None


def resolve_resume(root: Path, value: str | None, reason: str | None = None) -> dict[str, Any]:
    """The page an applied event sent (post-mortem change 3): a PDF path under resumes/, a sha256 (or 8+ hex prefix)
    of a PDF under resumes/, or none with a reason. Raises TrackerError naming what is missing."""
    value = (value or "").strip()
    tail = "; nothing was written"
    if reason is not None and value.lower() != "none":
        raise TrackerError("refused: --reason goes with --resume none" + tail)
    if not value:
        raise TrackerError("refused: an applied event names the page that went: pass --resume <PDF path under resumes/ "
                           "or its sha256>, or --resume none --reason \"<why>\" so a rejection can be traced to "
                           "the page it saw)" + tail)
    if value.lower() == "none":
        why = _clean(reason, 200)
        if not why:
            raise TrackerError("refused: --resume none needs --reason \"<why no PDF went>\"" + tail)
        return {"resume_variant": None, "resume_pdf": None, "resume_sha256": None, "resume_none_reason": why,
                "note": f"resume none: {why}"}
    root = Path(root)
    if HEX.match(value.lower()):
        inside = root.resolve()
        pdfs = [p for p in (root / "resumes").rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                and p.resolve().is_relative_to(inside)] if (root / "resumes").is_dir() else []   # no links out
        found = sorted((h, p.relative_to(root).as_posix()) for p in pdfs
                       if (h := _sha256(p)).startswith(value.lower()))
        if not found:
            raise TrackerError(f"refused: no PDF under resumes/ has sha256 {value}; pass the PDF's path, or copy the "
                               "page that went under its variant folder" + tail)
        if len({h for h, _ in found}) > 1:
            raise TrackerError(f"refused: sha256 prefix {value} matches {len({h for h, _ in found})} different PDFs; "
                               "give more of the hash" + tail)
        folders = sorted({_page_folder(rel) or rel for _, rel in found})
        if len(folders) > 1:                       # the same page in two variant folders: the hash cannot say which
            raise TrackerError(f"refused: sha256 {value} is the same PDF in {len(folders)} folders "
                               f"({', '.join(folders)}); pass the path of the copy that went" + tail)
        sha, rel = found[0]
    else:
        path = (root / value) if not Path(value).is_absolute() else Path(value)
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise TrackerError(f"refused: {value} is outside the department; pass its sha256 (the page under "
                               "resumes/ with the same hash is recorded)" + tail) from None
        if not path.is_file():
            raise TrackerError(f"refused: --resume {value}: no such file" + tail)
        if not rel.startswith("resumes/") or path.suffix.lower() != ".pdf":
            raise TrackerError(f"refused: --resume {value} is not a PDF under resumes/" + tail)
        sha = _sha256(path)
    return {"resume_variant": _page_folder(rel), "resume_pdf": rel, "resume_sha256": sha, "resume_none_reason": None,
            "note": f"resume {rel} sha256 {sha[:12]}"}


def recorded_resume(root: Path, slug: str) -> dict[str, Any]:
    """--resume same: a repeat applied event (a time correction) keeps the page already recorded."""
    try:
        _path, props, _body = load_application(root, slug)
    except (OSError, ValueError, TrackerError):
        props = {}
    keys = ("resume_variant", "resume_pdf", "resume_sha256", "resume_none_reason")
    if not (props.get("resume_pdf") or props.get("resume_none_reason")):
        raise TrackerError(f"refused: --resume same, but nothing is recorded yet on {slug}; pass the PDF's path or "
                           "sha256, or none --reason; nothing was written")
    what = props.get("resume_pdf") or f"none: {props.get('resume_none_reason')}"
    return {**{k: props.get(k) for k in keys}, "note": f"resume same: {what}"}


def is_manual_applied(type_: str, source: str) -> bool:
    """A manual `applied` event (the owner's word or apply-assist's confirmation), whose time must be stated."""
    return EVENT_STAGE.get(type_.strip().lower()) == "applied" and source.strip().lower() == "manual"


def append_event(root: Path, slug: str, type_: str, source: str = "manual", conf: float = 1.0,
                 evidence: str = "", at: str | None = None, now: datetime | None = None,
                 dry_run: bool = False, applied_via: str | None = None, force: bool = False,
                 override: str | None = None, resume: dict[str, Any] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if at is not None and parse_ts(at) is None:
        raise TrackerError(f"refused: --at {at!r} is not an ISO timestamp (e.g. 2026-09-28T16:15:00Z or "
                           "2026-09-28T09:15:00-07:00); nothing was written")
    if not (app_dir(root) / slug).is_dir():
        raise TrackerError(f"refused: no applications/{slug}/ folder; create it with "
                           "`python3 scripts/tracker.py new --job <job id>` (or check the slug); nothing was written")
    if is_manual_applied(type_, source):
        stamp = APPLIED_TIME_STATED if at else APPLIED_TIME_CLOCK
        evidence = f"{stamp}; {evidence}" if _clean(evidence) else stamp
        if not at:
            print(f"warning: manual {type_.strip()} event on {slug} without --at: recorded at {iso_utc(now)} from the "
                  "run clock and marked 'time: clock'; pass --at with the stated submission time (date, time and "
                  "timezone from the confirmation or the owner's word)", file=sys.stderr)
    override = _clean(override, 200) or None
    if type_.strip().lower() in GHOST_TYPES:
        if not override:
            raise TrackerError(GHOST_REFUSAL)
        evidence = f"override: {override}" + (f"; {evidence}" if _clean(evidence) else "")
    if not (app_dir(root) / slug / "application.md").is_file():
        if dry_run:
            return {"would_adopt": True, "warnings": [], "held": None}
        adopt_package(root, slug, now)
    path, props, body = load_application(root, slug)
    if type_.strip().lower() in REQUEUE_TYPES:
        if not _clean(evidence):
            raise TrackerError("refused: a requeue names why the item is still worth the work (--evidence \"posting "
                               "live: <how it was checked>\"); nothing was written")
        if STAGE_NEXT.get(str(props.get("stage")), (None, None))[1] != "entered":
            raise TrackerError(f"refused: a requeue moves the due date of a discovered, fit_checked or tailored item; "
                               f"{slug} is at {props.get('stage')}; nothing was written")
        proj = project(parse_events(body), now)
        began = proj["transitions"][-1][1] if proj["transitions"] else None
        done = sum(1 for e in parse_events(body) if e.type.strip().lower() in REQUEUE_TYPES
                   and (began is None or (_instant(e.at) or now) >= began))
        if done >= MAX_REQUEUES:
            raise TrackerError(f"refused: {slug} was requeued twice already at {props.get('stage')}: work it, hold it "
                               "(on_hold) or retire it; nothing was written")
    if applied_via:
        props["applied_via"] = applied_via
    if resume:                                     # post-mortem change 3: the page that went, by path and hash
        for key in ("resume_variant", "resume_pdf", "resume_sha256", "resume_none_reason"):
            if resume.get(key) is not None or key in props:
                props[key] = resume.get(key)
        evidence = f"{evidence}; {resume['note']}" if _clean(evidence) else resume["note"]
    line = format_event(at or iso_utc(now), type_, source, conf, evidence)
    body = append_section_line(body, "Events", line)
    if type_.strip().lower() in INBOUND:
        props["last_inbound_at"] = _day(_instant(at) or now).isoformat()
    props["updated"] = dept_day(now).isoformat()          # the run date, even when the re-projection is held
    if not dry_run:
        write_record(path, props, body)
    return refresh(root, slug, now, dry_run=dry_run, force=force)


FIT_HEADINGS = ["Hard gates", "Seniority band", "Lens", "Strongest matches (evidence ids)", "Biggest gaps",
                "Sponsorship evidence", "Wage level estimate", "Ghost score", "Score and verdict"]


def _package_props(path: Path, skipped: list[str] | None) -> dict[str, Any] | None:
    """A package file's frontmatter, or None when it does not parse: the folder is skipped with a warning naming
    the file on stderr and in `skipped`, so one broken package never stops `new` for every other job."""
    try:
        return fm.load(path)[0]
    except (ValueError, OSError) as exc:
        try:
            rel = path.relative_to(path.parents[2]).as_posix()
        except (IndexError, ValueError):
            rel = str(path)
        msg = f"skipped {rel}: {str(exc).splitlines()[0]}"
        if skipped is None or msg not in skipped:            # once per file per run
            print(f"warning: {msg}", file=sys.stderr)
            if skipped is not None:
                skipped.append(msg)
        return None


def _prepared_for(folder: Path, job_id: str, skipped: list[str] | None = None) -> bool:
    """True when fit-scorer already wrote this job's jd.md or fit.md here and no application exists yet."""
    if (folder / "application.md").exists():
        return False
    for name in ("jd.md", "fit.md"):
        if (folder / name).is_file():
            props = _package_props(folder / name, skipped)
            if props is None:
                return False
            if job_id in (_link_target(props.get("job")), _link_target(props.get("job_id"))):
                return True
    return False


def _norm_text(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def _package_for(root: Path, job_id: str, job: dict[str, Any], skipped: list[str] | None = None) -> str | None:
    """A package-only folder already prepared for this job, in any month: its jd.md or fit.md names the job id; else
    its company and title match the job's (so `new` does not mint a second folder, e.g. acme-...-202610, beside
    acme-...-202609)."""
    folders = [f for f in iter_folders(root) if not (f / "application.md").exists()]
    for f in folders:
        if _prepared_for(f, job_id, skipped):
            return f.name
    want = (_norm_text(job.get("title")), {_norm_text(job.get("company")), _norm_text(job.get("company_slug"))} - {""})
    for f in folders:
        for name in ("jd.md", "fit.md"):
            if (f / name).is_file():
                props = _package_props(f / name, skipped)
                if props is None:
                    break                                          # unreadable: skipped, named in the warning
                if _link_target(props.get("job")) not in (None, job_id):
                    break                                          # the folder names another job
                if want[0] and _norm_text(props.get("title")) == want[0] and _norm_text(props.get("company")) in want[1]:
                    return f.name
    return None


def _drop_package_only(root: Path, slug: str) -> None:
    listed = package_only_slugs(root)
    if slug in listed:
        _write_package_only(root, sorted(listed - {slug}))


def new_application(root: Path, job_id: str, now: datetime, dry_run: bool = False,
                    discovered_at: str | None = None, skipped: list[str] | None = None) -> tuple[str, bool]:
    """Create applications/<slug>/ for a job. Returns (slug, created). Idempotent per job. `discovered_at`
    (ISO date or timestamp, not after `now`) stamps the first event at the stated discovery time."""
    if discovered_at is not None:
        stated = parse_ts(discovered_at)
        if stated is None:
            raise TrackerError(f"refused: --discovered-at {discovered_at!r} is not an ISO date or timestamp "
                               "(e.g. 2026-08-10 or 2026-08-10T09:00:00-07:00); nothing was written")
        if stated > now:
            raise TrackerError(f"refused: --discovered-at {discovered_at!r} is after the run clock ({iso_utc(now)}); "
                               "nothing was written")
    jobs = load_jobs(root)
    if job_id not in jobs:
        raise KeyError(f"no job {job_id} under {Path(root) / 'jobs'}")
    job = jobs[job_id]
    for slug, props, _ in iter_applications(root) or []:
        if _link_target(props.get("job")) == job_id:
            if not dry_run and link_job(job, slug):
                save_job(root, job)
            return slug, False
    slug = _package_for(root, job_id, job, skipped)        # adopt the job's package folder, whatever its month
    if slug is None:
        base = f"{job.get('company_slug')}-{slugify(job.get('title') or 'role', 40)}-{now:%Y%m}"
        slug, n = base, 2
        while (app_dir(root) / slug).exists() and not _prepared_for(app_dir(root) / slug, job_id, skipped):
            bad = [m for m in (skipped or []) if f"/{slug}/" in m]
            if bad:                                          # its own package may be unreadable: never mint a sibling
                raise TrackerError(f"refused: applications/{slug}/ holds a package file that does not parse ({bad[0]}); "
                                   "quote its frontmatter, then run `new` again")
            slug, n = f"{base}-{n}", n + 1
    if dry_run:
        return slug, True
    folder = app_dir(root) / slug
    today = dept_day(now).isoformat()
    link = f"[[{job_id}]]"
    title, company = job.get("title"), job.get("company")
    jd = job.get("_jd_text") or jd_text_from_body(job.get("_body") or "")
    if not (folder / "jd.md").exists():
        write_record(folder / "jd.md", {"job": link, "company": company, "title": title,
                                        "source_url": job.get("source_url"), "apply_url": job.get("apply_url"),
                                        "snapshot_at": iso_utc(now)},
                     f"# JD snapshot: {title} at {company}\n\n{wrap_untrusted(jd, 'jd', limit=30_000)}\n")
    gate_lines = "\n".join(f"- {r}" for r in job.get("gate_reasons") or []) or "- not run yet (scripts/fit_gates.py)"
    prefill = {
        "Hard gates": f"gate: {job.get('gate') or 'unknown'}\n{gate_lines}",
        "Sponsorship evidence": f"- sponsorship: {job.get('sponsorship') or 'unknown'}\n"
                                f"- evidence: {job.get('sponsorship_evidence') or 'none yet'}\n"
                                f"- H-1B approvals (3 FY): {job.get('h1b_approvals_3y')}",
        "Wage level estimate": f"- LCA wage level: {job.get('wage_level_est') or 'unknown'}; "
                               f"median LCA wage: {job.get('median_lca_wage')}",
        "Ghost score": f"- {job.get('ghost_score')}",
    }
    fit_body = f"# Fit: {title} at {company}\n\n" + "".join(
        f"## {h}\n\n{prefill.get(h, '')}\n\n" for h in FIT_HEADINGS)
    if not (folder / "fit.md").exists():
        write_record(folder / "fit.md", {"job": link, "application": slug, "fit_score": None,
                                         "fit_verdict": job.get("fit_verdict") or "unscored", "scored_by": None},
                     fit_body)
    first = "fit_checked" if job.get("status") in ("fit_checked", "queued", "tailoring") else "discovered"
    first_at = discovered_at or iso_utc(now)
    _write_application(root, folder, link, company, title, job.get("company_slug") or company,
                       job.get("wage_level_est"), first, first_at,
                       f"created from {link}" + ("; discovered-at stated" if discovered_at else ""), now)
    job["application"] = f"[[{slug}]]"
    if job.get("status") in ("discovered", "fit_checked", None, ""):
        job["status"] = "queued"
    save_job(root, job)
    _drop_package_only(root, slug)
    return slug, True


def _event_lines(body: str) -> list[str]:
    sec = body.split("## Events", 1)[1] if "## Events" in body else ""
    sec = sec.split("\n## ", 1)[0]
    return [l for l in sec.splitlines() if l.startswith("- ")]


def _folder_job(folder: Path) -> str | None:
    for name in ("application.md", "jd.md", "fit.md"):
        if (folder / name).is_file():
            props, _ = fm.load(folder / name)
            jid = _link_target(props.get("job")) or _link_target(props.get("job_id"))
            if jid:
                return jid
    return None


def merge_plan(root: Path, src: str, dst: str) -> dict[str, Any]:
    a, b = app_dir(root) / src, app_dir(root) / dst
    if not (a / "application.md").is_file():
        raise TrackerError(f"refused: applications/{src}/application.md does not exist; nothing to merge")
    if not b.is_dir() or src == dst:
        raise TrackerError(f"refused: no applications/{dst}/ folder to merge into")
    ja, jb = _folder_job(a), _folder_job(b)
    if ja and jb and ja != jb:
        raise TrackerError(f"refused: {src} is job {ja} and {dst} is job {jb}; merge joins one job's folders only")
    src_lines = _event_lines(fm.load(a / "application.md")[1])
    if (b / "application.md").is_file():
        have = set(_event_lines(fm.load(b / "application.md")[1]))
        mode, events = "append", [l for l in src_lines if l not in have]
    else:
        mode, events = "move", src_lines
    left = sorted(p.name for p in a.iterdir() if p.is_file())
    return {"from": src, "into": dst, "job": ja or jb, "mode": mode, "events": events, "left": left,
            "outcome_copied": mode == "move" and (a / "outcome.md").is_file() and not (b / "outcome.md").is_file()}


DECISION_OWNER = "owner"      # data vocabulary: a mail-events row ruled by the owner; renamed centrally with the data
APP_TIERS = ("dream", "focused", "mass")           # a recorded field, set only here


def set_tier(root: Path, slug: str, tier: str, source: str, now: datetime, dry_run: bool = False) -> dict[str, Any]:
    """Record an application's tier and why. Refused (TrackerError): a tier outside dream, focused,
    mass; a blank --source; no application record. The resume-for step that makes the page sets it."""
    if tier not in APP_TIERS:
        raise TrackerError(f"refused: tier {tier!r} is not one of {', '.join(APP_TIERS)}")
    if not str(source or "").strip():
        raise TrackerError("refused: --source must say why this tier")
    path = app_dir(root) / slug / "application.md"
    if not path.is_file():
        raise TrackerError(f"refused: no application record for {slug!r}")
    _, props, body = load_application(root, slug)
    prev = props.get("tier")
    if not dry_run:
        props["tier"], props["tier_source"] = tier, str(source).strip()
        props["updated"] = dept_day(now).isoformat()
        write_record(path, props, body)
    return {"application": slug, "from": prev, "to": tier}


APP_PATHS = ("person", "cold", "unknown")           # B.1: who carried the application; set only by set_path


def set_path(root: Path, slug: str, path: str, via: str | None, source: str, now: datetime,
             dry_run: bool = False) -> dict[str, Any]:
    """Record whether a person carried an application or it went cold, the prospect who carried it and
    the evidence. Refused (TrackerError): a path outside person, cold, unknown; person without --via, or with a
    --via that names no prospects/<id>.md; cold or unknown with --via; a blank --source; no application record.
    The same three values again write nothing (`changed` is empty); different values overwrite all three."""
    if path not in APP_PATHS:
        raise TrackerError(f"refused: path {path!r} is not one of {', '.join(APP_PATHS)}")
    via = str(via or "").strip() or None
    if path == "person":
        if via is None:
            raise TrackerError("refused: path person needs --via <prospect id> (the person who carried it)")
        if "/" in via or via.startswith(".") or not (Path(root) / "prospects" / f"{via}.md").is_file():
            raise TrackerError(f"refused: --via {via!r} names no prospect record (prospects/{via}.md)")
    elif via is not None:
        raise TrackerError(f"refused: path {path} takes no --via (only person names a prospect)")
    if not str(source or "").strip():
        raise TrackerError("refused: --source must cite the evidence for this path (a file path, an event, a ruling)")
    file = app_dir(root) / slug / "application.md"
    if not file.is_file():
        raise TrackerError(f"refused: no application record for {slug!r}")
    _, props, body = load_application(root, slug)
    new = {"path": path, "via_prospect": via, "path_source": str(source).strip()}
    changed = {k: (props.get(k), v) for k, v in new.items() if k not in props or props[k] != v}
    prev = props.get("path")
    if changed and not dry_run:
        props.update(new)
        props["updated"] = dept_day(now).isoformat()
        write_record(file, props, body)
    return {"application": slug, "from": prev, "to": path, "changed": changed}


def merge(root: Path, src: str, dst: str, now: datetime) -> dict[str, Any]:
    """Run merge_plan's plan. Writes INTO's application.md (and outcome.md), the job link and the package-only list;
    never deletes FROM."""
    plan = merge_plan(root, src, dst)
    a, b = app_dir(root) / src, app_dir(root) / dst
    if plan["mode"] == "move":
        props, body = fm.load(a / "application.md")
        write_record(b / "application.md", {**props, "id": dst}, body)
        if plan["outcome_copied"]:
            ometa, obody = fm.load(a / "outcome.md")
            write_record(b / "outcome.md", {**ometa, "application": dst}, obody)
    elif plan["events"]:
        props, body = fm.load(b / "application.md")
        lines = body.split("\n")
        idx = max(i for i, l in enumerate(lines) if l in set(_event_lines(body)) or l.strip() == "## Events")
        lines[idx + 1:idx + 1] = plan["events"]
        write_record(b / "application.md", props, "\n".join(lines))
    refresh(root, dst, now)
    if plan["job"]:
        jobs = load_jobs(root)
        if plan["job"] in jobs:
            jobs[plan["job"]]["application"] = f"[[{dst}]]"
            save_job(root, jobs[plan["job"]])
    _drop_package_only(root, dst)
    return plan


def _write_application(root: Path, folder: Path, link: str | None, company: Any, title: Any, company_key: Any,
                       wage_level: Any, first: str, first_at: str, evidence: str, now: datetime) -> None:
    """Write application.md (and outcome.md when absent) for one folder; the slug is the folder name."""
    slug, today = folder.name, dept_day(now).isoformat()
    next_action, next_action_at = STAGE_NEXT[first][0], _day(_instant(first_at) or now).isoformat()
    app_props = {
        "id": slug, "job": link, "company": company,
        "company_slug": CompanyIndex.load(root).resolve(company_key)[0] if company_key else None,
        "title": title,
        "stage": first, "round": 0, "applied_at": None, "applied_via": None, "resume_variant": None,
        "cover_variant": None, "prospects": [], "thread_ids": [], "next_action": next_action,
        "next_action_at": next_action_at, "last_inbound_at": None, "ghost_after_days": None,
        "sponsorship_confirmed": None, "wage_level": wage_level, "outcome": "pending",
        "created": today, "updated": today,
    }
    app_body = f"# {title} at {company}\n\n## Events\n" + format_event(first_at, first, "manual", 1.0, evidence) + "\n"
    write_record(folder / "application.md", app_props, app_body)
    if not (folder / "outcome.md").exists():
        write_record(folder / "outcome.md", {"application": slug, "outcome": "pending"},
                     f"# Outcome: {title} at {company}\n\n## Outcome\n\n## Timeline summary\n\n## What worked\n\n"
                     "## What to change\n\n## Signals (team, visa, comp)\n")


def adopt_package(root: Path, slug: str, now: datetime) -> None:
    """Give a package-only folder its application record in place. Facts
    come from the job record when jobs/ has the folder's job, else from jd.md; jd.md and fit.md are kept."""
    folder = app_dir(root) / slug
    jd_props = fm.load(folder / "jd.md")[0] if (folder / "jd.md").is_file() else {}
    fit_props = fm.load(folder / "fit.md")[0] if (folder / "fit.md").is_file() else {}
    job_id = (_link_target(jd_props.get("job")) or _link_target(jd_props.get("job_id"))
              or _link_target(fit_props.get("job")))
    job = load_jobs(root).get(job_id or "")
    company = (job or {}).get("company") or jd_props.get("company")
    title = (job or {}).get("title") or jd_props.get("title")
    stated = parse_ts(str(jd_props.get("snapshot_at") or ""))
    first_at = iso_utc(stated) if stated and stated <= now else iso_utc(now)
    link = f"[[{job_id}]]" if job_id else None
    _write_application(root, folder, link, company, title, (job or {}).get("company_slug") or company,
                       (job or {}).get("wage_level_est"), "discovered", first_at,
                       "adopted package-only folder (jd.md and fit.md kept)"
                       + (f"; job {link}" if link else "; no job record") + "; time: jd snapshot or run clock", now)
    if job:
        job["application"] = f"[[{slug}]]"
        if job.get("status") in ("discovered", "fit_checked", None, ""):
            job["status"] = "queued"
        save_job(root, job)


# ------------------------------------------------------------------------------ metrics
def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 1) if values else None


def metrics(root: Path, now: datetime) -> dict[str, Any]:
    jobs = load_jobs(root)
    funnel = {s: 0 for s in PROGRESS}
    outcomes = {s: 0 for s in TERMINAL}
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "responded": 0})
    by_via: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "responded": 0})
    by_path: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "responded": 0, "screen": 0})
    by_channel: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "responded": 0, "screen": 0})
    by_order: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "responded": 0, "screen": 0})
    import channel_report                                     # channel and base order next to path
    listed = channel_report.listed_recruiters(root)
    durations: dict[str, list[float]] = defaultdict(list)
    latencies: list[float] = []
    warnings: list[dict[str, str]] = []
    count = 0
    for _slug, props, body in iter_applications(root) or []:
        count += 1
        warnings.extend(event_warnings(root, _slug))
        events = parse_events(body)
        proj = project(events, now, props.get("ghost_after_days"), props.get("last_inbound_at"))
        top = PROGRESS.index(proj["max_progress"])
        for s in PROGRESS[: top + 1]:
            funnel[s] += 1
        if proj["stage"] in outcomes:
            outcomes[proj["stage"]] += 1
        trans = proj["transitions"]
        for (s, at), (_nxt, at2) in zip(trans, trans[1:]):
            durations[s].append((at2 - at).total_seconds() / 86400)
        if proj["applied_at"] is None:
            continue
        job = jobs.get(_link_target(props.get("job")) or "", {})
        responded = any(EVENT_STAGE.get(e.type.lower()) in RESPONDED or
                        (e.type.lower().startswith("override:") and e.type.split(":", 1)[1] in RESPONDED)
                        for e in events)
        for bucket, key in ((by_source, job.get("source") or "unknown"), (by_via, props.get("applied_via") or "unknown")):
            bucket[key]["applied"] += 1
            bucket[key]["responded"] += int(responded)
        path_key = props.get("path") or "missing"                 # B.1: absent until `tracker.py path` records it
        by_path[path_key]["applied"] += 1
        by_path[path_key]["responded"] += int(responded)
        by_path[path_key]["screen"] += int(top >= PROGRESS.index("screen"))
        channel, order = channel_report.classify(root, props, listed)
        for bucket, key in ((by_channel, channel), (by_order, order)):
            bucket[key]["applied"] += 1
            bucket[key]["responded"] += int(responded)
            bucket[key]["screen"] += int(top >= PROGRESS.index("screen"))
        posted = parse_ts(job.get("posted_at"))
        if posted and proj["applied_at"] >= posted:          # applying before posting = bad data, skip
            latencies.append((proj["applied_at"] - posted).total_seconds() / 3600)

    def rates(bucket: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
        return {k: {**v, "rate": round(v["responded"] / v["applied"], 3)} for k, v in sorted(bucket.items())}

    import interviews        # interviews held counted apart from booked (one round model)
    rounds = interviews.totals(root, now.astimezone(interviews.LA).date())

    return {
        "generated_at": iso_utc(now),
        "applications": count,
        **{k: v for k, v in folder_counts(root).items() if k != "applications"},   # folders, package_only, untagged
        "funnel": funnel,
        "outcomes": outcomes,
        "response_rate_by_source": rates(by_source),
        "response_rate_by_applied_via": rates(by_via),
        "by_path": rates(by_path),
        "by_channel": rates(by_channel),             # person, recruiter, post, cold, unknown (scripts/channel_report.py)
        "by_base_order": rates(by_order),            # education_first, experience_first, unknown (the mass A/B)
        "median_days_in_stage": {s: _median(v) for s, v in durations.items() if v},
        "apply_latency_hours": {"median": _median(latencies), "within_72h": sum(1 for h in latencies if h <= 72),
                                "measured": len(latencies)},
        "interviews": rounds,
        "warnings": warnings,
    }


# ------------------------------------------------------------------------------ reconcile (mail-events rows)
MAIL_LEDGER = Path("state") / "mail-events.jsonl"
LABEL_EVENT = {"ats_confirmation": "confirmation_received", "ats_assessment": "assessment_received",
               "rejection": "rejection", "interview_scheduling": "email_in", "offer": "email_in"}
OUTREACH_LABELS = ("recruiter_outreach", "founder_outreach", "hiring_manager_outreach", "referral_intro", "networking")
NO_EFFECT_LABELS = ("job_alert", "newsletter", "other")
ROUNDS = ("screen", "interviewing", "final")
# The invite's own words name the round (roles/tracker-ops.md); first match wins, in this order.
_ROUND_WORDS = (
    ("final", re.compile(r"\b(final|onsite|on-site|on site|super ?day)\b", re.I)),
    ("interviewing", re.compile(r"\b(technical|coding|system design|panel|hiring manager|pair(ing)? session|"
                                r"team interview|meet the team|with (two|three|\d+) engineers)\b", re.I)),
    ("screen", re.compile(r"\b(recruiter (call|screen|chat)|phone screen|screen(ing)? call|screen\b|"
                          r"intro(ductory)? call|initial (call|chat|conversation))", re.I)),
)
REQ_SIGNALS = ("req_id", "job_url")
# mail_intake.py: the sender or subject names the company, and the title (company_title) or the company has
# one application folder (company_sole), in the application's date window; attached under the same thresholds
AUTO_SIGNALS = REQ_SIGNALS + ("company_title",)   # company_sole is review-only: a second, unrecorded role at the same company would be misfiled


@dataclass
class Reconciled:
    message_id: str
    status: str                  # attached | held | skipped | noop
    reason: str = ""
    slug: str | None = None
    row: dict[str, Any] | None = None
    lines: list[str] | None = None   # extra lines to print (notes, needs the owner)

    def line(self, dry_run: bool) -> str:
        if self.status == "attached":
            return f"{'would attach' if dry_run else 'attached'} {self.message_id} -> {self.slug}: {self.reason}"
        if self.status == "held":
            return f"held {self.message_id}" + (f" ({self.slug})" if self.slug else "") + f": {self.reason}"
        if self.status == "skipped":
            return f"skipped {self.message_id}: {self.reason}"
        return f"no event {self.message_id}: {self.reason}"


def invite_round(evidence: str) -> str | None:
    for stage, pattern in _ROUND_WORDS:
        if pattern.search(evidence or ""):
            return stage
    return None


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The latest row per message_id, in the order each message first appeared (a correction supersedes)."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        mid = str(row.get("message_id") or "").strip()
        if mid:
            latest.pop(mid, None)
            latest[mid] = row
    return list(latest.values())


def _events_text(root: Path, slug: str) -> str:
    path = app_dir(root) / slug / "application.md"
    if not path.is_file():
        return ""
    _, body = fm.load(path)
    m = re.search(r"(?ms)^## Events\n(.*?)(?=^## |\Z)", body)
    return m.group(1) if m else ""


def _id_re(mid: str) -> re.Pattern:
    return re.compile(r"(?<![\w@-])" + re.escape(mid.strip("<>")) + r"(?![\w@-])")


def _recorded(root: Path, mid: str) -> list[str]:
    pattern = _id_re(mid)
    return [slug for slug, _m, _b in iter_applications(root) or [] if pattern.search(_events_text(root, slug))]


def _event_for(row: dict[str, Any], rounds: dict[str, str]) -> tuple[str | None, str | None]:
    """(event type, held reason) for a row's label."""
    label, mid = row.get("label"), str(row.get("message_id"))
    if label == "interview_invite":
        stage = rounds.get(mid) or invite_round(str(row.get("evidence") or ""))
        if stage is None:
            return None, (f"interview_invite whose evidence does not name the round; read the invite and rerun with "
                          f"--round {mid}=screen|interviewing|final")
        return stage, None
    return LABEL_EVENT.get(str(label)), None


def _match_problem(root: Path, row: dict[str, Any], props: dict[str, Any]) -> str | None:
    """Why an auto row's match is not confirmed (None = confirmed)."""
    import mail_events as me                                   # the match rule's thresholds live there
    m = row.get("match") or {}
    signal = m.get("signal")
    try:
        score, ru = float(m.get("score") or 0), float(m.get("runner_up_score") or 0)
        conf = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        return "match score or confidence is not a number"
    if signal == "thread":
        tid = str(row.get("thread_id") or "")
        threads = [str(t) for t in props.get("thread_ids") or []]
        if tid and tid in threads:
            return None
        return (f"signal thread, but thread {tid or '(none)'} is not in the application's thread_ids "
                f"({', '.join(threads) or 'none'})")
    if signal in AUTO_SIGNALS:
        if not me.auto_allowed(score, ru):
            return (f"{signal} {score:.2f} with a lead of {score - ru:.2f} over {m.get('runner_up')}: auto needs "
                    f"{me.AUTO_MIN_SCORE:.2f} and a {me.AUTO_MIN_LEAD:.2f} lead")
        if conf + 1e-6 < me.AUTO_MIN_SCORE:
            return f"{signal} match, but the label confidence {conf:.2f} is under {me.AUTO_MIN_SCORE:.2f}"
        return None
    return (f"signal {signal} {score:.2f} cannot attach automatically (only a thread in thread_ids, or a req id, "
            f"job URL, company_title or company_sole at {me.AUTO_MIN_SCORE:.2f} with a {me.AUTO_MIN_LEAD:.2f} lead)")


def _candidates_text(row: dict[str, Any]) -> str:
    m = row.get("match") or {}
    cands = m.get("candidates")
    if isinstance(cands, list) and cands:
        return ", ".join(f"{c.get('application')} {float(c.get('score') or 0):.2f} ({c.get('signal')})"
                         for c in cands if isinstance(c, dict))
    parts = []
    if row.get("application") or m.get("score"):
        parts.append(f"{row.get('application') or '(top candidate not named)'} {float(m.get('score') or 0):.2f} "
                     f"({m.get('signal')})")
    if m.get("runner_up"):
        parts.append(f"{m['runner_up']} {float(m.get('runner_up_score') or 0):.2f}")
    return ", ".join(parts) or "no candidate"


def _review_line(row: dict[str, Any], reason: str) -> str:
    return (f"- [ ] {row.get('message_id')} · {row.get('label')} · candidates: {_candidates_text(row)} · "
            f"held by tracker.py reconcile: {_clean(reason, 300)} · decision:")


def _write_review(root: Path, day: date, held: list[tuple[dict[str, Any], str]]) -> tuple[Path, int]:
    path = Path(root) / "state" / "review" / f"mail-{day.isoformat()}.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else f"# Mail review {day.isoformat()}\n"
    added = 0
    for row, reason in held:
        if _id_re(str(row.get("message_id"))).search(text):
            continue
        text = append_section_line(text, "Held by tracker.py reconcile", _review_line(row, reason))
        added += 1
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path, added


def _stage_without(events: list[Event], mid: str, now: datetime, props: dict[str, Any]) -> str:
    pattern = _id_re(mid)
    kept = [e for e in events if not pattern.search(e.evidence)]
    return project(kept, now, props.get("ghost_after_days"), props.get("last_inbound_at"))["stage"]


def reconcile_row(root: Path, row: dict[str, Any], now: datetime, rounds: dict[str, str], dry_run: bool,
                  all_rows: list[dict[str, Any]]) -> Reconciled:
    mid = str(row.get("message_id")).strip()
    label = str(row.get("label") or "")
    correction = row.get("correction")
    mark = f"{mid} correction {row.get('logged_at')}" if correction else None
    slug = row.get("application") or None
    decision = row.get("decision")
    extra: list[str] = []
    prefix = f"{mark}; " if mark else ""
    recorded = _recorded(root, mid)
    if mark and any(mark in _events_text(root, s) for s in recorded):
        return Reconciled(mid, "skipped", f"correction already applied ({mark})")
    if recorded and not mark:
        return Reconciled(mid, "skipped", f"already recorded in {', '.join(recorded)}")
    if recorded and mark:
        earlier = [r for r in all_rows if str(r.get("message_id")).strip() == mid and r is not row]
        old = next((r for r in reversed(earlier) if r.get("application") in recorded), None)
        if old is not None and old.get("application") == slug and old.get("label") == label:
            return Reconciled(mid, "noop", "the correction keeps its application and label; nothing to append")
        for old_slug in recorded:                                  # note the superseded event(s), never remove
            path, props, body = load_application(root, old_slug)
            events = parse_events(body)
            superseded = [e for e in events if _id_re(mid).search(e.evidence)]
            what = "; ".join(f"{e.type} at {e.at}" for e in superseded) or "an earlier event"
            note = f"{mark}; supersedes {what}: {_clean(correction, 160)}"
            if not dry_run:
                append_event(root, old_slug, "note", "email", 1.0, note, iso_utc(now), now)
            extra.append(f"note on {old_slug}: supersedes {what}")
            moved = [e for e in superseded if EVENT_STAGE.get(e.type.lower())]
            if moved:
                back = _stage_without(events, mid, now, props)
                current = project(events, now, props.get("ghost_after_days"), props.get("last_inbound_at"))["stage"]
                if back != current and decision == DECISION_OWNER:
                    if not dry_run:
                        append_event(root, old_slug, f"override:{back}", "manual", 1.0,
                                     f"{mark}; owner: {_clean(correction, 160)}", iso_utc(now), now)
                    extra.append(f"override:{back} on {old_slug} (the owner's decision)")
                elif back != current:
                    extra.append(f"needs the owner: {old_slug} is at {current} from the superseded event; it goes back "
                                 f"to {back} only through override:{back} on the owner's decision")
    # ---- the guard
    if decision == "review":
        return Reconciled(mid, "held", "decision review (mail-triage could not decide the match)", slug, row, extra)
    if label == "suspected_scam":
        return Reconciled(mid, "held", "suspected_scam: attached to no application; the owner decides", slug, row, extra)
    if label in NO_EFFECT_LABELS or (label not in LABEL_EVENT and label != "interview_invite"
                                     and label not in OUTREACH_LABELS):
        return Reconciled(mid, "noop", f"label {label} has no application effect", slug, row, extra)
    if not slug:
        return Reconciled(mid, "held", "no application named", None, row, extra)
    if not (app_dir(root) / slug / "application.md").is_file():
        return Reconciled(mid, "held", f"no application {slug}", slug, row, extra)
    path, props, body = load_application(root, slug)
    if decision not in ("auto", DECISION_OWNER):
        return Reconciled(mid, "held", f"decision {decision!r} is not auto, {DECISION_OWNER} or review", slug, row, extra)
    if decision == "auto":
        problem = _match_problem(root, row, props)
        if problem:
            return Reconciled(mid, "held", problem, slug, row, extra)
    if label in OUTREACH_LABELS:
        in_thread = str(row.get("thread_id") or "") in [str(t) for t in props.get("thread_ids") or []]
        if not in_thread:
            return Reconciled(mid, "noop", f"{label} outside the application's threads (outreach roles)", slug, row,
                              extra)
        typ, why = "email_in", None
    else:
        typ, why = _event_for(row, rounds)
    if why:
        return Reconciled(mid, "held", why, slug, row, extra)
    target = EVENT_STAGE.get(str(typ))
    current = project(parse_events(body), now, props.get("ghost_after_days"), props.get("last_inbound_at"))["stage"]
    if target and decision == "auto" and stage_rank(target) < stage_rank(current):
        return Reconciled(mid, "held", f"out of sequence: {label} means {target}, earlier than the application's "
                                       f"current stage {current}", slug, row, extra)
    quote = _clean(row.get("evidence"), 180)
    note = "offer email received; " if label == "offer" else (f"{label}; " if typ == "email_in" else "")
    evidence = f"{prefix}{note}msg {mid}; classifier: '{quote}'"
    try:
        conf = float(row.get("confidence"))
    except (TypeError, ValueError):
        conf = 1.0
    at = str(row.get("at") or "") or iso_utc(now)
    if dry_run:
        return Reconciled(mid, "attached", f"{typ} (dry-run)", slug, row, extra)
    proj = append_event(root, slug, str(typ), "email", conf, evidence, at, now)
    stage_note = f"stage {proj['stage']}" + (" (HELD: unparseable event lines)" if proj.get("held") else "")
    return Reconciled(mid, "attached", f"{typ} ({stage_note})", slug, row, extra)


def listed_for_review(root: Path) -> str:
    """The text of every state/review/mail-*.md (a held message listed on any day is listed already)."""
    folder = Path(root) / "state" / "review"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("mail-*.md"))) if folder.is_dir() else ""


def reconcile(root: Path, now: datetime, day: date, rounds: dict[str, str] | None = None,
              dry_run: bool = False, only: set[str] | None = None,
              skip_listed: bool = False) -> tuple[list[Reconciled], Path | None]:
    """only: reconcile just these message ids (mail_intake.py's window); skip_listed: a held row already listed in
    any state/review/mail-*.md is not listed again (an hourly caller lists each held message once)."""
    ledger = Path(root) / MAIL_LEDGER
    if not ledger.is_file():
        raise TrackerError(f"no {MAIL_LEDGER} under {root}: nothing to reconcile, and absent is not empty "
                           "(has mail-triage run?)")
    rows = read_jsonl(ledger)
    latest = [r for r in _latest_rows(rows) if only is None or str(r.get("message_id") or "").strip() in only]
    out = [reconcile_row(root, row, now, rounds or {}, dry_run, rows) for row in latest]
    held = [(r.row, r.reason) for r in out if r.status == "held" and r.row is not None]
    if skip_listed and held:
        listed = listed_for_review(root)
        held = [(row, why) for row, why in held if not _id_re(str(row.get("message_id"))).search(listed)]
    review = None
    if held and not dry_run:
        review, _ = _write_review(root, day, held)
    return out, review


# ------------------------------------------------------------------------------ CLI
def wall_clock() -> datetime:
    return datetime.now(timezone.utc)


def run_now(today: date | None, now: datetime | None = None) -> datetime:
    """The instant a run computes as of: `now` (tests) or the wall clock, unless --today names another day than
    the department's day (js_common.dept_day), in which case 12:00 UTC of that day."""
    current = now or wall_clock()
    if today is None or today == dept_day(current):
        return current
    return datetime.combine(today, time(12, 0), tzinfo=timezone.utc)


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    dated = argparse.ArgumentParser(add_help=False)
    dated.add_argument("--today", type=cli_date, default=argparse.SUPPRESS,
                       help="run date YYYY-MM-DD for ghost flags, metrics and dates (default: the system date)")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[dated])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    nw = sub.add_parser("new", help="create an application folder from a job id", parents=[dated])
    nw.add_argument("--job", required=True)
    nw.add_argument("--discovered-at", default=None, metavar="ISO",
                    help="stated discovery time for a backlog record (ISO date or timestamp, not after the run "
                         "clock); default: the run clock")
    nw.add_argument("--dry-run", action="store_true")
    evp = sub.add_parser("event", help="append an event and re-project the stage", parents=[dated])
    evp.add_argument("slug")
    evp.add_argument("type", help="e.g. applied, confirmation_received, screen, interview, rejected, override:screen")
    evp.add_argument("--source", default="manual", help="email | manual | calendar | linkedin")
    evp.add_argument("--conf", type=float, default=1.0)
    evp.add_argument("--evidence", default="")
    evp.add_argument("--at", default=None,
                     help="ISO timestamp of the event (default: now, or --today at 12:00 UTC); for a manual applied "
                          "event, the stated submission time (without it: a warning and 'time: clock')")
    evp.add_argument("--via", default=None, choices=APPLIED_VIA, help="how it was applied (sets applied_via)")
    evp.add_argument("--round-at", default=None, metavar="ISO",
                     help="for a round event: when the round itself happens (date and time), not when it was booked")
    evp.add_argument("--reschedule", action="store_true",
                     help="for a round event: this moves an already booked round (it is not a new round)")
    evp.add_argument("--resume", default=None, metavar="PDF|SHA256|none",
                     help="required on applied: the PDF that went (a path under resumes/, or its sha256), none "
                          "(with --reason), or same (a repeat applied event keeps the recorded page)")
    evp.add_argument("--reason", default=None, help="with --resume none: why no PDF went")
    evp.add_argument("--dry-run", action="store_true")
    evp.add_argument("--force", action="store_true",
                     help="move the stage backwards even while unparseable event lines exist")
    evp.add_argument("--override", metavar="WORDS", default=None,
                     help="the owner's words and date; required to log `ghosted` (ghosting is otherwise inferred by "
                          "`project`, never logged) and recorded in the evidence")
    pj = sub.add_parser("project", help="re-project every application", parents=[dated])
    pj.add_argument("--dry-run", action="store_true")
    pj.add_argument("--force", action="store_true",
                    help="move stages backwards even while unparseable event lines exist")
    mg = sub.add_parser("merge", parents=[dated], help="move one record into the other folder of the same job")
    mg.add_argument("src", metavar="FROM")
    mg.add_argument("dst", metavar="INTO")
    mg.add_argument("--dry-run", action="store_true")
    tp = sub.add_parser("tier", parents=[dated], help="record an application's tier and why")
    tp.add_argument("slug")
    tp.add_argument("tier", nargs="?", choices=APP_TIERS)
    tp.add_argument("--source", default=None, help="why this tier (the owner's word, the page's depth, the rule)")
    tp.add_argument("--infer", action="store_true",
                    help="compute the tier from fit.md, the dream board and prospects/ and record it with why")
    tp.add_argument("--dry-run", action="store_true")
    pp = sub.add_parser("path", parents=[dated],
                        help="record whether a person carried an application or it went cold")
    pp.add_argument("slug")
    pp.add_argument("--path", required=True, choices=APP_PATHS)
    pp.add_argument("--via", default=None, metavar="PROSPECT_ID",
                    help="with --path person: the prospect who carried it (prospects/<id>.md must exist)")
    pp.add_argument("--source", default=None, help="the evidence for this path (a file path, an event, a ruling)")
    pp.add_argument("--dry-run", action="store_true")
    tg = sub.add_parser("tag-package-only", parents=[dated],
                        help="list application folders with no application.md in state/package-only.yaml")
    tg.add_argument("--dry-run", action="store_true")
    mt = sub.add_parser("metrics", help="funnel and response metrics", parents=[dated])
    mt.add_argument("--json", action="store_true")
    rc = sub.add_parser("reconcile", parents=[dated],
                        help="attach state/mail-events.jsonl rows on a confirmed match; hold the rest for review")
    rc.add_argument("--dry-run", action="store_true")
    rc.add_argument("--round", action="append", default=[], metavar="MESSAGE_ID=STAGE",
                    help="the round of an interview_invite the evidence does not name: screen, interviewing or final")
    args = ap.parse_args(argv)
    now = run_now(getattr(args, "today", None), now)
    if args.cmd == "new":
        try:
            skipped: list[str] = []
            slug, created = new_application(args.root, args.job, now, dry_run=args.dry_run,
                                            discovered_at=args.discovered_at, skipped=skipped)
        except TrackerError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        verb = ("would create" if args.dry_run else "created") if created else "exists"
        print(f"application {verb}: {slug}")
        for msg in skipped:                                  # F7: a broken package folder, named, never a crash
            print(msg)
        print_warnings(event_warnings(args.root, slug))
        return 0
    if args.cmd == "event":
        try:
            applied = EVENT_STAGE.get(args.type.strip().lower()) == "applied"
            if args.resume is not None and not applied:
                raise TrackerError("refused: --resume is for applied events; nothing was written")
            if applied and str(args.resume or "").strip().lower() == "same":
                resume = recorded_resume(args.root, args.slug)
            else:
                resume = resolve_resume(args.root, args.resume, args.reason) if applied else None
            evidence = round_evidence(args.type, args.evidence, args.round_at, args.reschedule)
            proj = append_event(args.root, args.slug, args.type, args.source, args.conf, evidence, args.at, now,
                                dry_run=args.dry_run, applied_via=args.via, force=args.force, override=args.override,
                                resume=resume)
        except TrackerError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if proj.get("would_adopt"):
            print(f"{args.slug}: would adopt the package-only folder (write application.md and outcome.md there), "
                  f"then log {args.type} (dry-run, nothing written)")
            return 0
        print_warnings(proj["warnings"])
        if proj["held"]:
            print(_held_message([(args.slug, proj)]))
            return 3
        if args.dry_run and resume:
            print(f"{args.slug}: would record {resume['note']}; resume_variant {resume.get('resume_variant')}")
        print(f"{args.slug}: stage {proj['stage']}{' (dry-run)' if args.dry_run else ''}")
        return 0
    if args.cmd == "project":
        n, held, ghosts, due = 0, [], 0, []
        print(ghost_thresholds_line())
        for slug, _props, _body in list(iter_applications(args.root) or []):
            proj = refresh(args.root, slug, now, dry_run=args.dry_run, force=args.force)
            n += 1
            print_warnings(proj["warnings"])
            if proj["held"]:
                held.append((slug, proj))
                print(f"{slug}: HELD at {proj['held']['from']} (projection {proj['held']['to']})")
            elif proj["ghosted"]:
                ghosts += 1
                print(f"{slug}: {ghost_flag(proj, now)}")
            else:
                print(f"{slug}: {proj['stage']}")
                action, when = next_action_for(proj, now)
                if action == FOLLOW_UP and when and when <= dept_day(now).isoformat():
                    due.append(f"{slug} ({proj['stage']}, due {when})")
        print(f"ghost flags: {ghosts} (inferred by this projection; never log a ghosted event)")
        print("follow-ups due (" + ", ".join(f"+{d}" for d in FOLLOW_UP_OFFSETS)
              + " business days without inbound: find a person, once): " + (", ".join(due) or "none"))
        print(f"projected {n} applications" + (" (dry-run, nothing written)" if args.dry_run else ""))
        if held:
            print(_held_message(held))
            return 3
        return 0
    if args.cmd == "tier":
        why = args.source
        try:
            if args.infer == bool(args.tier):
                raise TrackerError("refused: give a tier with --source, or --infer (not both, not neither)")
            tier = args.tier
            if args.infer:
                import strategy_review                       # run as a script, its `tracker` is another module:
                try:                                         # re-raise its TrackerError as this one
                    tier, why = strategy_review.infer_application_tier(args.root, args.slug)
                except strategy_review.tr.TrackerError as exc:
                    raise TrackerError(str(exc)) from None
            out = set_tier(args.root, args.slug, tier, why, now, dry_run=args.dry_run)
        except TrackerError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"{out['application']}: tier {out['from']} -> {out['to']}"
              + (" (dry-run, nothing written)" if args.dry_run else ""))
        if args.infer:
            print(f"why: {why}")
        return 0
    if args.cmd == "path":
        try:
            out = set_path(args.root, args.slug, args.path, args.via, args.source, now, dry_run=args.dry_run)
        except TrackerError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not out["changed"]:
            print(f"{out['application']}: path {out['to']} unchanged (same via_prospect and path_source; nothing written)")
            return 0
        print(f"{out['application']}: path {out['from']} -> {out['to']}"
              + (" (dry-run, nothing written)" if args.dry_run else ""))
        for key, (old, new) in out["changed"].items():
            print(f"  {key}: {old!r} -> {new!r}")
        return 0
    if args.cmd == "merge":
        try:
            plan = merge_plan(args.root, args.src, args.dst) if args.dry_run else merge(args.root, args.src, args.dst, now)
        except TrackerError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        verb = "would" if args.dry_run else "did"
        what = (f"move the record (application.md{' and outcome.md' if plan['outcome_copied'] else ''}, id -> {plan['into']})"
                if plan["mode"] == "move" else f"append {len(plan['events'])} missing event(s)")
        print(f"merge {plan['from']} -> {plan['into']} (job {plan['job']}): {verb} {what}")
        for line in plan["events"]:
            print(f"  {line}")
        print(f"  job link: {'would be' if args.dry_run else 'now'} [[{plan['into']}]]; {plan['into']} leaves "
              f"state/package-only.yaml; stage re-projected")
        print(f"  applications/{plan['from']}/ left to be removed by hand ({', '.join(plan['left'])}); nothing deleted"
              + (" (dry-run, nothing written)" if args.dry_run else ""))
        return 0
    if args.cmd == "tag-package-only":
        res = tag_package_only(args.root, dry_run=args.dry_run)
        verb = "would tag" if args.dry_run else "tagged"
        print(f"{verb} {len(res['tagged'])} package-only folder(s)" + (": " + ", ".join(res["tagged"]) if res["tagged"] else ""))
        if res["dropped"]:
            print(f"{'would drop' if args.dry_run else 'dropped'} {len(res['dropped'])} slug(s) now with application.md: "
                  + ", ".join(res["dropped"]))
        if res["legacy"]:
            print(f"legacy KIND marker(s): {len(res['legacy'])} {'to fold' if args.dry_run else 'folded'} into "
                  f"{PACKAGE_ONLY_STATE} (the KIND files stay; they may be deleted by the owner)")
        if res["stale"]:
            print(f"stale package-only marker(s), application.md wins (remove KIND by hand): {', '.join(res['stale'])}")
        print(f"folders {res['folders']}: applications {res['applications']}, package-only {res['package_only']}, "
              f"untagged {res['untagged']}" + (" (dry-run, nothing written)" if args.dry_run else ""))
        return 0
    if args.cmd == "reconcile":
        rounds: dict[str, str] = {}
        for item in args.round:
            mid, _, stage = item.partition("=")
            if stage not in ROUNDS or not mid.strip():
                ap.error(f"--round {item!r}: expected <message_id>=screen|interviewing|final")
            rounds[mid.strip()] = stage
        try:
            results, review = reconcile(args.root, now, dept_day(now), rounds, dry_run=args.dry_run)
        except TrackerError as exc:
            print(str(exc))
            return 1
        for r in results:
            print(r.line(args.dry_run))
            for extra in r.lines or []:
                print(f"  {extra}")
        held = [r for r in results if r.status == "held"]
        if review is not None:
            print(f"review file: {_rel(args.root, review)} ({len(held)} held row(s) listed, decision: blank)")
        elif held:
            print(f"review file: none written (dry-run); {len(held)} held row(s)")
        counts = {k: sum(r.status == k for r in results) for k in ("attached", "held", "skipped", "noop")}
        print(f"attached {counts['attached']}, held {counts['held']}, skipped {counts['skipped']}, "
              f"no event {counts['noop']}" + (" (dry-run, nothing written)" if args.dry_run else ""))
        return 0
    m = metrics(args.root, now)
    print_warnings(m["warnings"])
    if args.json:
        print(json.dumps(m, indent=2, default=str))
    else:
        print(ghost_thresholds_line())
        print(f"applications: {m['applications']}")
        print(f"folders: {m['folders']} (applications {m['applications']}, package-only {m['package_only']}, "
              f"untagged {m['untagged']})")
        print("funnel: " + ", ".join(f"{k} {v}" for k, v in m["funnel"].items()))
        print("outcomes: " + ", ".join(f"{k} {v}" for k, v in m["outcomes"].items()))
        for label, key in (("by source", "response_rate_by_source"), ("by applied_via", "response_rate_by_applied_via")):
            print(f"response rate {label}: " + (", ".join(f"{k} {v['responded']}/{v['applied']}" for k, v in m[key].items()) or "no data yet"))
        order = [*APP_PATHS, "missing"]
        paths = sorted(m["by_path"].items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else len(order), kv[0]))
        print("by path: " + (", ".join(f"{k} {v['responded']}/{v['applied']} responded ({v['screen']} screens)"
                                       for k, v in paths) or "no data yet"))
        missing = m["by_path"].get("missing", {}).get("applied", 0)
        if missing:
            print(f"path missing: {missing} applied applications (tracker.py path <slug> --path person|cold|unknown "
                  f"[--via <prospect id>] --source \"<evidence>\")")
        print(f"median days in stage: {m['median_days_in_stage'] or 'no data yet'}")
        lat = m["apply_latency_hours"]
        print(f"apply latency: median {lat['median']} h, {lat['within_72h']}/{lat['measured']} within 72 h")
        iv = m["interviews"]
        print(f"interview rounds: booked {iv['booked']}, held {iv['held']}, upcoming {iv['upcoming']}, "
              f"unconfirmed {iv['unconfirmed']} (time passed, no held record), ended unheld {iv['ended']}")
    return 0


def _rel(root: Path, path: Path) -> str:
    try:
        return str(Path(path).relative_to(Path(root)))
    except ValueError:
        return str(path)


def _held_message(held: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [f"refused to move {len(held)} stage(s) backwards while event lines are unparseable:"]
    for slug, proj in held:
        lines.append(f"  {slug}: {proj['held']['from']} -> {proj['held']['to']} (stage on file kept)")
        lines.extend(f"    {w['where']}: {w['line']}" for w in proj["warnings"])
    lines.append("fix those lines to '- <ISO> · <type> · <source> · conf=<x> · <evidence>', or rerun with --force")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
