#!/usr/bin/env python3
"""AI Dev Jobs (aidevboard.com) public jobs API -> job records (source aidevjobs; platform targets/platforms.yaml id
aidevjobs census BUILD 1).

  GET https://aidevboard.com/api/v1/jobs?tags=<a,b>&type=full-time&limit=50&page=N

Terms (https://aidevboard.com/terms, last updated 2026-04-16, read 2026-10-01): "Public read endpoints are open and
free, with hourly throttles and abuse controls"; no scraping "beyond API rate limits"; API data "may be used in your
applications but may not be resold as a standalone dataset". So: a few tag queries once a day, one request per second,
an HTTP 429 stops the run, and every record keeps the AI Dev Jobs job page as source_url and names the board in its body.

Response (observed 2026-10-01): {page, per_page, total, has_next, jobs: [...]}; a job has id, title, company_name,
company_slug, location, workplace (remote | hybrid | onsite), remote_scope, job_type, experience_level, salary_min and
salary_max (USD a year, strings), published_at, expires_at, description (text), tags, apply_url (often the employer's
ATS) and url (the board's job page). Only full-time roles are asked for (STEM OPT needs a W-2 employer).

Per query: pages in order until has_next is false, a page holds nothing newer than --max-days-old, or --max-pages.
Expired, stale and off-title jobs are dropped. No network skips cleanly; an HTTP error is raised.

CLI:  python aidevjobs.py [--root R] [--tags "llm,agents" ...] [--max-pages 2] [--max-days-old 7]
                          [--today YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (Http, TitleFilter, default_title_filter, load_title_filter, make_record,  # noqa: E402
                       norm_employment, parse_ts, upsert_jobs)
from himalayas import is_offline_error, run_now  # noqa: E402
from js_common import JS_ROOT, cli_date, normalize_company, short_hash, slugify  # noqa: E402

API = "https://aidevboard.com/api/v1/jobs"
SITE = "aidevboard.com"
PLATFORM = "aidevjobs"
DEFAULT_QUERIES = ["llm,agents", "evals", "rag", "llm,python"]
MAX_PAGES = 2
MAX_DAYS_OLD = 7
PAGE_SIZE = 50
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


def _amount(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    company = str(job.get("company_name") or job.get("company_slug") or "unknown").strip()
    slug = str(job.get("company_slug") or "") or slugify(normalize_company(company)) or slugify(company)
    url = str(job.get("url") or f"https://{SITE}/job/{job.get('id')}")
    apply_url = str(job.get("apply_url") or url)
    lo, hi = _amount(job.get("salary_min")), _amount(job.get("salary_max"))
    rec = make_record({"name": company, "slug": slug}, title=str(job.get("title") or "").strip(), source="aidevjobs",
                      source_url=url, apply_url=apply_url, ats_vendor="unknown", posted_at=job.get("published_at"),
                      location=job.get("location") or None,
                      remote=WORKPLACE.get(str(job.get("workplace") or "").lower(), "unknown"),
                      comp=(lo, hi, "USD") if (lo or hi) else (None, None, None),
                      employment_type=norm_employment(job.get("job_type")), jd_text=str(job.get("description") or ""),
                      now=now)
    rec["id"] = f"{slug}-adj-{short_hash(str(job.get('id') or url), n=8)}"
    rec["platform"] = PLATFORM
    rec["_body"] = rec["_body"] + f"\n- Via: AI Dev Jobs ({SITE}) public jobs API; credit and link back kept per its terms\n"
    return rec


def collect(http: Any, now: datetime, queries: list[str] | None = None, title_filter: TitleFilter | None = None,
            max_pages: int = MAX_PAGES, max_days_old: int = MAX_DAYS_OLD) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    qs = list(queries or DEFAULT_QUERIES)
    tf = title_filter or default_title_filter()
    cutoff = now - timedelta(days=max_days_old)
    merged: dict[str, dict[str, Any]] = {}
    info: dict[str, Any] = {"queries": len(qs), "requests": 0, "results": 0, "kept": 0, "stale": 0, "expired": 0,
                            "filtered": 0, "not_full_time": 0, "notes": []}
    for q in qs:
        for page in range(1, max(1, max_pages) + 1):
            try:
                data = http.get_json(API, params={"tags": q, "type": "full-time", "limit": PAGE_SIZE, "page": page}) or {}
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
                pub, expiry = parse_ts(job.get("published_at")), parse_ts(job.get("expires_at"))
                fresh = pub is not None and pub >= cutoff
                fresh_on_page += int(fresh)
                if expiry is not None and expiry < now:
                    info["expired"] += 1
                elif not fresh:
                    info["stale"] += 1
                elif str(job.get("job_type") or "full-time").lower() != "full-time":
                    info["not_full_time"] += 1
                elif not tf.matches(str(job.get("title") or "")):
                    info["filtered"] += 1
                else:
                    rec = normalize(job, now)
                    merged.setdefault(rec["id"], rec)
            if not jobs or not data.get("has_next") or fresh_on_page == 0:
                break
    info["kept"] = len(merged)
    return list(merged.values()), info


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--tags", action="append", default=[], help="comma-separated tags (repeatable); default: built-in")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--max-days-old", type=int, default=MAX_DAYS_OLD)
    ap.add_argument("--today", type=cli_date, default=None, help="run date YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or run_now(args.today)
    records, info = collect(http or Http(min_interval=1.0), now, queries=args.tags or None,
                            title_filter=load_title_filter(args.root), max_pages=args.max_pages,
                            max_days_old=args.max_days_old)
    if info.get("skipped"):
        print(f"aidevjobs skipped: {info['skipped']}")
        return 0
    print(f"aidevjobs: {info['queries']} queries, {info['requests']} requests, {info['results']} results, {info['kept']} kept "
          f"({info['stale']} stale, {info['expired']} expired, {info['not_full_time']} not full-time, "
          f"{info['filtered']} off-title)")
    for note in info["notes"]:
        print(f"note: {note}")
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    print(f"{'dry-run (nothing written)' if args.dry_run else 'written'}: {len(plan['new'])} new, "
          f"{len(plan['updated'])} re-sighted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
