#!/usr/bin/env python3
"""Adzuna job search -> job records (design §4; source: adzuna). Free key, ~250 calls/day.

  GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
      ?app_id&app_key&what&where&distance&results_per_page&max_days_old&sort_by&content-type

Keys come from the environment (ADZUNA_APP_ID, ADZUNA_APP_KEY); without them the source is
skipped cleanly (exit 0). Keys are never written anywhere: any query parameter in a result URL
that carries a key value is removed before the record is stored. Titles pass the department title
filter; predicted salaries (salary_is_predicted=1) are not reported as comp. Adzuna ids repeat
across queries, so records merge by id (<company_slug>-adz-<adzuna id>).

CLI:  python adzuna.py [--root R] [--query "what" ...] [--where PLACE] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (Http, TitleFilter, default_title_filter, load_title_filter,  # noqa: E402
                       make_record, norm_employment, remote_mode, upsert_jobs)
from js_common import JS_ROOT, html_to_text, normalize_company, slugify  # noqa: E402

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
CURRENCY = {"us": "USD", "gb": "GBP", "ca": "CAD", "de": "EUR", "fr": "EUR", "nl": "EUR"}
DEFAULT_QUERIES: list[dict[str, str]] = [
    {"what": "machine learning engineer", "where": "San Francisco"},
    {"what": "AI engineer", "where": "San Francisco"},
    {"what": "LLM engineer"},
    {"what": "applied AI engineer"},
    {"what": "forward deployed engineer"},
    {"what": "ML platform engineer"},
    {"what": "agentic AI engineer"},
    {"what": "machine learning engineer remote"},
]


def credentials(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    env = os.environ if env is None else env
    app_id, app_key = (env.get("ADZUNA_APP_ID") or "").strip(), (env.get("ADZUNA_APP_KEY") or "").strip()
    return (app_id, app_key) if app_id and app_key else None


def _strip_tags(text: Any) -> str:
    return re.sub(r"\s+", " ", html_to_text(str(text or ""))).strip()


def scrub_url(url: str, secrets: Iterable[str]) -> str:
    """Drop query parameters whose value contains a secret (Adzuna echoes the app id)."""
    secrets = [s for s in secrets if s]
    if not url or not secrets:
        return url
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not any(s in v for s in secrets)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def search(http: Any, app_id: str, app_key: str, what: str, where: str | None = None, page: int = 1,
           country: str = "us", results_per_page: int = 50, max_days_old: int = 3,
           distance: int = 80) -> dict[str, Any]:
    params: dict[str, Any] = {"app_id": app_id, "app_key": app_key, "what": what}
    if where:
        params.update({"where": where, "distance": distance})
    params.update({"results_per_page": results_per_page, "max_days_old": max_days_old,
                   "sort_by": "date", "content-type": "application/json"})
    return http.get_json(API.format(country=country, page=page), params=params) or {}


def normalize(res: dict[str, Any], now: datetime, secrets: Iterable[str] = (),
              currency: str = "USD") -> dict[str, Any]:
    title = _strip_tags(res.get("title"))
    company = _strip_tags((res.get("company") or {}).get("display_name")) or "Unknown"
    slug = slugify(normalize_company(company)) or slugify(company)
    location = _strip_tags((res.get("location") or {}).get("display_name")) or None
    predicted = str(res.get("salary_is_predicted", "0")).strip().lower() in ("1", "true")
    lo, hi = res.get("salary_min"), res.get("salary_max")
    comp = (None, None, None) if predicted or not (lo or hi) else (
        int(round(lo)) if lo else None, int(round(hi)) if hi else None, currency)
    url = scrub_url(str(res.get("redirect_url") or ""), secrets)
    rec = make_record(
        {"name": company, "slug": slug}, title=title, source="adzuna", source_url=url, apply_url=url,
        ats_vendor="unknown", posted_at=res.get("created"), location=location,
        remote=remote_mode(location, title), comp=comp,
        employment_type=norm_employment(res.get("contract_time") or res.get("contract_type")),
        jd_text=html_to_text(str(res.get("description") or "")), now=now)
    rec["id"] = f"{slug}-adz-{res.get('id')}"
    return rec


def collect(http: Any, now: datetime, env: Mapping[str, str] | None = None,
            queries: list[dict[str, str]] | None = None, title_filter: TitleFilter | None = None,
            country: str = "us") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    creds = credentials(env)
    if not creds:
        return [], {"skipped": "ADZUNA_APP_ID/ADZUNA_APP_KEY not set; adzuna skipped", "results": 0, "kept": 0}
    tf = title_filter or default_title_filter()
    merged: dict[str, dict[str, Any]] = {}
    total = 0
    for q in queries or DEFAULT_QUERIES:
        data = search(http, creds[0], creds[1], q["what"], where=q.get("where"), country=country)
        for res in data.get("results") or []:
            total += 1
            if not tf.matches(_strip_tags(res.get("title"))):
                continue
            rec = normalize(res, now, secrets=creds, currency=CURRENCY.get(country, "USD"))
            merged.setdefault(rec["id"], rec)
    return list(merged.values()), {"results": total, "kept": len(merged), "queries": len(queries or DEFAULT_QUERIES)}


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None,
         env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--query", action="append", default=[], help="'what' text (repeatable)")
    ap.add_argument("--where", default=None, help="location text for every --query")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if not credentials(env):
        print("adzuna skipped: ADZUNA_APP_ID/ADZUNA_APP_KEY not set")
        return 0
    queries = [{"what": q, **({"where": args.where} if args.where else {})} for q in args.query] or None
    records, info = collect(http or Http(), now, env=env, queries=queries,
                            title_filter=load_title_filter(args.root))
    print(f"adzuna: {info['queries']} queries, {info['results']} results, {info['kept']} kept")
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"{mode}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
