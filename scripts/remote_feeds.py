#!/usr/bin/env python3
"""Remotive and RemoteOK public APIs -> job records (sources remotive, remoteok; platforms.yaml ids remotive, remoteok;
 census BUILD 3).

  GET https://remotive.com/api/remote-jobs?category=software-dev        (Remotive)
  GET https://remoteok.com/api                                          (RemoteOK; the first element is its legal notice)

Terms, read 2026-10-01:
  Remotive (github.com/remotive-io/remote-jobs-api): "link back to the URL found on Remotive AND mention Remotive as a
    source"; jobs "delayed by 24 hours"; "max. 4 times a day", more than 2 a minute blocked; never resubmit its jobs to
    third-party sites. Enforced: a per-day call ledger in state/remote_feeds.json refuses a fifth Remotive call that day.
  RemoteOK (remoteok.com/api, its own JSON): "link back ... to the URL on Remote OK and mention Remote OK as a source";
    no logo use. One call per run.
Every record keeps the board's job URL as source_url and names the board in its body; nothing is republished.

Both boards are mostly worldwide remote: a job is kept only when its location is empty or names the US, the Americas,
North America, worldwide or anywhere, it was posted within --max-days-old, and its title passes the department title
filter. Measured on 2026-10-01: 0 fresh US-eligible on-title roles on either board, which is why the registry keeps both
at status manual; the pipeline polls them only when sources.yaml lists `remotive` / `remoteok`.

CLI:  python remote_feeds.py [--root R] [--feed remotive|remoteok ...] [--max-days-old 7] [--today D] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (Http, TitleFilter, default_title_filter, html_to_text, load_title_filter,  # noqa: E402
                       make_record, norm_employment, parse_comp_text, parse_ts, upsert_jobs)
from himalayas import is_offline_error, run_now  # noqa: E402
from js_common import JS_ROOT, cli_date, normalize_company, short_hash, slugify  # noqa: E402

REMOTIVE = "https://remotive.com/api/remote-jobs"
REMOTEOK = "https://remoteok.com/api"
FEEDS = ("remotive", "remoteok")
DAILY_CAP = {"remotive": 4, "remoteok": 4}
MAX_DAYS_OLD = 7
US_OK = re.compile(r"\b(?:usa?|u\.s\.a?\.?|united states|americas|north america|worldwide|anywhere|global)\b", re.I)
NAMES = {"remotive": "Remotive (remotive.com)", "remoteok": "Remote OK (remoteok.com)"}


def _ledger_path(root: Path) -> Path:
    return Path(root) / "state" / "remote_feeds.json"


def _take_call(root: Path, feed: str, day: str) -> bool:
    """Count one call for feed today; False when the day's cap is reached (nothing counted)."""
    path = _ledger_path(root)
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    used = int(data.get(feed, {}).get(day, 0))
    if used >= DAILY_CAP[feed]:
        return False
    data.setdefault(feed, {})
    data[feed] = {day: used + 1}                       # keep only today's count
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return True


def _record(feed: str, *, company: str, title: str, url: str, apply_url: str, posted: Any, location: str | None,
            comp: tuple, employment: str | None, jd_html: str, ident: str, now: datetime) -> dict[str, Any]:
    slug = slugify(normalize_company(company)) or slugify(company) or "unknown"
    rec = make_record({"name": company, "slug": slug}, title=title, source=feed, source_url=url, apply_url=apply_url,
                      ats_vendor="unknown", posted_at=posted, location=location or None, remote="remote", comp=comp,
                      employment_type=employment, jd_text=html_to_text(jd_html), now=now)
    rec["id"] = f"{slug}-{'rv' if feed == 'remotive' else 'rok'}-{short_hash(ident or url, n=8)}"
    rec["platform"] = feed
    rec["_body"] = rec["_body"] + f"\n- Via: {NAMES[feed]} public jobs API; credit and link back kept per its API terms\n"
    return rec


def _remotive(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    return _record("remotive", company=str(job.get("company_name") or "unknown"), title=str(job.get("title") or "").strip(),
                   url=str(job.get("url") or ""), apply_url=str(job.get("url") or ""), posted=job.get("publication_date"),
                   location=job.get("candidate_required_location"), comp=parse_comp_text(str(job.get("salary") or "")),
                   employment=norm_employment(job.get("job_type")), jd_html=str(job.get("description") or ""),
                   ident=str(job.get("id") or ""), now=now)


def _remoteok(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    lo, hi = job.get("salary_min") or None, job.get("salary_max") or None
    return _record("remoteok", company=str(job.get("company") or "unknown"), title=str(job.get("position") or "").strip(),
                   url=str(job.get("url") or ""), apply_url=str(job.get("apply_url") or job.get("url") or ""),
                   posted=job.get("date"), location=job.get("location"),
                   comp=(int(lo) if lo else None, int(hi) if hi else None, "USD") if (lo or hi) else (None, None, None),
                   employment=None, jd_html=str(job.get("description") or ""), ident=str(job.get("id") or ""), now=now)


def collect(http: Any, now: datetime, root: Path, feeds: list[str] | None = None, title_filter: TitleFilter | None = None,
            max_days_old: int = MAX_DAYS_OLD) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tf = title_filter or default_title_filter()
    cutoff, day = now - timedelta(days=max_days_old), now.date().isoformat()
    records: dict[str, dict[str, Any]] = {}
    info: dict[str, Any] = {}
    for feed in feeds or list(FEEDS):
        row = {"results": 0, "kept": 0, "stale": 0, "not_us": 0, "filtered": 0}
        info[feed] = row
        if not _take_call(root, feed, day):
            row["skipped"] = f"daily cap of {DAILY_CAP[feed]} calls reached ({day})"
            continue
        try:
            if feed == "remotive":
                jobs = (http.get_json(REMOTIVE, params={"category": "software-dev"}) or {}).get("jobs") or []
                pairs = [(j, j.get("title"), j.get("candidate_required_location"), j.get("publication_date"), _remotive)
                         for j in jobs]
            else:
                jobs = [j for j in http.get_json(REMOTEOK) or [] if isinstance(j, dict) and j.get("id")]
                pairs = [(j, j.get("position"), j.get("location"), j.get("date"), _remoteok) for j in jobs]
        except Exception as exc:
            if not is_offline_error(exc):
                raise
            row["skipped"] = f"no network: {exc}"
            continue
        for job, title, loc, posted, build in pairs:
            row["results"] += 1
            when = parse_ts(posted)
            if when is None or when < cutoff:
                row["stale"] += 1
            elif loc and not US_OK.search(str(loc)):
                row["not_us"] += 1
            elif not tf.matches(str(title or "")):
                row["filtered"] += 1
            else:
                rec = build(job, now)
                records.setdefault(rec["id"], rec)
                row["kept"] += 1
    return list(records.values()), info


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--feed", action="append", choices=FEEDS, default=[])
    ap.add_argument("--max-days-old", type=int, default=MAX_DAYS_OLD)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or run_now(args.today)
    records, info = collect(http or Http(min_interval=31.0), now, args.root, feeds=args.feed or None,
                            title_filter=load_title_filter(args.root), max_days_old=args.max_days_old)
    for feed, row in info.items():
        print(f"{feed}: " + (row["skipped"] if row.get("skipped") else
              f"{row['results']} results, {row['kept']} kept ({row['stale']} stale, {row['not_us']} not US, "
              f"{row['filtered']} off-title)"))
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    print(f"{'dry-run (nothing written)' if args.dry_run else 'written'}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
