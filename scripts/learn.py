#!/usr/bin/env python3
"""learn.py - the shared learning record: every role and lane writes what it learned
as one JSON line per observation in state/learn/<role>.jsonl, so the evening wrap, lessons.py and strategy_review read
one place.

A row has exactly these keys:
  date         YYYY-MM-DD, the department's day (Pacific) when it was written; computed at run time
  role         the role or lane that learned it (kebab-case: recruiter, resume, prep-coach ...); also the file name
  observation  what happened, one line
  evidence     where it shows (a file, a command's number, an event); never empty (rule 7: done means verified)
  change       what changes because of it ("" when nothing changes yet)
  status       proposed (a candidate) | tried (the change is running) | kept (it helped at its re-check)
The file is append-only (OS rule 12): a status moves by appending the same observation with the new status; the
newest row of an observation is its status. The same row twice on one day is written once (a lane may retry).

Charter §1 decides when a candidate may change anything: about 15 tries in that segment, or one clear cause
(js_common.MIN_TRIES; lessons.py marks experiments ready). LESSONS.md stays the Recruiter's ledger of decided lessons.

CLI (stable: every lane card's "Learned" step calls it):
  python3 scripts/learn.py add --role <role> --observation "..." --evidence "..." [--change "..."]
                               [--status proposed|tried|kept] [--date YYYY-MM-DD]
  python3 scripts/learn.py list [--role R] [--status S] [--since YYYY-MM-DD] [--json]
Read only except `add`, which appends one line. Nothing outward.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, append_jsonl, cli_date, dept_day  # noqa: E402

LEARN_DIR = Path("state") / "learn"
KEYS = ("date", "role", "observation", "evidence", "change", "status")
STATUSES = ("proposed", "tried", "kept")
ROLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_TEXT = 1000


class LearnError(ValueError):
    """A row was refused; nothing was written."""


def validate(row: Any) -> list[str]:
    """The schema problems of one row ([] when it is valid)."""
    if not isinstance(row, dict):
        return ["not a JSON object"]
    errs = []
    missing = [k for k in KEYS if k not in row]
    extra = sorted(set(row) - set(KEYS))
    if missing:
        errs.append(f"missing {', '.join(missing)}")
    if extra:
        errs.append(f"unknown key {', '.join(extra)}")
    d = row.get("date")
    if "date" in row:
        if not (isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)):
            errs.append(f"date {d!r} is not YYYY-MM-DD")
        else:
            try:
                date.fromisoformat(d)
            except ValueError:
                errs.append(f"date {d!r} is not a calendar day")
    if "role" in row and not (isinstance(row["role"], str) and ROLE_RE.fullmatch(row["role"])):
        errs.append(f"role {row.get('role')!r} is not kebab-case")
    for key in ("observation", "evidence"):
        if key in row and not (isinstance(row[key], str) and row[key].strip()):
            errs.append(f"{key} is empty")
    if "change" in row and not isinstance(row["change"], str):
        errs.append("change is not text")
    for key in ("observation", "evidence", "change"):
        v = row.get(key)
        if isinstance(v, str) and ("\n" in v or len(v) > MAX_TEXT):
            errs.append(f"{key} must be one line of at most {MAX_TEXT} characters")
    if "status" in row and row["status"] not in STATUSES:
        errs.append(f"status {row.get('status')!r} is not one of {', '.join(STATUSES)}")
    return errs


def path_for(root: Path, role: str) -> Path:
    return Path(root) / LEARN_DIR / f"{role}.jsonl"


def read(root: Path, role: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(valid rows, problems) from state/learn/*.jsonl (one role's file with `role`), in file order. A problem is
    {"file", "line", "errors"}: a bad line is reported, never dropped silently and never read as a row."""
    folder = Path(root) / LEARN_DIR
    files = [path_for(root, role)] if role else sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
    rows, problems = [], []
    for f in files:
        if not f.is_file():
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append({"file": f.name, "line": n, "errors": [f"not JSON ({exc.msg})"]})
                continue
            errs = validate(row)
            if not errs and row["role"] != f.stem:
                errs = [f"role {row['role']!r} written in {f.name}"]
            if errs:
                problems.append({"file": f.name, "line": n, "errors": errs})
            else:
                rows.append(row)
    return rows, problems


def current(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest row of each (role, observation): its present status, in first-seen order."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        latest[(r["role"], r["observation"].strip())] = r
    return list(latest.values())


def add(root: Path, role: str, observation: str, evidence: str, change: str = "", status: str = "proposed",
        day: date | None = None) -> tuple[dict[str, Any], bool]:
    """Append one validated row to state/learn/<role>.jsonl. Returns (row, written): written is False when the same
    row (date, role, observation, status) is already there. Raises LearnError on a schema problem."""
    day = day or dept_day(datetime.now(timezone.utc))
    row = {"date": day.isoformat(), "role": str(role).strip(), "observation": str(observation).strip(),
           "evidence": str(evidence).strip(), "change": str(change or "").strip(), "status": status}
    errs = validate(row)
    if errs:
        raise LearnError("refused: " + "; ".join(errs) + "; nothing was written")
    have, _ = read(root, row["role"])
    same = ("date", "role", "observation", "status")
    if any(all(h[k] == row[k] for k in same) for h in have):
        return row, False
    append_jsonl(path_for(root, row["role"]), row)
    return row, True


def select(rows: list[dict[str, Any]], status: str | None = None, since: date | None = None) -> list[dict[str, Any]]:
    return [r for r in rows if (status is None or r["status"] == status)
            and (since is None or r["date"] >= since.isoformat())]


def render(rows: list[dict[str, Any]], problems: list[dict[str, Any]]) -> str:
    lines = [f"{len(rows)} learned rows (state/learn/*.jsonl)" + (f"; {len(problems)} invalid lines" if problems else "")]
    for r in rows:
        lines.append(f"- {r['date']} · {r['role']} · {r['status']}: {r['observation']} (evidence: {r['evidence']})"
                     + (f"; change: {r['change']}" if r["change"] else ""))
    for p in problems:
        lines.append(f"- INVALID {p['file']}:{p['line']}: {'; '.join(p['errors'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="append one observation")
    a.add_argument("--role", required=True)
    a.add_argument("--observation", required=True)
    a.add_argument("--evidence", required=True)
    a.add_argument("--change", default="")
    a.add_argument("--status", default="proposed", choices=STATUSES)
    a.add_argument("--date", type=cli_date, default=None)
    ls = sub.add_parser("list", help="list the rows (read only)")
    ls.add_argument("--role", default=None)
    ls.add_argument("--status", default=None, choices=STATUSES)
    ls.add_argument("--since", type=cli_date, default=None)
    ls.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        try:
            row, written = add(args.root, args.role, args.observation, args.evidence, args.change, args.status,
                               args.date)
        except LearnError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        rel = path_for(Path("."), row["role"]).as_posix()
        print(f"{'wrote' if written else 'already recorded'} {rel}: {row['date']} {row['status']}: {row['observation']}")
        return 0
    if args.role and not ROLE_RE.fullmatch(args.role):
        print(f"refused: role {args.role!r} is not kebab-case", file=sys.stderr)
        return 2
    rows, problems = read(args.root, args.role)
    rows = select(rows, args.status, args.since)
    if args.json:
        print(json.dumps({"rows": rows, "invalid": problems}, indent=1, ensure_ascii=False))
    else:
        print(render(rows, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
