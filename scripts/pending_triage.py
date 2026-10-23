#!/usr/bin/env python3
"""A dry-run proposal for the postings parked in jobs-pending/. Writes nothing in jobs/.

    python3 scripts/pending_triage.py [--root R] [--today YYYY-MM-DD] [--live] [--out hub/plans/jobs-pending.md]

When the owner empties the jobs table to start clean, the postings found so far are parked in jobs-pending/. This
sorts them without importing anything:
  board_rescan     postings from a company board (Greenhouse, Lever, Ashby, Workday, Jibe, sitemap, amazon): the
                   board scan finds the live ones again with current data, so they are not imported
  already_in_jobs  the same URL is already a job
  gate_fail        the parked record failed the hard gates
  scout_skipped    the scout already judged it out of scope (status skipped, a review line says why)
  below_cut        other sources (HN, Himalayas) scored under the import cut (PENDING_CUT, 60: parked postings are
                   old, so freshness alone keeps them under the daily cut; the import re-runs
                   gates and triage, which apply the real cut)
  proposed         other sources at or over the import cut: listed with their hunt_score, and with --live a polite GET of
                   the posting (200 = up, 404 or 410 = gone, anything else = unknown; the host's robots.txt read first, RFC 9309; 2 s apart or its
                   Crawl-delay) so only live ones are imported
The import, on the owner's word: `mv jobs-pending/<id>.md jobs/`, then
`python3 scripts/pipeline.py --stages gates,queue,triage`.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import fetch_ats as fa  # noqa: E402
import hunt_score as hs  # noqa: E402
import js_common as jc  # noqa: E402

BOARD_SOURCES = ("greenhouse", "lever", "ashby", "workday", "jibe", "sitemap", "amazon")
LIVE_DELAY_S = 2.0
PENDING_CUT = 60.0
GONE = (404, 410)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _robots(http: Any, host: str) -> Any:
    """The host's robots.txt as fetch_ats.Robots (RFC 9309), or a note saying why nothing may be fetched there:
    a 4xx robots.txt means no rules; no answer or a 5xx means a full disallow."""
    try:
        status, text = http.get_text(f"{host}/robots.txt", headers={"Accept": "text/plain"})
    except Exception:
        status, text = 0, ""
    if status == 0 or status >= 500:
        return f"not fetched: {host}/robots.txt did not answer (status {status or 'none'}), a full disallow"
    return fa.Robots(text if status < 400 else "")


def triage(root: Path, *, today: date, http: Any = None, live: bool = False,
           cut: float = PENDING_CUT) -> dict[str, Any]:
    root = Path(root)
    known = {str(r.get("source_url")) for r in fa.load_jobs(root).values() if r.get("source_url")} \
        if (root / "jobs").is_dir() else set()
    ctx = hs.build_context(root, today=today)
    counts = {"parked": 0, "board_rescan": 0, "already_in_jobs": 0, "gate_fail": 0, "scout_skipped": 0,
              "below_cut": 0, "proposed": 0}
    proposed = []
    for path, meta, body in jc.iter_records(root / "jobs-pending"):
        counts["parked"] += 1
        rec = dict(meta)
        rec.setdefault("id", path.stem)
        rec["_jd_text"] = fa.jd_text_from_body(body)
        if rec.get("source") in BOARD_SOURCES:
            counts["board_rescan"] += 1
            continue
        if str(rec.get("source_url")) in known:
            counts["already_in_jobs"] += 1
            continue
        if rec.get("gate") == "fail":
            counts["gate_fail"] += 1
            continue
        if rec.get("status") == "skipped":
            counts["scout_skipped"] += 1
            continue
        res = hs.score(dict(rec, status="fit_checked"), ctx)
        if res["hunt_score"] < cut:
            counts["below_cut"] += 1
            continue
        proposed.append({"id": rec["id"], "hunt_score": res["hunt_score"], "company": rec.get("company"),
                         "title": rec.get("title"), "source": rec.get("source"), "url": rec.get("source_url"),
                         "posted_at": rec.get("posted_at"), "live": None})
    proposed.sort(key=lambda p: (-p["hunt_score"], p["id"]))
    if live and proposed:
        http = http or fa.Http()
        robots: dict[str, Any] = {}
        fetched = False
        for p in proposed:
            parts = urlparse(str(p["url"]))
            host = f"{parts.scheme}://{parts.netloc}"
            if host not in robots:
                robots[host] = _robots(http, host)
            rules = robots[host]
            if isinstance(rules, str):
                p["live_note"] = rules
                continue
            if not rules.allowed(str(p["url"])):
                p["live_note"] = f"not fetched: {host}/robots.txt disallows this path"
                continue
            if fetched:
                _sleep(max(LIVE_DELAY_S, rules.delay or 0))
            fetched = True
            try:
                status, _ = http.get_text(str(p["url"]), headers=fa.HTML_ACCEPT)
            except Exception:
                p["live_note"] = "unknown (no answer)"
                continue
            if status == 200:
                p["live"] = True
            elif status in GONE:
                p["live"] = False
            else:                          # a bot wall (403), a rate limit or a server error says nothing either way
                p["live_note"] = f"unknown (HTTP {status}; check by hand)"
    counts["proposed"] = len(proposed)
    return {"date": today.isoformat(), "cut": cut, "daily_cut": ctx["cut"], "live": live, "counts": counts,
            "proposed": proposed}


def _cell(value: Any, width: int = 80) -> str:
    """One table cell: no pipe (it would split the row), no em-dash when style.no_em_dash is set, one line, at most `width` chars."""
    text = re.sub(r"\s+", " ", str(value or "")).replace("|", "/")
    if cfg.get("style.no_em_dash", False):
        text = text.replace("\u2014", "-")
    text = text.strip()
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def render(out: dict[str, Any]) -> str:
    c = out["counts"]
    how = "with `--live`" if out.get("live") else "without `--live` (liveness not checked)"
    lines = [f"# The parked postings (jobs-pending/), a dry-run proposal ({out['date']})", "",
             f"Produced by `python3 scripts/pending_triage.py` {how}; nothing was imported. Counts:", ""]
    lines += [f"- {k}: {v}" for k, v in c.items()]
    lines += ["", "Board postings are left to the board scan: it re-finds the live ones with current data. Proposed for "
              f"import (other sources at the import cut {out['cut']:g} or over; the daily cut is {out['daily_cut']:g}; `live` is "
              "a GET of the posting today: yes = HTTP 200, no = 404 or 410, anything else unknown; for an HN posting, yes means "
              "the Who is Hiring comment is still up, not that the role is still open):", "",
              "| score | company | title | source | posted | live | import |", "|---|---|---|---|---|---|---|"]
    for p in out["proposed"]:
        live = {True: "yes", False: "no", None: p.get("live_note") or "not checked"}[p["live"]]
        lines.append(f"| {p['hunt_score']} | {_cell(p['company'], 40)} | {_cell(p['title'])} | {p['source']} | {str(p['posted_at'])[:10]} | "
                     f"{live} | `mv jobs-pending/{p['id']}.md jobs/` |")
    lines += ["", "After the moves: `python3 scripts/pipeline.py --stages gates,queue,triage` (commit on the owner's word)."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today")
    ap.add_argument("--live", action="store_true", help="GET each proposed posting (polite, 2 s apart)")
    ap.add_argument("--cut", type=float, default=PENDING_CUT, help=f"import cut (default {PENDING_CUT:g})")
    ap.add_argument("--out", type=Path, help="write the proposal here (default: print)")
    args = ap.parse_args(argv)
    today = jc.parse_date(args.today) if args.today else jc.today()
    text = render(triage(args.root, today=today, live=args.live, cut=args.cut))
    if args.out:
        out = args.out if args.out.is_absolute() else args.root / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
