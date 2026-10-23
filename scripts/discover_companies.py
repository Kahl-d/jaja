#!/usr/bin/env python3
"""discover_companies.py - new companies found deterministically.

For each company that posted in jobs/ but is not in targets/companies.yaml (scout_coverage.py's new_companies, or
--slugs), find its public ATS board and count its roles:
  1. the token a job URL already names (jobs.ashbyhq.com/<token>/, boards.greenhouse.io/<token>/,
     job-boards.greenhouse.io/<token>/, jobs.lever.co/<token>/): one request, no guessing;
  2. else name probes: the slug, then the slug without hyphens, each against Greenhouse, Lever and Ashby in that
     order (the public board APIs fetch_ats.py already reads); the first board that answers wins. A 404 is "not
     there"; any other failure is noted on the row and the probes go on.
Roles are every listed posting; AI-title roles are those the department title filter (fetch_ats.load_title_filter)
keeps. The candidates go to state/reports/new-companies-<date>.md, most AI-title roles first, each with the
companies.yaml entry the Recruiter would add. Nothing is ever added automatically: the Recruiter adds an entry by
hand and sets its segment through `segment_companies.py set`.

CLI:  python3 scripts/discover_companies.py [--root R] [--today D] [--slugs a,b] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import FetchError, Http, TitleFilter, load_jobs, load_title_filter  # noqa: E402
from js_common import JS_ROOT, cli_date, today  # noqa: E402

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER = "https://api.lever.co/v0/postings/{token}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{token}"
VENDORS = (("greenhouse", GREENHOUSE), ("lever", LEVER), ("ashby", ASHBY))
URL_TOKEN = [("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)", re.I)),
             ("greenhouse", re.compile(r"^https?://(?:job-)?boards\.greenhouse\.io/([^/?#]+)", re.I)),
             ("lever", re.compile(r"^https?://jobs\.lever\.co/([^/?#]+)", re.I))]


def _titles(vendor: str, data: Any) -> list[str]:
    if vendor == "lever":
        return [str(p.get("text") or "") for p in data or [] if isinstance(p, dict)]
    jobs = (data or {}).get("jobs") or [] if isinstance(data, dict) else []
    return [str(j.get("title") or "") for j in jobs if isinstance(j, dict) and j.get("isListed") is not False]


def _get(http: Any, vendor: str, token: str) -> list[str] | None:
    """The board's role titles, or None when the board is not there (404). Other failures raise FetchError."""
    url = dict(VENDORS)[vendor].format(token=token)
    try:
        data = http.get_json(url, params={"mode": "json"}) if vendor == "lever" else http.get_json(url)
    except FetchError as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise
    return _titles(vendor, data)


def url_token(jobs: list[dict[str, Any]]) -> tuple[str, str] | None:
    for job in jobs:
        for field in ("source_url", "apply_url"):
            for vendor, rx in URL_TOKEN:
                m = rx.match(str(job.get(field) or ""))
                if m and m.group(1).lower() not in ("embed", "jobs"):
                    return vendor, m.group(1)
    return None


def watchlist_slugs(root: Path) -> set[str]:
    try:
        data = yaml.safe_load((root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return set()
    return {str(c.get("slug")) for c in data.get("companies") or [] if isinstance(c, dict) and c.get("slug")}


def discover(root: Path, slugs: list[str], http: Any, title_filter: TitleFilter) -> list[dict[str, Any]]:
    """One row per company not on the watchlist: its board (vendor, token), role counts and how it was found."""
    known = watchlist_slugs(root)
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for job in load_jobs(root).values():
        by_slug.setdefault(str(job.get("company_slug") or ""), []).append(job)
    rows = []
    for slug in slugs:
        if slug in known:
            continue
        jobs = by_slug.get(slug, [])
        row: dict[str, Any] = {"slug": slug, "company": next((str(j["company"]) for j in jobs if j.get("company")), slug),
                               "postings": len(jobs), "vendor": None, "token": None, "roles": None, "ai_roles": None,
                               "found_by": None, "note": ""}
        hint = url_token(jobs)
        tries = [(hint[0], hint[1], "job URL")] if hint else []
        if not hint:
            tokens = list(dict.fromkeys([slug, slug.replace("-", "")]))
            tries = [(vendor, token, "name probe") for token in tokens for vendor, _ in VENDORS]
        errors = []
        for vendor, token, how in tries:
            try:
                titles = _get(http, vendor, token)
            except FetchError as exc:
                errors.append(f"{vendor} {token}: {str(exc).splitlines()[0][:80]}")
                continue
            if titles is not None:
                row.update(vendor=vendor, token=token, roles=len(titles), found_by=how,
                           ai_roles=sum(1 for t in titles if title_filter.matches(t)))
                break
        if row["vendor"] is None:
            row["note"] = "no public board found (probed " + ", ".join(f"{v} {t}" for v, t, _ in tries) + ")"
        if errors:
            row["note"] = (row["note"] + "; " if row["note"] else "") + "errors: " + "; ".join(errors)
        rows.append(row)
    rows.sort(key=lambda r: (-(r["ai_roles"] or -1), -(r["roles"] or -1), -r["postings"], r["slug"]))
    return rows


SHOWN = {"job URL": "job URL", "name probe": "name probe: verify it is this company"}   # a shared name can be another


def render(rows: list[dict[str, Any]], day: date) -> str:
    found = [r for r in rows if r["vendor"]]
    lines = [f"# New companies {day.isoformat()}: {len(found)} boards found for {len(rows)} companies not on the "
             "watchlist", "",
             "Built by scripts/discover_companies.py from the companies that posted in jobs/ but are not in "
             "targets/companies.yaml. Candidates only: never added automatically. The Recruiter adds an entry by "
             "hand and sets its segment with `python3 scripts/segment_companies.py set <slug> <segment> --source "
             "owner`.", "",
             "| slug | company | postings in jobs/ | board | roles | AI-title roles | found by | entry to add |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        board = f"{r['vendor']} `{r['token']}`" if r["vendor"] else "none"
        entry = (f"`{{name: \"{r['company']}\", slug: {r['slug']}, ats_vendor: {r['vendor']}, board_token: "
                 f"\"{r['token']}\"}}`" if r["vendor"] else r["note"])
        lines.append(f"| {r['slug']} | {r['company']} | {r['postings']} | {board} | "
                     f"{'' if r['roles'] is None else r['roles']} | {'' if r['ai_roles'] is None else r['ai_roles']} | "
                     f"{SHOWN.get(r['found_by'], '')} | {entry} |")
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    os.write(fd, text.encode("utf-8"))
    os.close(fd)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv: list[str] | None = None, http: Any = None, title_filter: TitleFilter | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--slugs", default=None, help="comma list instead of scout_coverage's new_companies")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    day = args.today or today()
    if args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        import scout_coverage
        slugs = [c["slug"] for c in scout_coverage.report(day, args.root)["new_companies"]]
    rows = discover(args.root, slugs[:args.limit], http or Http(), title_filter or load_title_filter(args.root))
    text = render(rows, day)
    if args.dry_run:
        print(text, end="")
        return 0
    path = args.root / "state" / "reports" / f"new-companies-{day.isoformat()}.md"
    write(path, text)
    print(f"wrote {path.relative_to(args.root)}: {sum(1 for r in rows if r['vendor'])} boards for {len(rows)} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
