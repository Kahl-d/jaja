#!/usr/bin/env python3
"""The job-search clock (a fixed number of days from a start date): clock.yaml is the single source of truth; this script reads and sets it.

Day 1 is the day after `employment_end` (the last working day); day `clock_days` is the last
day to have a new job. The clock is a position, never a fraction of work done.

    python3 scripts/clock.py status [--today YYYY-MM-DD] [--json]
    python3 scripts/clock.py set --employment-end YYYY-MM-DD [--confirmed]
    python3 scripts/clock.py set --new-employment-start YYYY-MM-DD|none
    python3 scripts/clock.py set --visa-allowance-days N|none --allowance-source "<the DSO's written answer>"

status  day N of `clock_days` (0 or negative before the start, "ended" after), days left, clock end,
        week (1..13, 0 before the start), the phase from clock.yaml `phases`, unemployment days used
        (from the day after employment_end to today, or to the day before new_employment_start), and
        the visa allowance left when legal-visa has recorded the DSO's written answer.
set     rewrites only the named keys (comments and every other line stay as they are) and bumps
        `updated`. A new --employment-end is provisional unless --confirmed names it; the visa
        allowance is refused without --allowance-source. Exit 2 on bad input; nothing is written.

Importable (dashboard.py): load(root) -> dict | None (None when clock.yaml is absent; ClockError when
it is malformed), status(cfg, today) -> dict (with "line", the one-line clock), render_status(st).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, cli_date, fm  # noqa: E402

CLOCK_FILE = "clock.yaml"
BEFORE = "before the clock starts"


class ClockError(ValueError):
    """clock.yaml is malformed or a requested change is invalid; nothing was written."""


# ------------------------------------------------------------------------------ load
def clock_path(root: Path) -> Path:
    return Path(root) / CLOCK_FILE


def _day(cfg: dict[str, Any], key: str, required: bool) -> date | None:
    value = cfg.get(key)
    if value is None or value == "":
        if required:
            raise ClockError(f"clock.yaml: `{key}` is required (YYYY-MM-DD)")
        return None
    if isinstance(value, date):
        return value
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value).strip()):
            raise ValueError
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ClockError(f"clock.yaml: `{key}: {value}` is not a date (YYYY-MM-DD)") from None


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    """The clock with typed values; ClockError names the first problem."""
    if not isinstance(raw, dict):
        raise ClockError("clock.yaml: the top level must be a mapping")
    cfg: dict[str, Any] = {"employer": raw.get("employer") or None, "basis": str(raw.get("basis") or "").strip()}
    cfg["employment_end"] = _day(raw, "employment_end", True)
    if not isinstance(raw.get("employment_end_confirmed"), bool):
        raise ClockError(f"clock.yaml: `employment_end_confirmed: {raw.get('employment_end_confirmed')}` must be true or false")
    cfg["employment_end_confirmed"] = raw["employment_end_confirmed"]
    days = raw.get("clock_days")
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ClockError(f"clock.yaml: `clock_days: {days}` must be a whole number of days, 1 or more")
    cfg["clock_days"] = days
    if not cfg["basis"]:
        raise ClockError("clock.yaml: `basis` (who set the clock, when, and their words) is required")
    cfg["new_employment_start"] = _day(raw, "new_employment_start", False)
    allowance = raw.get("visa_allowance_days")
    if allowance is not None and (isinstance(allowance, bool) or not isinstance(allowance, int) or allowance < 0):
        raise ClockError(f"clock.yaml: `visa_allowance_days: {allowance}` must be a whole number of days or null")
    source = str(raw.get("visa_allowance_source") or "").strip() or None
    if allowance is not None and not source:
        raise ClockError("clock.yaml: `visa_allowance_days` needs `visa_allowance_source` (the DSO's written answer)")
    cfg["visa_allowance_days"], cfg["visa_allowance_source"] = allowance, source
    cfg["updated"] = _day(raw, "updated", False)
    stem = raw.get("stem_extension")
    cfg["stem_extension"] = _plain(stem) if isinstance(stem, dict) else None
    cfg["phases"] = _phases(raw.get("phases"), math.ceil(days / 7))
    return cfg


def _phases(raw: Any, weeks: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ClockError("clock.yaml: `phases` must list {weeks: [first, last], name} rows")
    owner: dict[int, str] = {}
    out = []
    for row in raw:
        span = row.get("weeks") if isinstance(row, dict) else None
        name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
        if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(w, int) and not isinstance(w, bool)
                                                                     for w in span) or span[0] > span[1] or not name):
            raise ClockError(f"clock.yaml: phase row {row!r} needs weeks: [first, last] and a name")
        for w in range(span[0], span[1] + 1):
            if w in owner:
                raise ClockError(f"clock.yaml: week {w} is in two phases ({owner[w]}, {name})")
            owner[w] = name
        out.append({"weeks": list(span), "name": name})
    for w in range(1, weeks + 1):
        if w not in owner:
            raise ClockError(f"clock.yaml: week {w} has no phase (the clock has {weeks} weeks)")
    extra = sorted(w for w in owner if w < 1 or w > weeks)
    if extra:
        raise ClockError(f"clock.yaml: week {extra[0]} is outside the clock (weeks 1 to {weeks})")
    return out


def load(root: Path) -> dict[str, Any] | None:
    """The validated clock, or None when clock.yaml does not exist (absent is not empty)."""
    path = clock_path(root)
    if not path.is_file():
        return None
    try:
        raw = fm.parse_yaml(path.read_text(encoding="utf-8"))
    except fm.FrontmatterError as exc:
        raise ClockError(f"clock.yaml: {exc}") from None
    return validate(raw)


# ------------------------------------------------------------------------------ status
def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def status(cfg: dict[str, Any], today: date) -> dict[str, Any]:
    n = cfg["clock_days"]
    end = cfg["employment_end"]
    start = end + timedelta(days=1)
    last = start + timedelta(days=n - 1)
    day = (today - start).days + 1
    left = (last - today).days
    weeks = math.ceil(n / 7)
    new = cfg["new_employment_start"]
    label = "confirmed" if cfg["employment_end_confirmed"] else "provisional"
    if new:
        state = "new-job"
    elif today < start:
        state = "before-start"
    elif today <= last:
        state = "running"
    else:
        state = "ended"
    week = 0 if day <= 0 else ((day - 1) // 7 + 1 if day <= n else None)
    if week == 0:
        phase = BEFORE
    elif week is None:
        phase = "ended"
    else:
        phase = next(p["name"] for p in cfg["phases"] if p["weeks"][0] <= week <= p["weeks"][1])

    last_idle = today if new is None else min(today, new - timedelta(days=1))
    used = max(0, (last_idle - start).days + 1)
    allowance = cfg["visa_allowance_days"]
    unemployment = {
        "start": start.isoformat(), "days_used": used,
        "last_day": (new - timedelta(days=1)).isoformat() if new else None,
        "days_total": max(0, (new - start).days) if new else None,
        "allowance": allowance, "allowance_left": None if allowance is None else allowance - used,
        "allowance_source": cfg["visa_allowance_source"],
    }
    new_day = (new - start).days + 1 if new else None
    ends = f"ends {last.isoformat()} ({label})"
    if state == "new-job":
        line = f"Job clock: stopped · new employment starts {new.isoformat()} (day {new_day} of {n}) · {ends}"
    elif state == "ended":
        line = f"Job clock: ended {last.isoformat()} ({label}) · day {day} · {_plural(-left, 'day')} past the end"
    else:
        remaining = "last day" if left == 0 else f"{_plural(left, 'day')} left"
        line = f"Job clock: day {day} of {n} · {remaining} · {ends}"
        if state == "before-start":
            line += f" · starts {start.isoformat()}"
    return {
        "today": today.isoformat(), "employer": cfg["employer"],
        "employment_end": end.isoformat(), "employment_end_confirmed": cfg["employment_end_confirmed"],
        "label": label, "clock_days": n, "clock_start": start.isoformat(), "clock_end": last.isoformat(),
        "day": day, "days_left": left, "state": state, "week": week, "weeks": weeks, "phase": phase,
        "new_employment_start": new.isoformat() if new else None, "new_job_day": new_day,
        "unemployment": unemployment, "stem_extension": cfg["stem_extension"], "basis": cfg["basis"],
        "line": line,
    }


def detail_line(st: dict[str, Any]) -> str:
    """Week, phase and the unemployment count in one line (the dashboard's second clock line)."""
    week = "ended" if st["week"] is None else f"week {st['week']} · {st['phase']}"
    return f"{week[:1].upper()}{week[1:]} · {unemployment_text(st)} · {allowance_text(st)}"


def unemployment_text(st: dict[str, Any]) -> str:
    u = st["unemployment"]
    if st["state"] == "before-start" and not u["days_used"]:
        return f"unemployment days used: 0 (the count starts {u['start']})"
    text = f"unemployment days used: {u['days_used']} (since {u['start']})"
    if u["days_total"] is not None:
        text += f"; the count stops at {u['days_total']} on {u['last_day']}, the day before the new job starts"
    return text


def allowance_text(st: dict[str, Any]) -> str:
    u = st["unemployment"]
    if u["allowance"] is None:
        return "visa allowance: not set (legal-visa sets it only from the DSO's written answer)"
    left = u["allowance_left"]
    state = f"{left} left" if left >= 0 else f"over the allowance by {-left}"
    return f"visa allowance: {u['allowance']} days ({u['allowance_source']}) · {state}"


def render_status(st: dict[str, Any]) -> str:
    employer = f" at {st['employer']}" if st["employer"] else ""
    lines = [st["line"],
             f"state: {st['state']} · last working day{employer}: {st['employment_end']} ({st['label']})",
             "week: ended" if st["week"] is None else f"week {st['week']} · {st['phase']}",
             unemployment_text(st), allowance_text(st)]
    stem = st.get("stem_extension")
    if stem:
        lines.append(f"STEM OPT extension: {stem.get('status', 'status unknown')} since {stem.get('filed', '?')} · "
                     f"automatic extension to {stem.get('auto_extension_to', '?')} · source {stem.get('source', '?')}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------ set
def _yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, date)):
        return value.isoformat() if isinstance(value, date) else str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _comment_at(rest: str) -> int:
    """Index in `rest` (the text after `key:`) where the whitespace before a trailing '# comment' starts,
    or -1. Quotes (with backslash escapes inside double quotes) hide '#'."""
    quote, i = None, 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and i and rest[i - 1] in " \t":
            j = i
            while j and rest[j - 1] in " \t":
                j -= 1
            return j
        i += 1
    return -1


def set_keys(text: str, changes: dict[str, Any]) -> str:
    """Rewrite top-level `key: value` lines in place, keeping a trailing comment in its column; append
    keys that are missing."""
    lines = text.splitlines()
    for key, value in changes.items():
        rendered = _yaml_value(value)
        for i, line in enumerate(lines):
            if not line.startswith(f"{key}:"):
                continue
            rest = line[len(key) + 1:]
            cut = _comment_at(rest)
            if cut < 0:
                lines[i] = f"{key}: {rendered}"
            else:
                old_value, tail = rest[:cut].strip(), rest[cut:]
                comment = tail.lstrip(" \t")
                gap = max(1, len(tail) - len(comment) + len(old_value) - len(rendered))
                lines[i] = f"{key}: {rendered}{' ' * gap}{comment}"
            break
        else:
            lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n"


def apply(root: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Write the changes if the result still validates; return the new clock."""
    path = clock_path(root)
    if not path.is_file():
        raise ClockError(f"clock.yaml not found in {root}")
    text = set_keys(path.read_text(encoding="utf-8"), changes)
    try:
        cfg = validate(fm.parse_yaml(text))
    except fm.FrontmatterError as exc:
        raise ClockError(f"clock.yaml would not parse after the change: {exc}") from None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".clock-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return cfg


def _date_or_none(text: str) -> date | None:
    if str(text).strip().lower() == "none":
        return None
    return cli_date(text)


def _days_or_none(text: str) -> int | None:
    if str(text).strip().lower() == "none":
        return None
    if not re.fullmatch(r"\d+", str(text).strip()):
        raise argparse.ArgumentTypeError(f"expected a whole number of days or none, got {text!r}")
    return int(text)


# ------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=JS_ROOT, help="job-search root (default: this department)")
    common.add_argument("--today", type=cli_date, default=None, help="YYYY-MM-DD (default: the system date)")
    s = sub.add_parser("status", parents=[common], help="where today is on the clock")
    s.add_argument("--json", action="store_true")
    t = sub.add_parser("set", parents=[common], help="change the dates (comments are kept)")
    t.add_argument("--employment-end", type=cli_date, help="the last working day, YYYY-MM-DD")
    t.add_argument("--confirmed", action="store_true", help="the owner confirmed the --employment-end date")
    t.add_argument("--new-employment-start", type=_date_or_none, default=argparse.SUPPRESS,
                   help="first day at the new job, YYYY-MM-DD, or none to clear it")
    t.add_argument("--visa-allowance-days", type=_days_or_none, default=argparse.SUPPRESS,
                   help="unemployment days the DSO confirmed in writing, or none to clear it")
    t.add_argument("--allowance-source", help="the DSO's written answer: date, sender, subject")
    args = ap.parse_args(argv)
    today = args.today or date.today()
    try:
        if args.cmd == "status":
            cfg = load(args.root)
            if cfg is None:
                print(f"clock.yaml not found in {args.root}: the job clock has no data yet", file=sys.stderr)
                return 2
            st = status(cfg, today)
            print(json.dumps(st, indent=2) if args.json else render_status(st))
            return 0
        changes: dict[str, Any] = {}
        if args.confirmed and not args.employment_end:
            raise ClockError("--confirmed needs --employment-end: name the date the owner confirmed")
        if args.employment_end:
            changes["employment_end"] = args.employment_end
            changes["employment_end_confirmed"] = bool(args.confirmed)
        if hasattr(args, "new_employment_start"):
            changes["new_employment_start"] = args.new_employment_start
        if hasattr(args, "visa_allowance_days"):
            if args.visa_allowance_days is not None and not (args.allowance_source or "").strip():
                raise ClockError("--visa-allowance-days needs --allowance-source: the DSO's written answer "
                                 "(date, sender, subject); legal-visa never sets it from anything else")
            changes["visa_allowance_days"] = args.visa_allowance_days
            changes["visa_allowance_source"] = (args.allowance_source or "").strip() or None \
                if args.visa_allowance_days is not None else None
        if not changes:
            raise ClockError("nothing to set: pass --employment-end, --new-employment-start or --visa-allowance-days")
        changes["updated"] = today
        cfg = apply(args.root, changes)
        print(status(cfg, today)["line"])
        return 0
    except ClockError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
