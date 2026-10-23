#!/usr/bin/env python3
"""Per-platform yield and the platform registry check (targets/platforms.yaml; owner platform-scout).

  python3 scripts/platform_yield.py [--root R] [--today YYYY-MM-DD] [--days 30] [--write] [--json]
  python3 scripts/platform_yield.py --check [--root R] [--today YYYY-MM-DD]

Yield, over jobs first seen in the trailing --days window (default 30) before --today:
  fetched     records found on the platform, plus records another source found first that the platform
              also surfaced (`seen_on`)
  new         records found first on the platform that are not duplicates (`duplicate_of` empty)
  queued      of those, fit_verdict apply or priority, status queued / tailoring / applied, or an application
  applied     applications on those records that reached applied (tracker.py projection)
  interviews  applications on those records that reached screen or later
A record's platform is its `platform` field, else its source (greenhouse, lever, ashby, workday -> company-ats;
hn -> hacker-news; adzuna; himalayas; <id>-alert -> <id>). --write rewrites only the `yield:` and
`yield_as_of:` lines of each entry, in place, so comments and hand-written fields survive.

--check validates the registry: required fields, access and status words, adapters that exist, one
platform per alert sender, every ignored sender's kind (job-alert | marketing | noise) with its why and date, an entry for every job_alerts.py parser and every sources.yaml source, and
flags terms and reviews gone stale. `agent_terms` (none | names-agents | gateway-only | bars-bots) is what the terms
say about agents; bars-bots (the terms bar bots or automation on the platform itself)
may stay active or probation, because the channel is the owner's own alert mail and the apply is the owner's click or the
employer's site; an entry whose terms name agents, or whose only agent access is a credentials or auto-apply
gateway, is never probation or active (an error: manual if the owner may read it personally, else blocked). A status change the monthly watch proposed carries
`proposed: {from: <the status before>, date: <watch date>}` until the Recruiter rules at its next Monday review
(the watch day when it is a Monday): a malformed marker is an error, one still there after that Monday a
warning. A proposed retirement (to manual or blocked) of a platform whose `<id>-alert` source is still enabled
in sources.yaml warns until the marker is dropped, so the ask to job-scout to switch it off is never lost.
Exit 1 when there are errors (warnings alone exit 0).

Both modes end with `next monthly watch: YYYY-MM-DD` (`next_watch` in --json): the first WATCH_DAY (the 3rd,
routines/js-platform-watch.yaml `schedule`) after --today, so the watch run itself names the 3rd of next
month (December -> January of the next year). The watch log copies it; no one computes it by hand.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_alerts as ja  # noqa: E402
import tracker  # noqa: E402
from fetch_ats import load_jobs, parse_ts  # noqa: E402
from js_common import CODE_ROOT, JS_ROOT, cli_date, fm, parse_date  # noqa: E402

ACCESS = ("api", "rss", "email-alert", "browser", "manual")
STATUS = ("active", "probation", "manual", "blocked")
AGENT_TERMS = ("none", "names-agents", "gateway-only", "bars-bots")  # none: nothing on agents beyond generic scraping bans
IGNORED_KINDS = ("job-alert", "marketing", "noise")   # targets/platforms.yaml ignored_senders `kind`
CHANNEL_SAFE = ("none", "bars-bots")      # may be probation or active: the channel never automates the platform itself
FIELDS = ("id", "name", "url", "access", "adapter", "alert_senders", "tos", "agent_terms", "sponsorship_filter",
          "account_needed", "alert_setup", "status", "yield", "last_reviewed")
NEVER_PROBATION = "never probation/active: set manual (the owner reads it personally) or blocked"
RETIRED = ("manual", "blocked")
YIELD_KEYS = ("fetched", "new", "queued", "applied", "interviews")
SOURCE_PLATFORM = {"greenhouse": "company-ats", "lever": "company-ats", "ashby": "company-ats",
                   "workday": "company-ats", "jibe": "company-ats", "sitemap": "company-ats",
                   "amazon": "company-ats",
                   "hn": "hacker-news", "adzuna": "adzuna", "himalayas": "himalayas",
                   "aidevjobs": "aidevjobs", "remotive": "remotive", "remoteok": "remoteok",
                   **{d.source: pid for pid, d in ja.DEDICATED.items()}}
QUEUED_STATUS = ("queued", "tailoring", "applied")
REVIEW_EVERY_DAYS = 45
WATCH_DAY = 3             # the monthly platform watch runs on the 3rd (routines/js-platform-watch.yaml)
TERMS_EVERY_DAYS = 180


def next_watch(today: date) -> date:
    """The next monthly watch: the first WATCH_DAY strictly after `today` (on a watch day, next month's)."""
    if today.day < WATCH_DAY:
        return today.replace(day=WATCH_DAY)
    return date(today.year + 1, 1, WATCH_DAY) if today.month == 12 else date(today.year, today.month + 1, WATCH_DAY)


def ratify_by(proposed_on: date) -> date:
    """the Recruiter's next Monday review after a watch: the watch day itself when it is a Monday."""
    return date.fromordinal(proposed_on.toordinal() + (7 - proposed_on.weekday()) % 7)


def proposal_problems(pid: str, entry: dict[str, Any], today: date) -> tuple[list[str], list[str]]:
    """(errors, warnings) for an entry's `proposed:` marker (a status change awaiting the Recruiter)."""
    proposed = entry.get("proposed")
    if proposed is None:
        return [], []
    day = parse_date(proposed.get("date")) if isinstance(proposed, dict) else None    # not "on": YAML reads it as true
    before = proposed.get("from") if isinstance(proposed, dict) else None
    if day is None or before not in STATUS:
        return [f"{pid}: proposed must be {{from: <the status before: {', '.join(STATUS)}>, date: YYYY-MM-DD}}"], []
    if before == entry.get("status"):
        return [f"{pid}: proposed from {before}, but status is still {before}: set the proposed status or drop "
                "the marker"], []
    due = ratify_by(day)
    if today > due:
        return [], [f"{pid}: status {entry.get('status')} proposed {day} (from {before}) was due for the Recruiter's "
                    f"ratification on {due}: record the ruling (drop `proposed:`, or restore {before} on a revert)"]
    return [], []


def platform_of(rec: dict[str, Any]) -> str | None:
    return str(rec.get("platform") or "") or SOURCE_PLATFORM.get(str(rec.get("source") or ""))


def compute(root: Path, today: date, days: int = 30) -> dict[str, dict[str, int]]:
    start = today - timedelta(days=days)
    jobs = load_jobs(root) if (Path(root) / "jobs").is_dir() else {}
    window = {jid: r for jid, r in jobs.items()
              if (seen := parse_ts(r.get("first_seen"))) is not None and start <= seen.date() <= today}
    out: dict[str, dict[str, int]] = {}

    def bump(pid: str | None, key: str) -> None:
        if pid:
            out.setdefault(pid, {k: 0 for k in YIELD_KEYS})[key] += 1

    for rec in window.values():
        own = platform_of(rec)
        bump(own, "fetched")
        for other in rec.get("seen_on") or []:
            if other != own:
                bump(str(other), "fetched")
        if rec.get("duplicate_of"):
            continue
        bump(own, "new")
        if (rec.get("fit_verdict") in ("apply", "priority") or rec.get("status") in QUEUED_STATUS
                or rec.get("application")):
            bump(own, "queued")
    now = datetime.combine(today, time(23, 59, 59), tzinfo=timezone.utc)
    applied_at, screen_at = tracker.PROGRESS.index("applied"), tracker.PROGRESS.index("screen")
    for _slug, props, body in tracker.iter_applications(Path(root)) or []:
        rec = window.get(tracker._link_target(props.get("job")) or "")
        if rec is None:
            continue
        progress = tracker.PROGRESS.index(tracker.project(tracker.parse_events(body), now)["max_progress"])
        if progress >= applied_at:
            bump(platform_of(rec), "applied")
        if progress >= screen_at:
            bump(platform_of(rec), "interviews")
    return out


def write_yields(path: Path, yields: dict[str, dict[str, int]], as_of: date) -> int:
    """Rewrite each platform's `yield:` and `yield_as_of:` lines in place; returns entries touched."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    section, current, touched = None, None, set()
    for i, line in enumerate(lines):
        top = re.match(r"^([A-Za-z_][\w-]*):", line)
        if top:
            section, current = top.group(1), None
            continue
        m = re.match(r"^\s*-\s+id:\s*['\"]?([\w.-]+)", line)
        if m and section == "platforms":
            current = m.group(1)
            continue
        if section != "platforms" or current is None:
            continue
        m = re.match(r"^(\s+)yield:\s*\{[^}\n]*\}(\s+#[^\n]*)?(\n?)$", line)
        if m:
            counts = yields.get(current, {k: 0 for k in YIELD_KEYS})
            body = ", ".join(f"{k}: {counts.get(k, 0)}" for k in YIELD_KEYS)
            lines[i] = f"{m.group(1)}yield: {{{body}}}{m.group(2) or ''}{m.group(3)}"
            touched.add(current)
            continue
        m = re.match(r"^(\s+)yield_as_of:[^\n#]*?(\s+#[^\n]*)?(\n?)$", line)
        if m:
            lines[i] = f"{m.group(1)}yield_as_of: '{as_of.isoformat()}'{m.group(2) or ''}{m.group(3)}"
    path.write_text("".join(lines), encoding="utf-8")
    return len(touched)


def check(root: Path, today: date | None = None) -> tuple[list[str], list[str]]:
    root = Path(root)
    today = today or date.today()
    errors: list[str] = []
    warnings: list[str] = []
    path = ja.platforms_path(root)
    if not path.is_file():
        return [f"{path} is missing"], []
    try:
        entries = (fm.parse_yaml(path.read_text(encoding="utf-8")) or {}).get("platforms") or []
    except Exception as exc:
        return [f"{path}: {exc}"], []
    platforms: dict[str, dict[str, Any]] = {}
    senders: dict[str, str] = {}
    for entry in entries:
        pid = str(entry.get("id") or "?")
        if pid in platforms:
            errors.append(f"{pid}: listed twice")
        platforms[pid] = entry
        missing = [f for f in FIELDS if f not in entry]
        if missing:
            errors.append(f"{pid}: missing {', '.join(missing)}")
        for a in entry.get("access") or []:
            if a not in ACCESS:
                errors.append(f"{pid}: access '{a}' is not one of {', '.join(ACCESS)}")
        if entry.get("status") not in STATUS:
            errors.append(f"{pid}: status '{entry.get('status')}' is not one of {', '.join(STATUS)}")
        terms = entry.get("agent_terms")
        if "agent_terms" in entry and terms not in AGENT_TERMS:
            errors.append(f"{pid}: agent_terms '{terms}' is not one of {', '.join(AGENT_TERMS)}")
        elif terms in AGENT_TERMS and terms not in CHANNEL_SAFE and entry.get("status") in ("probation", "active"):
            errors.append(f"{pid}: agent_terms {terms} with status {entry.get('status')}: {NEVER_PROBATION}")
        tos = entry.get("tos") or {}
        if not isinstance(tos, dict) or not all(tos.get(k) for k in ("quote", "url", "read_on")):
            errors.append(f"{pid}: tos needs quote, url and read_on")
        else:
            read = parse_date(tos.get("read_on"))
            if read and (today - read).days > TERMS_EVERY_DAYS:
                warnings.append(f"{pid}: terms last read {read}; read them again")
            if str(tos.get("verified")).lower() == "no":
                warnings.append(f"{pid}: terms quote not verified on the platform's own page")
        y = entry.get("yield")
        if not isinstance(y, dict) or set(y) != set(YIELD_KEYS):
            errors.append(f"{pid}: yield must be {{{', '.join(YIELD_KEYS)}}}")
        adapter = entry.get("adapter")
        if adapter and not ((CODE_ROOT / str(adapter)).is_file() or (root / str(adapter)).is_file()):
            errors.append(f"{pid}: adapter {adapter} does not exist")
        alert_senders = [str(s).lower() for s in entry.get("alert_senders") or []]
        if (adapter == "scripts/job_alerts.py" or "email-alert" in (entry.get("access") or [])) and not alert_senders:
            errors.append(f"{pid}: an email-alert platform needs alert_senders")
        for s in alert_senders:
            if s in senders and senders[s] != pid:
                errors.append(f"{s} is listed under {senders[s]} and {pid}")
            senders.setdefault(s, pid)
        found = proposal_problems(pid, entry, today)
        errors += found[0]
        warnings += found[1]
        reviewed = parse_date(entry.get("last_reviewed"))
        if reviewed and (today - reviewed).days > REVIEW_EVERY_DAYS:
            warnings.append(f"{pid}: last reviewed {reviewed}; the monthly platform watch is overdue")
    try:
        ignored = (fm.parse_yaml(path.read_text(encoding="utf-8")) or {}).get("ignored_senders") or []
    except Exception:
        ignored = []
    for row in ignored:
        sender = str((row or {}).get("sender") or "?") if isinstance(row, dict) else "?"
        if not isinstance(row, dict) or not all(row.get(k) for k in ("sender", "why", "decided")):
            errors.append(f"ignored_senders {sender}: needs sender, why and decided")
        elif row.get("kind") not in IGNORED_KINDS:
            errors.append(f"ignored_senders {sender}: kind {row.get('kind')!r} is not one of {', '.join(IGNORED_KINDS)}")
    for pid in ja.DEDICATED:
        entry = platforms.get(pid)
        if entry is None:
            errors.append(f"{pid}: job_alerts.py has a parser for it but targets/platforms.yaml has no entry")
        elif entry.get("adapter") != "scripts/job_alerts.py":
            errors.append(f"{pid}: adapter must be scripts/job_alerts.py (it has a dedicated parser)")
    sources_file = root / "sources.yaml"
    if sources_file.is_file():
        sources = (fm.parse_yaml(sources_file.read_text(encoding="utf-8")) or {}).get("sources") or []
        enabled = {str(s.get("id")) for s in sources if s.get("enabled") is not False}
        for pid, entry in platforms.items():
            if (isinstance(entry.get("proposed"), dict) and entry.get("status") in RETIRED
                    and f"{pid}-alert" in enabled):
                warnings.append(f"{pid}: ask job-scout to switch off {pid}-alert after ratification")
        for s in sources:
            sid = str(s.get("id"))
            pid = SOURCE_PLATFORM.get(sid)
            if sid == ja.GENERIC_SOURCE:
                continue
            if pid is None:
                warnings.append(f"sources.yaml {sid}: no platform mapping in platform_yield.SOURCE_PLATFORM")
            elif pid not in platforms:
                errors.append(f"sources.yaml {sid}: its platform {pid} has no entry in targets/platforms.yaml")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date, default=None, help="as-of date YYYY-MM-DD (default: today)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--write", action="store_true", help="write the yields into targets/platforms.yaml")
    ap.add_argument("--check", action="store_true", help="validate targets/platforms.yaml and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = args.today or date.today()
    if args.check:
        errors, warnings = check(args.root, today)
        for e in errors:
            print(f"ERROR {e}")
        for w in warnings:
            print(f"warning {w}")
        print(f"platform check: {len(errors)} errors, {len(warnings)} warnings")
        print(f"next monthly watch: {next_watch(today)}")
        return 1 if errors else 0
    yields = compute(args.root, today, args.days)
    if args.json:
        print(json.dumps({"as_of": today.isoformat(), "days": args.days, "yield": yields,
                          "next_watch": next_watch(today).isoformat()}, indent=2))
    else:
        print(f"platform yield, jobs first seen {today - timedelta(days=args.days)} to {today}")
        print(f"{'platform':18} " + " ".join(f"{k:>10}" for k in YIELD_KEYS))
        for pid, counts in sorted(yields.items(), key=lambda kv: (-kv[1]["new"], kv[0])):
            print(f"{pid:18} " + " ".join(f"{counts[k]:>10}" for k in YIELD_KEYS))
        if not yields:
            print("(no jobs first seen in the window)")
    if args.write:
        path = ja.platforms_path(args.root)
        n = write_yields(path, yields, today)
        print(f"wrote {path} ({n} platforms, as of {today})")
    if not args.json:
        print(f"next monthly watch: {next_watch(today)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
