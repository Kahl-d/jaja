#!/usr/bin/env python3
"""The people watch: the people who post about hiring, watched on X and read on LinkedIn.

    python3 scripts/people_watch.py check                       # validate targets/people-watch.yaml
    python3 scripts/people_watch.py x-queries                   # the X `from:` queries x_api.py watch runs
    python3 scripts/people_watch.py linkedin-plan [--limit 20]  # today's named reads, oldest-read first
    python3 scripts/people_watch.py mark-read --person "Name"   # after a read, so the rotation moves on

`targets/people-watch.yaml` (agency-owned data; job-scout and prospector propose rows) lists each person with a name,
`x_handle` and/or `linkedin_url` (a linkedin.com/in/ profile, never a search), company, role, **why** they are on the
list, added, source, and status: `watch` (read, not followed), `followed` (the owner followed them, by hand or by an
approved x_api.py follow) or `dropped`. A row without a reason or a channel is refused.

X: `x_api.py watch` adds every active handle here to its `from:` queries (chunked under the query length limit), so
their hiring posts land in the watch's review file. LinkedIn: the plan names at most A_DAILY_VIEWS (default 20, policy.browser.people_watch_daily_views) of the shared
daily profile reads, each through `browser_research.py view` (one named person's recent-activity
page, never a feed or a search list), during the weekday sweep the owner runs. Nothing here follows, clicks or sends;
a follow is an approval (x_api.py follow --request) or the owner's own click on LinkedIn.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402

A_DAILY_VIEWS = int(cfg.get("policy.browser.people_watch_daily_views", 20))   # this watch's share of the daily profile reads
STATUSES = ("watch", "followed", "dropped")
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
PROFILE = re.compile(r"^https?://(?:www\.)?linkedin\.com/in/[^/?#]+/?$")
X_QUERY_LIMIT = 480


class WatchError(RuntimeError):
    """The people-watch file or a request against it is invalid."""


def _path(root: Path) -> Path:
    return Path(root) / "targets" / "people-watch.yaml"


def load(root: Path) -> list[dict[str, Any]]:
    path = _path(root)
    if not path.is_file():
        return []
    import yaml
    people = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("people") or []
    out = []
    for i, p in enumerate(people, 1):
        name = str((p or {}).get("name") or "").strip()
        where = f"people-watch.yaml person {i} ({name or 'no name'})"
        if not name:
            raise WatchError(f"{where}: a name is required")
        if not str(p.get("why") or "").strip():
            raise WatchError(f"{where}: `why` is required (the reason this person is watched)")
        if p.get("status") not in STATUSES:
            raise WatchError(f"{where}: status must be one of {STATUSES}")
        handle = str(p.get("x_handle") or "").strip().lstrip("@")
        url = str(p.get("linkedin_url") or "").strip()
        if not handle and not url:
            raise WatchError(f"{where}: at least one channel (x_handle or linkedin_url) is required")
        if handle and not HANDLE.match(handle):
            raise WatchError(f"{where}: x_handle {handle!r} is not an X handle")
        if url and not PROFILE.match(url):
            raise WatchError(f"{where}: linkedin_url must be one profile (linkedin.com/in/<name>), not {url!r}")
        out.append({**p, "name": name, "x_handle": handle.lower() or None, "linkedin_url": url or None})
    return out


def active(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in people if p["status"] != "dropped"]


def x_handles(root: Path) -> list[str]:
    return [p["x_handle"] for p in active(load(root)) if p["x_handle"]]


def x_queries(handles: list[str], *, limit: int = X_QUERY_LIMIT) -> list[str]:
    """`(from:a OR from:b ...) -is:retweet`, chunked so each query stays under X's length limit."""
    out, chunk = [], []
    tail = " -is:retweet"

    def text(hs: list[str]) -> str:
        return "(" + " OR ".join(f"from:{h}" for h in hs) + ")" + tail
    for h in handles:
        if chunk and len(text(chunk + [h])) > limit:
            out.append(text(chunk))
            chunk = []
        chunk.append(h)
    if chunk:
        out.append(text(chunk))
    return out


def _reads_path(root: Path) -> Path:
    return Path(root) / "state" / "people-watch" / "reads.json"


def _reads(root: Path) -> dict[str, str]:
    try:
        return json.loads(_reads_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_read(root: Path, name: str, today: date) -> None:
    reads = _reads(root)
    reads[name] = today.isoformat()
    path = _reads_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reads, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def linkedin_plan(root: Path, *, today: date, limit: int = A_DAILY_VIEWS) -> list[dict[str, Any]]:
    """Today's named LinkedIn reads: active people with a profile, least recently read first."""
    if limit > A_DAILY_VIEWS:
        raise WatchError(f"the people watch reads at most {A_DAILY_VIEWS} LinkedIn profiles a day")
    reads = _reads(root)
    people = [p for p in active(load(root)) if p["linkedin_url"]]
    order = sorted(range(len(people)), key=lambda i: (reads.get(people[i]["name"], ""), i))
    plan = []
    for i in order[:limit]:
        p = people[i]
        page = p["linkedin_url"].rstrip("/") + "/recent-activity/all/"
        plan.append({"name": p["name"], "company": p.get("company"), "why": p.get("why"), "url": page,
                     "last_read": reads.get(p["name"]),
                     "command": (f"python3 scripts/browser_research.py view --person \"{p['name']}\" --url {page} "
                                 f"--named-by \"people-watch\"")})
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "x-queries", "linkedin-plan", "mark-read"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, default=jc.JS_ROOT)
        p.add_argument("--today")
        if name == "linkedin-plan":
            p.add_argument("--limit", type=int, default=A_DAILY_VIEWS)
        if name == "mark-read":
            p.add_argument("--person", required=True)
    args = ap.parse_args(argv)
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        if args.cmd == "check":
            people = load(args.root)
            print(f"people-watch: {len(people)} people ({len(active(people))} active); "
                  f"{sum(1 for p in active(people) if p['x_handle'])} on X, "
                  f"{sum(1 for p in active(people) if p['linkedin_url'])} on LinkedIn")
        elif args.cmd == "x-queries":
            print("\n".join(x_queries(x_handles(args.root))) or "no X handles on the list")
        elif args.cmd == "linkedin-plan":
            for p in linkedin_plan(args.root, today=today, limit=args.limit):
                print(f"- {p['name']} ({p['company']}; {p['why']}); last read {p['last_read'] or 'never'}\n  {p['command']}")
        else:
            mark_read(args.root, args.person, today)
    except WatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
