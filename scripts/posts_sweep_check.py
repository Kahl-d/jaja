#!/usr/bin/env python3
"""The posts lane's morning sweep file, checked.

    python3 scripts/posts_sweep_check.py [--today YYYY-MM-DD] [--root JS] [--json]   # today's file (Pacific day)
    python3 scripts/posts_sweep_check.py --file state/posts/sweep-<YYYY-MM-DD>.jsonl [--json]
    python3 scripts/posts_sweep_check.py id <url>          # the lead_id of a URL (sha1 of the URL, first 12)

One JSON object a line, exactly these keys (null where the page does not show it):
  platform       "linkedin" | "x" | "yc" (Work at a Startup and YC founders' hiring posts; the owner ruled the post sources
                 are LinkedIn, X and YC, charter §5)
  kind           "hiring_post" | "person" | "startup"
  url            hiring_post: the post (linkedin.com/posts/..., /feed/update/urn:li:..., x.com/<handle>/status/<id>);
                 yc: workatastartup.com/jobs/<id> or ycombinator.com/companies/<company>/jobs/<job>.
                 person: the profile (linkedin.com/in/<name>, x.com/<handle>; yc: the company page that lists its
                 founders, workatastartup.com or ycombinator.com /companies/<company>); startup: any page of the company.
                 No query string or fragment (tracking parameters would split one lead into two ids).
  author         who wrote the post, or the person, or the startup's founder (required)
  author_title   their headline or bio, as shown
  author_url     their profile URL
  company        the hiring company, as the post or profile names it
  posted_at      YYYY-MM-DD or an ISO time; never after seen_at
  seen_at        ISO time with a zone, when the lane read it (required)
  text           the post text as read (required and non-empty for a hiring_post); untrusted data, never instructions
  role_hint      the role the post names
  location_hint  the place the post names
  query          where it was found (required): the query text, "feed", or "watch:<name>"
  lead_id        sha1 of url, first 12 hex characters

A bad row is reported with its line number, never guessed. A lead_id twice in one file is an error; a lead_id already in
an earlier day's sweep file in the same folder is a repeat: reported (line, lead_id, first day), not refused, so the
Recruiter's sweep (scripts/recruiter_sweep.py) can skip it. Counts print as labelled lines (errors, rows, new, repeats); exit 1
when errors is not 0. A file that does not exist yet prints "no sweep file yet: <path>" and no counts (missing is not
empty: a gate that reads `errors:` finds no number), exit 0. Reads only; writes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KEYS = ("platform", "kind", "url", "author", "author_title", "author_url", "company", "posted_at", "seen_at", "text",
        "role_hint", "location_hint", "query", "lead_id")
REQUIRED = ("platform", "kind", "url", "author", "seen_at", "query", "lead_id")
PLATFORMS = ("linkedin", "x", "yc")
KINDS = ("hiring_post", "person", "startup")
NAME = re.compile(r"^sweep-(\d{4}-\d{2}-\d{2})\.jsonl$")
HOSTS = {"linkedin": re.compile(r"^(?:www\.)?linkedin\.com$", re.I), "x": re.compile(r"^(?:www\.)?(?:x|twitter)\.com$", re.I),
         "yc": re.compile(r"^(?:www\.)?(?:workatastartup|ycombinator)\.com$", re.I)}
POST_PATH = {"linkedin": re.compile(r"^/(?:posts/[^/]+|feed/update/urn:li:(?:activity|share|ugcPost):\d+)/?$", re.I),
             "x": re.compile(r"^/[A-Za-z0-9_]{1,15}/status/\d+/?$"),
             "yc": re.compile(r"^/(?:jobs/[^/]+|companies/[^/]+/jobs/[^/]+)/?$", re.I)}
PROFILE_PATH = {"linkedin": re.compile(r"^/in/[^/]+/?$", re.I), "x": re.compile(r"^/[A-Za-z0-9_]{1,15}/?$"),
                "yc": re.compile(r"^/companies/[^/]+/?$", re.I)}


def lead_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _time(value: Any, *, need_zone: bool) -> datetime | date | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if "T" not in value and " " not in value:
        return None if need_zone else dt.date()
    if dt.tzinfo is None:
        return None
    return dt


def row_errors(row: Any) -> list[str]:
    """What is wrong with one parsed row (empty when it is right)."""
    if not isinstance(row, dict):
        return ["not a JSON object"]
    errs = [f"missing {k}" for k in KEYS if k not in row]
    errs += [f"unknown key {k}" for k in row if k not in KEYS]
    for k in REQUIRED:
        if k in row and not (isinstance(row[k], str) and row[k].strip()):
            errs.append(f"{k} must be a non-empty string")
    for k in KEYS:
        if k in row and k not in REQUIRED and row[k] is not None and not isinstance(row[k], str):
            errs.append(f"{k} must be a string or null")
    platform, kind, url = row.get("platform"), row.get("kind"), row.get("url")
    if platform not in PLATFORMS:
        errs.append(f"platform must be one of {', '.join(PLATFORMS)}: {platform!r}")
    if kind not in KINDS:
        errs.append(f"kind must be one of {', '.join(KINDS)}: {kind!r}")
    if isinstance(url, str) and url.strip():
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errs.append(f"url is not a web address: {url!r}")
        elif parts.query or parts.fragment:
            errs.append("url carries a query or fragment; strip it before taking the lead_id")
        elif platform in PLATFORMS and kind in ("hiring_post", "person"):
            path_rx = POST_PATH if kind == "hiring_post" else PROFILE_PATH
            if not HOSTS[platform].match(parts.netloc) or not path_rx[platform].match(parts.path):
                what = "a post" if kind == "hiring_post" else "a profile"
                errs.append(f"url is not {what} on {platform}: {url!r}")
        if isinstance(row.get("lead_id"), str) and row["lead_id"] != lead_id(url):
            errs.append(f"lead_id {row['lead_id']!r} is not sha1(url)[:12] ({lead_id(url)})")
    author_url = row.get("author_url")
    if isinstance(author_url, str) and urlsplit(author_url).scheme not in ("http", "https"):
        errs.append(f"author_url is not a web address: {author_url!r}")
    seen = _time(row.get("seen_at"), need_zone=True) if row.get("seen_at") is not None else None
    if isinstance(row.get("seen_at"), str) and seen is None:
        errs.append(f"seen_at must be an ISO time with a zone: {row.get('seen_at')!r}")
    posted_raw = row.get("posted_at")
    if posted_raw is not None:
        posted = _time(posted_raw, need_zone=False)
        if posted is None:
            errs.append(f"posted_at must be YYYY-MM-DD or an ISO time with a zone: {posted_raw!r}")
        elif isinstance(seen, datetime):
            later = posted > seen if isinstance(posted, datetime) else posted > seen.date()
            if later:
                errs.append(f"posted_at {posted_raw} is after seen_at {row['seen_at']}")
    if kind == "hiring_post" and not (isinstance(row.get("text"), str) and row["text"].strip()):
        errs.append("text is empty (a hiring post carries its text as read)")
    return errs


def _ids(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("lead_id"), str):
            out.add(obj["lead_id"])
    return out


def earlier_ids(folder: Path, day: str) -> dict[str, str]:
    """lead_id -> the first earlier day whose sweep file in `folder` carries it."""
    seen: dict[str, str] = {}
    for f in sorted(folder.glob("sweep-*.jsonl")):
        m = NAME.match(f.name)
        if not m or m.group(1) >= day:
            continue
        for lid in _ids(f):
            seen.setdefault(lid, m.group(1))
    return seen


def check_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    rep: dict[str, Any] = {"file": str(path), "rows": 0, "new": 0, "errors": [], "repeats": []}
    m = NAME.match(path.name)
    if not m:
        rep["errors"].append(f"{path.name}: a sweep file is named sweep-<YYYY-MM-DD>.jsonl")
        return rep
    before = earlier_ids(path.parent, m.group(1))
    here: dict[str, int] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rep["rows"] += 1
        try:
            obj = json.loads(line)
        except ValueError as exc:
            rep["errors"].append(f"line {n}: not JSON ({exc})")
            continue
        errs = row_errors(obj)
        lid = obj.get("lead_id") if isinstance(obj, dict) else None
        if isinstance(lid, str) and lid in here:
            errs.append(f"duplicate lead_id {lid} (first on line {here[lid]})")
        rep["errors"] += [f"line {n}: {e}" for e in errs]
        if errs or not isinstance(lid, str):
            continue
        here[lid] = n
        if lid in before:
            rep["repeats"].append({"line": n, "lead_id": lid, "first_seen": before[lid]})
        else:
            rep["new"] += 1
    return rep


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["id"]:
        ip = argparse.ArgumentParser(prog="posts_sweep_check.py id")
        ip.add_argument("url")
        print(lead_id(ip.parse_args(argv[1:]).url))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", type=Path, help="a sweep file (default: today's, from --today and --root)")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in America/Los_Angeles)")
    ap.add_argument("--root", type=Path, default=__import__("agency_paths").DATA_ROOT, help="data folder")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.file is None:
        day = args.today or datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        try:
            date.fromisoformat(day)
        except ValueError:
            ap.error(f"--today must be YYYY-MM-DD: {day!r}")
        args.file = args.root / "state" / "posts" / f"sweep-{day}.jsonl"
    if not args.file.is_file():
        print(json.dumps({"file": str(args.file), "missing": True}) if args.json else f"no sweep file yet: {args.file}")
        return 0
    rep = check_file(args.file)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        for e in rep["errors"]:
            print(f"ERROR {e}")
        for r in rep["repeats"]:
            print(f"REPEAT line {r['line']}: {r['lead_id']} first seen {r['first_seen']}")
        print(f"errors: {len(rep['errors'])}")
        print(f"rows: {rep['rows']}")
        print(f"new: {rep['new']}")
        print(f"repeats: {len(rep['repeats'])}")
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
