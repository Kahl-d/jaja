#!/usr/bin/env python3
"""watchdog.py - catch routines that went silent.

Every routine spec (routines/*.yaml in the code root; fixtures, dot and underscore files skipped, as in the
sweep) is checked. All shipped routines are disabled, so a fresh install reports every routine OFF. For an enabled one,
the newest receipt in <runtime>/logs/runs/<name>/ is compared with its cron schedule (local time). The three roots
are scripts/kernel/roots.py: specs come from the code root, receipts, alerts and the state file from the runtime
root; `--root R` treats one folder as all three.

    OK      the next scheduled run after the last receipt is not overdue yet
    LATE    one scheduled run has passed (plus 15 minutes of grace) without a receipt
    SILENT  two scheduled runs have passed (plus grace) without a receipt. For a regular schedule
            that is 2x its interval; nights and weekends the schedule skips do not count.
    ERROR   enabled but unwatchable: an unreadable spec, a name that is not a slug, a bad schedule; or a run
            that wrote `· started` (with `- run_id:`) and no verdict with that run_id within 45 minutes (its
            `- budget_min:` + 25 when noted): killed or hung. A started line never counts as a fresh receipt.
    OFF     not enabled (enabled: false or absent): ignored; a non-boolean `enabled` is an ERROR

A routine is measured from its newest receipt, or from the end of its last_reviewed day when that
is later (a fresh review or install restarts the clock). An enabled routine with neither is
SILENT: nothing shows it ever ran.

Receipts are <runtime>/logs/runs/<name>/<YYYY-MM-DD>.md, one file per day, append-only, one section
per run headed `## Run <ISO timestamp> · <status>` (write one with `watchdog.py receipt`). The
newest heading timestamp in the newest file counts; a file without one counts at its
modification time, clamped to the day in its name.

Writes
    <runtime>/registry/watchdog.json         the state (for the briefing) plus the alert ledger
    <runtime>/work/inbox/<date>-watchdog-<name>.md   one alert per SILENT or ERROR routine per day, never
                                             overwritten; the ledger keeps it to one a day even after
                                             triage moves the item
    --no-inbox is report only: it prints the report and writes none of these (no state file, no alert,
    no receipt; --receipt with it is refused).
    <runtime>/logs/runs/watchdog/<date>.md   only with --receipt (the watchdog routine's own receipt)

The sweep has no hook for this state (its routine rows carry only the last receipt date), so the
nightly sweep (scripts/kernel/nightly_sweep.py) runs the watchdog before the sweep and the briefing
counts the alerts in its inbox line.

Exit 1 when an enabled routine is SILENT or ERROR, else 0.

CLI
    python3 scripts/kernel/watchdog.py [--today D | --now ISO] [--json] [--no-inbox] [--receipt] [--root R | --code P --runtime P]
    python3 scripts/kernel/watchdog.py receipt <routine> --status ok|warn|failed
            [--sentinel NO_ACTION|NEEDS_YOU] [--note TEXT]... [--now ISO] [--root R]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontmatter as fm  # noqa: E402
from roots import Roots, add_root_args, from_args, roots as as_roots  # noqa: E402

try:                                  # the configured names a routine's `owner:` may carry for the owner themself
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import agency_config as _cfg  # noqa: E402
    OWNER_HANDLES = tuple(str(h) for h in (_cfg.get("identity.owner_handles", ["owner"]) or ["owner"]))
except Exception:  # noqa: BLE001  (a standalone copy without the config module)
    OWNER_HANDLES = ("owner",)

SELF = "scripts/kernel/watchdog.py"
STATE_PATH = "registry/watchdog.json"        # under the runtime root
RUNS = "logs/runs"                            # under the runtime root
GRACE = dt.timedelta(minutes=15)
LEDGER_DAYS = 7
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
RECEIPT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[-_.][^/]*)?\.md")
STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?")
SEVERITY = {"OK": 0, "LATE": 1, "SILENT": 2, "ERROR": 3}
STATUSES = ("ok", "warn", "failed", "started")
# A run writes "started" first and its verdict last, both with "- run_id: <id>". A started run with no
# end after STARTED_GRACE_MIN (or its noted budget_min + STARTED_BUDGET_MARGIN_MIN) was killed or hung: ERROR.
STARTED_GRACE_MIN = 45
STARTED_BUDGET_MARGIN_MIN = 25
STARTED_HEADING = re.compile(r"·\s*started\s*$")
SENTINELS = ("NO_ACTION", "NEEDS_YOU")
REFERENCE = dt.datetime(2026, 1, 1)  # fixed start for interval sampling (replayable)


# --------------------------------------------------------------------------- #
# cron (5 fields, local time; *, a-b, */n, a-b/n, a/n, lists, month and day names, 7 = Sunday, @macros)
# --------------------------------------------------------------------------- #
MONTH_NAMES = {n: i for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
DAY_NAMES = {n: i for i, n in enumerate("sun mon tue wed thu fri sat".split())}
MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
          "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day of month", 1, 31, {}), ("month", 1, 12, MONTH_NAMES),
          ("day of week", 0, 7, DAY_NAMES))


def _num(token: str, name: str, names: dict[str, int]) -> int:
    if token.isdigit():
        return int(token)
    if token.lower() in names:
        return names[token.lower()]
    raise ValueError(f"{name}: {token!r} is not a number" + (" or a name" if names else ""))


def _field(text: str, name: str, lo: int, hi: int, names: dict[str, int]) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        if not base or (slash and not step_text.isdigit()) or (slash and int(step_text) == 0):
            raise ValueError(f"{name}: {part!r} is not a valid entry")
        step = int(step_text) if slash else 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _num(a, name, names), _num(b, name, names)
        else:
            start = _num(base, name, names)
            end = hi if slash else start
        if not (lo <= start <= hi and lo <= end <= hi):
            raise ValueError(f"{name}: {part!r} is outside {lo}-{hi}")
        if start > end:
            raise ValueError(f"{name}: {part!r} is a reversed range")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class Cron:
    expr: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    dom_star: bool
    dow_star: bool

    def _day_ok(self, day: dt.date) -> bool:
        dom = day.day in self.days
        dow = day.isoweekday() % 7 in self.weekdays
        if self.dom_star and self.dow_star:
            return True
        if self.dom_star:
            return dow
        if self.dow_star:
            return dom
        return dom or dow  # both restricted: either one matches (cron semantics)

    def next_fire(self, after: dt.datetime) -> dt.datetime:
        """The first scheduled minute strictly after `after` (naive local time)."""
        t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = t + dt.timedelta(days=366 * 5)
        while t <= limit:
            if t.month not in self.months:
                t = dt.datetime(t.year + (t.month == 12), t.month % 12 + 1, 1)
                continue
            if not self._day_ok(t.date()):
                t = dt.datetime(t.year, t.month, t.day) + dt.timedelta(days=1)
                continue
            if t.hour not in self.hours:
                later = [h for h in self.hours if h > t.hour]
                t = t.replace(hour=later[0], minute=0) if later else \
                    dt.datetime(t.year, t.month, t.day) + dt.timedelta(days=1)
                continue
            if t.minute not in self.minutes:
                later = [m for m in self.minutes if m > t.minute]
                t = t.replace(minute=later[0]) if later else t.replace(minute=0) + dt.timedelta(hours=1)
                continue
            return t
        raise ValueError(f"schedule {self.expr!r} never fires")

    def interval(self) -> dt.timedelta:
        """The usual gap between runs: the most common one over 60 runs (ties: the shorter)."""
        fires, t = [], REFERENCE
        for _ in range(60):
            t = self.next_fire(t)
            fires.append(t)
        gaps = collections.Counter(b - a for a, b in zip(fires, fires[1:]))
        return max(gaps.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def parse_cron(expr: Any) -> Cron:
    text = " ".join(str(expr if expr is not None else "").split())
    if text.startswith("@"):
        if text.lower() not in MACROS:
            raise ValueError(f"unknown schedule macro {text!r}")
        text = MACROS[text.lower()]
    parts = text.split(" ") if text else []
    if len(parts) != 5:
        raise ValueError(f"schedule {expr!r} needs 5 fields: minute hour day-of-month month day-of-week")
    sets = [_field(p, name, lo, hi, names) for p, (name, lo, hi, names) in zip(parts, FIELDS)]
    weekdays = frozenset(0 if d == 7 else d for d in sets[4])
    cron = Cron(text, tuple(sorted(sets[0])), tuple(sorted(sets[1])), frozenset(sets[2]), frozenset(sets[3]), weekdays,
                parts[2].startswith("*"), parts[4].startswith("*"))
    cron.next_fire(REFERENCE)  # raises when it never fires (e.g. 31 February)
    return cron


def humanize(delta: dt.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"
    days = delta.days
    if 28 <= days <= 31:
        return "1mo"
    return f"{days}d"


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def to_local(stamp: dt.datetime) -> dt.datetime:
    """Naive local wall time (aware stamps are converted; naive ones are already local)."""
    return stamp.astimezone().replace(tzinfo=None) if stamp.tzinfo else stamp


def parse_stamp(text: str) -> dt.datetime | None:
    m = STAMP_RE.search(text)
    if not m:
        return None
    day, hh, mm, ss, tz = m.groups()
    iso = f"{day}T{hh}:{mm}:{ss or '00'}"
    if tz:
        iso += "+00:00" if tz == "Z" else (tz if ":" in tz else f"{tz[:3]}:{tz[3:]}")
    try:
        return to_local(dt.datetime.fromisoformat(iso))
    except ValueError:
        return None


def as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10]) if value not in (None, "") else None
    except ValueError:
        return None


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------- #
# specs and receipts
# --------------------------------------------------------------------------- #
def load_routines(root: Any = None) -> list[dict[str, Any]]:
    """Every routine spec as {"path", "meta"} (meta {"_error": ...} when unreadable), sweep's selection."""
    root = as_roots(root).code
    paths = sorted((root / "routines").glob("*.yaml")) if (root / "routines").is_dir() else []
    out = []
    for path in paths:
        if "fixtures" in path.relative_to(root).parts or path.name.startswith((".", "_")):
            continue
        try:
            data = fm.parse_yaml(path.read_text(encoding="utf-8"))
        except (fm.FrontmatterError, UnicodeDecodeError, OSError) as exc:
            data = {"_error": str(exc)}
        out.append({"path": rel(root, path), "meta": data if isinstance(data, dict) else {"_error": "not a mapping"}})
    return out


def _started_only(path: Path | None) -> bool:
    """True when a receipt file holds a started run heading and no other run heading: a run began, nothing finished."""
    if path is None:
        return False
    try:
        heads = [ln for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("## Run")]
    except OSError:
        return False
    return bool(heads) and all(STARTED_HEADING.search(h) for h in heads)


def latest_receipt(root: Any, name: str) -> tuple[dt.datetime | None, Path | None, str | None]:
    """(time, file, how) of the newest receipt for a routine; how is 'heading' or 'mtime'."""
    folder = as_roots(root).runtime / RUNS / name
    if not NAME_RE.fullmatch(name) or not folder.is_dir():
        return None, None, None
    dated: list[tuple[dt.date, Path]] = []
    for path in folder.iterdir():
        m = RECEIPT_RE.fullmatch(path.name)
        if not m or not path.is_file():
            continue
        day = as_date(m.group(1))
        if day:
            dated.append((day, path))
    if not dated:
        return None, None, None
    newest = max(day for day, _ in dated)
    best: tuple[dt.datetime, Path, str] | None = None
    for day, path in sorted(dated):
        if day != newest:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        stamps = [s for s in (parse_stamp(line) for line in lines
                              if line.startswith("#") and not STARTED_HEADING.search(line)) if s]
        if stamps:
            candidate = (max(stamps), path, "heading")
        else:
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
            low, high = dt.datetime.combine(day, dt.time.min), dt.datetime.combine(day, dt.time(23, 59, 59))
            candidate = (min(max(mtime, low), high), path, "mtime")
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best if best else (None, None, None)


def write_receipt(root: Any, routine: str, now: dt.datetime, status: str = "ok",
                  notes: Iterable[str] = (), sentinel: str | None = None, day: dt.date | None = None) -> Path:
    """Append one run section to <runtime>/logs/runs/<routine>/<date>.md (created with its header).

    The file is `now`'s local day unless `day` names the run's day (a replayed run); the heading
    always carries `now`."""
    if not NAME_RE.fullmatch(str(routine)):
        raise ValueError(f"routine name {routine!r} is not a slug (a-z, 0-9, hyphens)")
    if status not in STATUSES:
        raise ValueError(f"status {status!r} not in {STATUSES}")
    if sentinel is not None and sentinel not in SENTINELS:
        raise ValueError(f"sentinel {sentinel!r} not in {SENTINELS}")
    local = to_local(now)
    day = (day or local.date()).isoformat()
    folder = as_roots(root).runtime / RUNS / routine
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.md"
    section = f"## Run {local.astimezone().isoformat(timespec='seconds')} · {status}\n"
    if sentinel:
        section += f"- sentinel: {sentinel}\n"
    section += "".join(f"- {' '.join(str(n).split())}\n" for n in notes if str(n).strip())
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(f"---\nroutine: {routine}\ndate: {day}\n---\n# {routine} receipts {day}\n\n{section}")
    except FileExistsError:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n" + section)
    return path


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
def _assess(root: Roots, doc: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    meta, path = doc["meta"], doc["path"]
    row: dict[str, Any] = {
        "name": Path(path).stem, "path": path, "enabled": None, "owner": None, "runs_on": None, "schedule": None,
        "interval": None, "state": "ERROR", "last_receipt": None, "receipt": None, "measured_from": None,
        "next_due": None, "silent_after": None, "reason": "",
    }
    if "_error" in meta:
        row["reason"] = f"unreadable spec: {meta['_error']}"
        return row
    enabled = meta.get("enabled")
    row.update(name=str(meta.get("name") or Path(path).stem), enabled=enabled is True,
               owner=meta.get("owner"), runs_on=meta.get("runs_on"), schedule=meta.get("schedule"))
    if enabled is not None and not isinstance(enabled, bool):
        row.update(enabled=None, reason=f"enabled must be true or false, got {enabled!r}: cannot tell whether to watch it")
        return row
    name = row["name"]
    if NAME_RE.fullmatch(name):
        last, receipt, how = latest_receipt(root, name)
        row["last_receipt"], row["receipt"] = iso(last), rel(root.runtime, receipt) if receipt else None
    else:
        last, how = None, None
    try:
        cron = parse_cron(row["schedule"])
        row["interval"] = humanize(cron.interval())
    except ValueError as exc:
        cron, schedule_error = None, str(exc)
    if not row["enabled"]:
        row.update(state="OFF", reason="not enabled")
        return row
    if not NAME_RE.fullmatch(name):
        row["reason"] = f"name {name!r} is not a slug; its receipts cannot be located safely"
        return row
    if cron is None:
        row["reason"] = f"schedule: {schedule_error}"
        return row
    # a hung run before the newest finished run is history: the routine ran fine since
    done = newest_verdict(root, name)
    hung = [r for r in open_runs(root, name, now) if r["overdue"] and (done is None or r["at"] is None or r["at"] >= done)]
    if hung:
        r = hung[-1]
        row.update(state="ERROR", reason=f"run {r['run_id']} started at {iso(r['at'])}, no end after {r['grace_min']:g} min: "
                                         "killed or hung (the started line is in the receipt; no verdict followed)")
        return row
    ran = done is not None or (last is not None and not _started_only(receipt))   # free-form receipts count as a run
    reviewed = as_date(meta.get("last_reviewed"))
    if reviewed and reviewed > now.date():                 # a future date would hide a stopped routine
        reviewed = None
    review_start = dt.datetime.combine(reviewed + dt.timedelta(days=1), dt.time.min) if reviewed else None
    # `installed:` (when the scheduled task was last installed) restarts the clock like a review; a
    # routine reinstalled after its review is late only after its first slot since the install. A future value is
    # ignored (lint names it), so it can never turn SILENT into OK
    installed = _as_datetime(meta.get("installed"))
    if installed and installed > now:
        installed = None
    # A routine that never ran (no receipt with a verdict, ever) is measured from its review, not a reinstall: a
    # routine recreated after its first slots would otherwise read OK for a month while nothing had ever run.
    # A reinstall restarts the clock only after a first run.
    if installed and ran and (review_start is None or installed > review_start):
        review_start, source_review = installed, f"installed {installed.isoformat(timespec='minutes')}"
    else:
        source_review = f"end of last_reviewed {reviewed.isoformat()}" if reviewed else ""
    if last and ran and (review_start is None or last >= review_start):   # a started line alone is no run
        anchor, source = last, f"receipt {row['receipt']} ({how})"
    elif review_start:
        anchor, source = review_start, source_review
    else:
        row.update(state="SILENT", reason=f"enabled, but no receipt in {RUNS}/{name}/ and no last_reviewed date: "
                                          "nothing shows it ever ran")
        return row
    first = cron.next_fire(anchor)
    second = cron.next_fire(first)
    row.update(measured_from=iso(anchor), next_due=iso(first), silent_after=iso(second + GRACE))
    if not ran and now > first + GRACE:
        row.update(state="SILENT", reason=f"never ran: no receipt with a verdict in {RUNS}/{name}/, and the run due "
                                          f"{iso(first)} (measured from {source}) passed; a reinstall does not restart "
                                          "the clock of a routine that never ran")
    elif now > second + GRACE:
        row.update(state="SILENT", reason=f"measured from {source} at {iso(anchor)}: the runs due {iso(first)} "
                                          f"and {iso(second)} both passed without a receipt")
    elif now > first + GRACE:
        row.update(state="LATE", reason=f"measured from {source}: the run due {iso(first)} has no receipt yet")
    else:
        row.update(state="OK", reason=f"measured from {source}: next run due {iso(first)}")
    return row


def newest_verdict(root: Any, name: str) -> dt.datetime | None:
    """The time of the newest run section with a verdict (any status but `started`) in the two newest receipt files;
    never a file's modification time (a file holding only a started line must not look like a finished run)."""
    folder = as_roots(root).runtime / RUNS / name
    if not NAME_RE.fullmatch(name) or not folder.is_dir():
        return None
    best = None
    for path in sorted((p for p in folder.iterdir() if RECEIPT_RE.fullmatch(p.name) and p.is_file()), key=lambda p: p.name)[-2:]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for head in re.findall(r"(?m)^## Run .*$", text):
            if STARTED_HEADING.search(head):
                continue
            at = parse_stamp(head)
            if at and (best is None or at > best):
                best = at
    return best


def open_runs(root: Any, name: str, now: dt.datetime) -> list[dict[str, Any]]:
    """Started runs with no end carrying their run_id, from the two newest receipt files, each with its grace in
    minutes and whether that grace has passed at `now`."""
    folder = as_roots(root).runtime / RUNS / name
    if not NAME_RE.fullmatch(name) or not folder.is_dir():
        return []
    files = sorted((p for p in folder.iterdir() if RECEIPT_RE.fullmatch(p.name) and p.is_file()), key=lambda p: p.name)[-2:]
    started: dict[str, dict[str, Any]] = {}
    ended: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for section in re.split(r"(?m)^(?=## Run )", text):
            head = section.splitlines()[0] if section.startswith("## Run ") else ""
            m = re.search(r"(?m)^- run_id: (\S+)", section)
            if not head or not m:
                continue
            if STARTED_HEADING.search(head):
                b = re.search(r"(?m)^- budget_min: (\d+(?:\.\d+)?)", section)
                grace = float(b.group(1)) + STARTED_BUDGET_MARGIN_MIN if b else STARTED_GRACE_MIN
                started[m.group(1)] = {"run_id": m.group(1), "at": parse_stamp(head), "grace_min": grace}
            else:
                ended.add(m.group(1))
    out = []
    for rid, run in started.items():
        if rid in ended or run["at"] is None:
            continue
        out.append({**run, "overdue": now > run["at"] + dt.timedelta(minutes=run["grace_min"])})
    return out


def _as_datetime(value: Any) -> dt.datetime | None:
    """A spec's `installed:` (a date, or a local date and time) as a naive local datetime; None when absent or unreadable.
    A value with a zone is converted to local time, never read as local wall time."""
    if isinstance(value, dt.datetime):
        return to_local(value)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    try:
        return to_local(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))) if value else None
    except ValueError:
        return None


def evaluate(root: Any, now: dt.datetime) -> dict[str, Any]:
    """Pure read: the state of every routine at `now` (naive local time)."""
    root = as_roots(root)
    now = to_local(now)
    rows = [_assess(root, doc, now) for doc in load_routines(root)]
    watched = [r for r in rows if r["state"] != "OFF"]
    counts = {"enabled": len(watched), "ok": 0, "late": 0, "silent": 0, "error": 0, "off": len(rows) - len(watched)}
    for r in watched:
        counts[r["state"].lower()] += 1
    state = max((r["state"] for r in watched), key=SEVERITY.__getitem__) if watched else "IDLE"
    return {"now": iso(now), "state": state, "counts": counts, "grace_minutes": int(GRACE.total_seconds() // 60),
            "routines": rows}


# --------------------------------------------------------------------------- #
# side effects
# --------------------------------------------------------------------------- #
def _alert_key(row: dict[str, Any]) -> str:
    if NAME_RE.fullmatch(row["name"]):
        return row["name"]
    return re.sub(r"[^a-z0-9]+", "-", Path(row["path"]).stem.lower()).strip("-") or "routine"


def _yaml_str(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if NAME_RE.fullmatch(text) else json.dumps(text, ensure_ascii=False)


def alert_text(row: dict[str, Any], today: dt.date) -> str:
    owner = row.get("owner")
    owner_ref = owner if owner in (None, "") or str(owner).startswith(("role:", *OWNER_HANDLES)) else f"role:{owner}"
    last = (f"{row['last_receipt']} in `{row['receipt']}`" if row["last_receipt"] else
            f"none in `{RUNS}/{row['name'] if NAME_RE.fullmatch(row['name']) else '?'}/`")
    lines = [
        "---", "source: alert", f"received: {today.isoformat()}", "suggested: task",
        f"routine: {_yaml_str(row['name'])}", f"state: {row['state']}", "---",
        f"# watchdog: {row['name']} is {row['state']}", "",
        f"- Spec: `{row['path']}` · owner {owner or '?'} · runs_on {row.get('runs_on') or '?'} · "
        f"schedule `{row.get('schedule')}`" + (f" (every {row['interval']})" if row.get("interval") else ""),
        f"- Last receipt: {last}",
        f"- Finding: {row['reason']}",
    ]
    if row["state"] == "SILENT" and row.get("silent_after"):
        lines.append(f"- Silent since {row['silent_after']}: the second scheduled run after {row['measured_from']} "
                     f"plus {int(GRACE.total_seconds() // 60)} minutes of grace")
    lines += [
        "- Reproduce: `python3 scripts/kernel/watchdog.py --no-inbox`",
        f"- Suggested route: a task for {owner_ref or 'role:ops-watchdog'} (routine owner) or role:ops-watchdog; "
        "the watchdog itself never edits, disables or re-runs a routine.",
        "",
    ]
    return "\n".join(lines)


def _read_ledger(root: Roots) -> dict[str, str]:
    try:
        data = json.loads((root.runtime / STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    ledger = data.get("alerted") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in ledger.items()} if isinstance(ledger, dict) else {}


def check(root: Any, now: dt.datetime, *, today: dt.date | None = None, write_state: bool = True,
          inbox: bool = True, receipt: bool = False) -> dict[str, Any]:
    """evaluate() plus the writes: inbox alerts (deduped per routine per day), the state file, a receipt."""
    root = as_roots(root)
    now = to_local(now)
    today = today or now.date()
    report = evaluate(root, now)
    ledger = {k: v for k, v in _read_ledger(root).items()
              if (as_date(v) or dt.date.min) > today - dt.timedelta(days=LEDGER_DAYS)}
    written: list[str] = []
    if inbox:
        for row in report["routines"]:
            if row["state"] not in ("SILENT", "ERROR"):
                continue
            key = _alert_key(row)
            if ledger.get(key) == today.isoformat():
                continue
            path = root.runtime / "work" / "inbox" / f"{today.isoformat()}-watchdog-{key}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(path, "x", encoding="utf-8") as fh:
                    fh.write(alert_text(row, today))
                written.append(rel(root.runtime, path))
            except FileExistsError:
                pass  # an item for today is already in the inbox
            ledger[key] = today.isoformat()
    report["alerts_written"] = written
    report["alerted"] = dict(sorted(ledger.items()))
    payload = {"_generated": f"GENERATED by {SELF} from routines/, {RUNS} - do not edit",
               "generated_at": iso(now), **report}
    if write_state:  # nightly-sweep and the hourly watchdog both write it: replace atomically
        target = root.runtime / STATE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".watchdog.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    if receipt:
        bad = [r for r in report["routines"] if r["state"] in ("SILENT", "ERROR")]
        notes = [summary(report)] + [f"{r['name']} {r['state']}: {r['reason']}" for r in report["routines"]
                                      if r["state"] in ("SILENT", "ERROR", "LATE")] + [f"alert: {p}" for p in written]
        write_receipt(root, "watchdog", now, status="warn" if bad else "ok", notes=notes,
                      sentinel="NEEDS_YOU" if bad else "NO_ACTION")
    return payload


def summary(report: dict[str, Any]) -> str:
    c = report["counts"]
    return (f"state {report['state']} · {c['enabled']} enabled ({c['ok']} ok, {c['late']} late, {c['silent']} silent, "
            f"{c['error']} error) · {c['off']} off")


def table(report: dict[str, Any]) -> str:
    head = ("ROUTINE", "STATE", "SCHEDULE", "EVERY", "LAST RECEIPT", "SILENT AFTER", "OWNER")
    rows = [(r["name"], r["state"], str(r.get("schedule")), r.get("interval") or "?", r["last_receipt"] or "never",
             r["silent_after"] or "-", str(r.get("owner") or "?")) for r in report["routines"]]
    widths = [max(len(str(x[i])) for x in [head, *rows]) for i in range(len(head))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(line, widths)).rstrip() for line in [head, *rows]]
    lines.append(f"watchdog {report['now']}: {summary(report)}")
    lines += [f"alert written: {p}" for p in report.get("alerts_written", [])]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_now(value: str) -> dt.datetime:
    try:
        return to_local(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--now {value!r} is not an ISO timestamp") from exc


def _check_cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="watchdog.py", description="Report routines that went silent.",
                                 epilog="Also: watchdog.py receipt <routine> --status ok|warn|failed ...")
    add_root_args(ap)
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--today", help="YYYY-MM-DD: now (if today) or 12:00 local on that day (a replay)")
    when.add_argument("--now", type=parse_now, help="ISO timestamp (naive = local time)")
    ap.add_argument("--json", action="store_true", help="print the state JSON instead of the table")
    ap.add_argument("--no-inbox", action="store_true",
                    help="report only: print the report, write nothing (no state file, no inbox alert, no receipt)")
    ap.add_argument("--receipt", action="store_true", help="append the watchdog routine's own receipt")
    args = ap.parse_args(argv)
    if args.now:
        now = args.now
    elif args.today:
        day = as_date(args.today)
        if day is None:
            ap.error(f"--today {args.today!r} is not YYYY-MM-DD")
        now = dt.datetime.now().replace(microsecond=0) if day == dt.date.today() else dt.datetime.combine(day, dt.time(12))
    else:
        now = dt.datetime.now().replace(microsecond=0)
    if args.no_inbox and args.receipt:
        ap.error("--receipt writes a receipt; --no-inbox is report only (pick one)")
    report = check(from_args(args), now, inbox=not args.no_inbox, write_state=not args.no_inbox,
                   receipt=args.receipt)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else table(report))
    return 1 if report["state"] in ("SILENT", "ERROR") else 0


def _receipt_cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="watchdog.py receipt", description="Append a run receipt for a routine.")
    ap.add_argument("routine")
    add_root_args(ap)
    ap.add_argument("--status", required=True, choices=STATUSES)
    ap.add_argument("--sentinel", choices=SENTINELS)
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--now", type=parse_now, help="ISO timestamp (default: now)")
    ap.add_argument("--session", help="the run's Claude Code session id: its measured tokens go on the receipt "
                                      "(scripts/kernel/token_ledger.py)")
    args = ap.parse_args(argv)
    if args.session:
        try:
            import token_ledger
            rows = token_ledger.ledger(token_ledger.project_dir(), args.session)
            tot = {c: sum(v[c] for v in rows.values()) for c in token_ledger.COLS}
            models = sorted({m for (_, _, m) in rows})
            args.note.append(f"tokens: calls {tot['calls']}, input {tot['in']}, cache writes {tot['cw']}, cache reads "
                             f"{tot['cr']}, output {tot['out']} ({', '.join(models) or 'no transcript found'})")
        except Exception as exc:  # noqa: BLE001 - a receipt is never lost to the meter
            args.note.append(f"tokens: not measured ({exc})")
    try:
        path = write_receipt(from_args(args), args.routine, args.now or dt.datetime.now().replace(microsecond=0),
                             status=args.status, notes=args.note, sentinel=args.sentinel)
    except ValueError as exc:
        print(f"watchdog: error: {exc}", file=sys.stderr)
        return 1
    print(rel(from_args(args).runtime, path))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["receipt"]:
        return _receipt_cli(argv[1:])
    return _check_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
