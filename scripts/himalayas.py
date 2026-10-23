#!/usr/bin/env python3
"""Himalayas remote-jobs API -> job records (source: himalayas; platform targets/platforms.yaml id himalayas).

  GET https://himalayas.app/jobs/api/search?q&country&employment_type&sort&page

Keyless public API. Terms (https://himalayas.app/api and /docs/remote-jobs-api, read 2026-09-25):
anyone may use it who links back to the Himalayas job URL and names Himalayas as the source; its jobs
are never resubmitted to third-party sites; requests are rate limited (HTTP 429) and the data refreshes
every 24 hours, so this runs at most once a day. Every record keeps the Himalayas job URL as
source_url and names Himalayas in its body.

Response (observed 2026-09-25): {updatedAt, offset, limit, totalCount, jobs: [...]}; a job has title,
companyName, companySlug, employmentType, minSalary, maxSalary, salaryPeriod, currency, seniority,
locationRestrictions (country names; the docs also show objects with a name), timezoneRestrictions,
description (HTML), pubDate and expiryDate (epoch seconds; the docs say milliseconds, both are
accepted), applicationLink and guid (the Himalayas job page). Himalayas lists remote jobs only.

Per query: pages in order (sort=recent) until a page holds no posting newer than --max-days-old, the
page count reaches --max-pages, or totalCount is exhausted. Expired postings, postings older than the
cutoff and titles outside the department title filter are dropped. Only full-time roles are asked
for (STEM OPT needs a W-2 employer). No network (DNS, refused, timeout) skips cleanly; an HTTP error
is raised so the pipeline records it.

CLI:  python himalayas.py [--root R] [--query "AI engineer" ...] [--max-pages 3] [--max-days-old 3]
                          [--today YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import socket
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (FetchError, Http, TitleFilter, default_title_filter, load_title_filter,  # noqa: E402
                       make_record, norm_employment, parse_ts, upsert_jobs)
from js_common import JS_ROOT, cli_date, html_to_text, normalize_company, short_hash, slugify  # noqa: E402

API = "https://himalayas.app/jobs/api/search"
SITE = "https://himalayas.app"
PLATFORM = "himalayas"
DEFAULT_QUERIES = ["AI engineer", "machine learning engineer", "LLM engineer", "applied AI engineer",
                   "AI agent engineer", "forward deployed engineer", "ML platform engineer"]
MAX_PAGES = 3
MAX_DAYS_OLD = 3
SHOWN_COUNTRIES = 3
_OFFLINE = re.compile(r"NameResolution|Name or service not known|nodename nor servname|Temporary failure in name "
                      r"resolution|Failed to establish a new connection|Connection(?:Error| refused| reset| aborted)|"
                      r"Network is unreachable|No route to host|timed out|getaddrinfo|offline", re.I)


def run_now(day: date | None) -> datetime:
    """The run clock: the wall clock for today (or no --today), else noon UTC on that day."""
    if day is None or day == date.today():
        return datetime.now(timezone.utc)
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)


def is_offline_error(exc: BaseException) -> bool:
    """True for 'no network' failures (DNS, refused, unreachable, timeout), false for HTTP errors."""
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return True
    return isinstance(exc, (FetchError, OSError)) and bool(_OFFLINE.search(str(exc)))


def search(http: Any, q: str, page: int = 1, country: str = "US", employment_type: str = "Full Time",
           sort: str = "recent") -> dict[str, Any]:
    params = {"q": q, "country": country, "employment_type": employment_type, "sort": sort, "page": page}
    return http.get_json(API, params=params) or {}


def _country(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("alpha2") or "").strip()
    return str(value or "").strip()


def location_text(restrictions: Iterable[Any] | None) -> str:
    """'Remote (United States, Canada)'; the US first; at most three names, then '+N more'."""
    names = [n for n in (_country(r) for r in restrictions or []) if n]
    if not names:
        return "Remote (worldwide)"
    us = [n for n in names if n.lower() in ("united states", "usa", "us", "united states of america")]
    ordered = us[:1] + [n for n in names if n not in us]
    shown = ordered[:SHOWN_COUNTRIES]
    more = len(ordered) - len(shown)
    return "Remote (" + ", ".join(shown) + (f", +{more} more" if more else "") + ")"


def normalize(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", str(job.get("title") or "")).strip()
    company = re.sub(r"\s+", " ", str(job.get("companyName") or "")).strip() or "Unknown"
    slug = slugify(normalize_company(company)) or slugify(company)
    url = str(job.get("guid") or job.get("applicationLink") or "").strip()
    apply_url = str(job.get("applicationLink") or url).strip()
    period = str(job.get("salaryPeriod") or "annual").lower()
    lo, hi = job.get("minSalary"), job.get("maxSalary")
    comp = ((int(lo) if lo else None, int(hi) if hi else None, job.get("currency") or "USD")
            if period == "annual" and (lo or hi) else (None, None, None))
    jd_text = html_to_text(str(job.get("description") or "")) or str(job.get("excerpt") or "")
    rec = make_record({"name": company, "slug": slug}, title=title, source="himalayas", source_url=url,
                      apply_url=apply_url, ats_vendor="unknown", posted_at=job.get("pubDate"),
                      location=location_text(job.get("locationRestrictions")), remote="remote", comp=comp,
                      employment_type=norm_employment(job.get("employmentType")), jd_text=jd_text, now=now)
    m = re.search(r"-(\d{6,})/?$", url)
    rec["id"] = f"{slug}-him-{m.group(1) if m else short_hash(url, n=8)}"
    rec["platform"] = PLATFORM
    rec["_body"] = rec["_body"].replace(
        f"- Apply: {apply_url}\n",
        f"- Apply: {apply_url}\n- Via: Himalayas ({SITE}) public jobs API; credit and link back kept per its API terms\n", 1)
    return rec


def collect(http: Any, now: datetime, queries: list[str] | None = None, title_filter: TitleFilter | None = None,
            max_pages: int = MAX_PAGES, max_days_old: int = MAX_DAYS_OLD,
            country: str = "US") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    qs = list(queries or DEFAULT_QUERIES)
    tf = title_filter or default_title_filter()
    cutoff = now - timedelta(days=max_days_old)
    merged: dict[str, dict[str, Any]] = {}
    info: dict[str, Any] = {"queries": len(qs), "requests": 0, "results": 0, "kept": 0, "stale": 0,
                            "expired": 0, "filtered": 0, "notes": []}
    for q in qs:
        for page in range(1, max(1, max_pages) + 1):
            try:
                data = search(http, q, page=page, country=country)
            except Exception as exc:
                if not is_offline_error(exc):
                    raise
                if info["requests"] == 0:
                    return [], dict(info, skipped=f"no network: {exc}")
                info["notes"].append(f"network lost after {info['requests']} requests: {exc}")
                info["kept"] = len(merged)
                return list(merged.values()), info
            info["requests"] += 1
            jobs = data.get("jobs") or []
            fresh_on_page = 0
            for job in jobs:
                info["results"] += 1
                pub = parse_ts(job.get("pubDate"))
                expiry = parse_ts(job.get("expiryDate"))
                fresh = pub is not None and pub >= cutoff
                fresh_on_page += int(fresh)
                if expiry is not None and expiry < now:
                    info["expired"] += 1
                elif not fresh:
                    info["stale"] += 1
                elif not tf.matches(str(job.get("title") or "")):
                    info["filtered"] += 1
                else:
                    rec = normalize(job, now)
                    merged.setdefault(rec["id"], rec)
            total, limit = int(data.get("totalCount") or 0), int(data.get("limit") or 20)
            if not jobs or fresh_on_page == 0 or page * limit >= total:
                break
    info["kept"] = len(merged)
    return list(merged.values()), info


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--query", action="append", default=[], help="search text (repeatable); default: built-in titles")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--max-days-old", type=int, default=MAX_DAYS_OLD)
    ap.add_argument("--today", type=cli_date, default=None, help="run date YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or run_now(args.today)
    records, info = collect(http or Http(min_interval=1.0), now, queries=args.query or None,
                            title_filter=load_title_filter(args.root), max_pages=args.max_pages,
                            max_days_old=args.max_days_old)
    if info.get("skipped"):
        print(f"himalayas skipped: {info['skipped']}")
        return 0
    print(f"himalayas: {info['queries']} queries, {info['requests']} requests, {info['results']} results, "
          f"{info['kept']} kept ({info['stale']} stale, {info['expired']} expired, {info['filtered']} off-title)")
    for note in info["notes"]:
        print(f"note: {note}")
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"{mode}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
