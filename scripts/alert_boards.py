#!/usr/bin/env python3
"""alert_boards.py: employers named in job-alert cards -> their public Greenhouse, Ashby or Lever board -> proposals.

Almost every job starts on an employer ATS board, and the periodic board poll (fetch_ats.py) is the most reliable
channel for fresh postings.
Alert mail (LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, Jobright, Wellfound, Indeed ...) names many employers that
targets/companies.yaml does not watch. This script finds their boards so the poll can read them:

  1. Employers: the job ids of the alert ledger (state/job-alerts.jsonl, parsed and partial rows of the last --days),
     read from jobs/<id>.md: company, company_slug, title, platform, links. Kept: not on the watchlist (slug, name,
     the name without form words such as "Inc", "Services", "AI", or board token), not an undisclosed or agency name
     ("Stealth Startup", "Confidential", "... Staffing"), and at least --min-fit cards whose title passes the
     department's title filter (an employer that only posts off-target roles is not worth a lookup). Ranked by those
     on-title cards, then by all cards.
  2. A link first: an alert card that already links the employer's own Greenhouse, Ashby or Lever page names the token
     (the yc_boards.py board-link reader); one call to the vendor's public API checks it.
  3. Guessed slugs last (the yc_boards.py method): the company slug, the slug without dashes, the slug without a
     trailing "ai", "labs", "hq", "inc", "technologies" word, each against the vendors' keyless public APIs
     (Ashby posting-api, Greenhouse boards-api, Lever postings). Breadth first: every employer's first guess before
     any employer's second, so a small --max-lookups budget covers the top employers. A guess counts only when its
     board lists at least one job. It is `proposed` (confirmed) when the board lists a job whose title is the title of
     one of this employer's alert cards (the same company posts the same role), else `to_confirm` (a board under that
     name exists, but it could be another company of the same name: seen in yc_boards.py with a common one-word name).

Nothing here edits targets/companies.yaml. With --write, each board found becomes one row appended to the add-only list
state/proposals/companies-new.jsonl (with the companies.yaml entry text a person appends, unverified, tier good), the
run is listed in state/review/alert-boards-<date>.md, and employers with no board are cached in state/alert_boards.json
(not probed again for NO_BOARD_DAYS). An employer already proposed is not proposed again. A dry run (the default) writes
nothing and prints what it would propose. Card text is untrusted: names are quoted, tokens must match TOKEN.
Sequential requests with fetch_ats.Http's per-host delay; at most --max-lookups API calls.

CLI:  python3 scripts/alert_boards.py [--root R] [--days 30] [--max-lookups 60] [--limit N] [--min-fit 1]
                                      [--today YYYY-MM-DD] [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import FetchError, Http, TitleFilter, load_companies, load_title_filter  # noqa: E402
from js_common import JS_ROOT, append_jsonl, cli_date, fm, normalize_company, read_jsonl, slugify, today  # noqa: E402
from yc_boards import API, BOARD_URL, board_links  # noqa: E402

VENDORS = ("ashby", "greenhouse", "lever")           # yc_boards.py order
LEDGER_STATUSES = ("parsed", "partial")
NO_BOARD_DAYS = 14
DAYS = 30
MAX_LOOKUPS = 60
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")
TRAILING = re.compile(r"-(?:ai|labs?|hq|inc|technologies|technology|tech|software|systems|io|app)$")
UNDISCLOSED = re.compile(r"\b(?:stealth|confidential|undisclosed|anonymous|jobs via|hiring for|recruit\w*|staffing|"
                         r"talent|consult\w*|placement|search partners?|headhunt\w*|manpower)\b", re.I)
# Words that name a company's form, not the company: "Acme.com Services LLC", "ACME INDUSTRIES", "Acme AI" and
# "The Acme Company (Corporate)" are all Acme on the watchlist.
GENERIC = {"the", "com", "services", "service", "technologies", "technology", "tech", "industries", "software",
           "systems", "solutions", "group", "holdings", "corporate", "labs", "lab", "ai", "hq", "us", "u", "s", "usa",
           "company", "international", "global", "io", "app", "inc", "llc", "corp"}
SOURCE = "alert_boards"


# =============================================================================== employers
@dataclass
class Employer:
    key: str                                      # normalize_company(name)
    name: str
    slug: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    fit: int = 0                                  # cards whose title passes the title filter
    platforms: set[str] = field(default_factory=set)
    links: list[tuple[str, str]] = field(default_factory=list)       # (vendor, token) read from the cards' links

    def titles(self) -> list[str]:
        return list(dict.fromkeys(str(c.get("title") or "") for c in self.cards if c.get("title")))


def _job(root: Path, job_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[\w.-]{1,200}", job_id or ""):
        return None
    path = Path(root) / "jobs" / f"{job_id}.md"
    if not path.is_file():
        return None
    try:
        meta, _ = fm.load(path)
    except Exception:                             # a malformed record is skipped, never fatal
        return None
    return dict(meta, id=job_id) if isinstance(meta, dict) else None


def _at(row: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(row.get("at") or "").replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except ValueError:
        return None


def alert_job_ids(root: Path, day: date, days: int = DAYS) -> list[str]:
    """Job ids of the ledger's parsed and partial rows from the last `days` days (each once, newest run first)."""
    since = day - timedelta(days=days)
    out: dict[str, None] = {}
    for row in reversed(read_jsonl(Path(root) / "state" / "job-alerts.jsonl")):
        when = _at(row)
        if row.get("status") not in LEDGER_STATUSES or when is None or not since <= when <= day:
            continue
        for jid in row.get("jobs") or []:
            out.setdefault(str(jid), None)
    return list(out)


def core(name: str) -> str:
    """The company's name words without its form words (GENERIC) and bracketed asides, dash-joined ('' when none)."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"\([^)]*\)", " ", str(name or "")).lower())
    return "-".join(w for w in words if w not in GENERIC)


def _known(companies: list[dict[str, Any]]) -> tuple[set[str], set[tuple[str, str]]]:
    keys = {str(c.get("slug") or "").lower() for c in companies} | {normalize_company(str(c.get("name") or ""))
                                                                   for c in companies}
    keys |= {core(str(c.get("name") or "")) for c in companies} | {core(str(c.get("slug") or "")) for c in companies}
    keys |= {k.replace(" ", "-") for k in keys}
    tokens = {(str(c.get("ats_vendor") or "").lower(), str(c.get("board_token") or "").lower())
              for c in companies if c.get("board_token")}
    return keys - {""}, tokens


def employers(root: Path, day: date, days: int = DAYS, companies: list[dict[str, Any]] | None = None,
              title_filter: TitleFilter | None = None, min_fit: int = 1) -> tuple[list[Employer], dict[str, int]]:
    """(employers not on the watchlist with at least `min_fit` on-title alert cards, ranked; counts of what was skipped
    and why)."""
    companies = load_companies(root) if companies is None else companies
    tf = title_filter or load_title_filter(root)
    keys, _ = _known(companies)
    by_key: dict[str, Employer] = {}
    skipped = {"jobs": 0, "missing_records": 0, "watched": 0, "undisclosed": 0, "off_title_employers": 0}
    for jid in alert_job_ids(root, day, days):
        skipped["jobs"] += 1
        rec = _job(root, jid)
        if rec is None:
            skipped["missing_records"] += 1
            continue
        name = re.sub(r"\s+", " ", str(rec.get("company") or "")).strip()
        key = normalize_company(name)
        slug = slugify(key) if key else ""
        if not key or not slug or slug == "unknown":
            continue
        if {key, slug, core(name), str(rec.get("company_slug") or "").lower()} & keys:
            skipped["watched"] += 1
            continue
        if UNDISCLOSED.search(name):
            skipped["undisclosed"] += 1
            continue
        emp = by_key.setdefault(key, Employer(key, name[:120], slug))
        title = str(rec.get("title") or "")
        emp.cards.append({"id": jid, "title": title, "platform": rec.get("platform")})
        emp.fit += int(bool(title) and tf.matches(title))
        if rec.get("platform"):
            emp.platforms.add(str(rec["platform"]))
        for url in (rec.get("source_url"), rec.get("apply_url")):
            for vendor, token in board_links(str(url or "")):
                if TOKEN.fullmatch(token) and (vendor, token) not in emp.links:
                    emp.links.append((vendor, token))
    kept = [e for e in by_key.values() if e.fit >= min_fit]
    skipped["off_title_employers"] = len(by_key) - len(kept)
    return sorted(kept, key=lambda e: (-e.fit, -len(e.cards), e.key)), skipped


def guesses(emp: Employer) -> list[str]:
    """Slug guesses, most likely first: the name's letters run together ("exampleai", "acmetechnologies"), the name
    without its form words ("arkham"), then the dashed slug and the slug without a trailing ai/labs/hq word."""
    out: list[str] = []
    base = slugify(normalize_company(re.sub(r"\([^)]*\)", " ", emp.name))) or emp.slug
    trimmed, short = TRAILING.sub("", base), core(emp.name)
    for s in (base.replace("-", ""), short.replace("-", ""), base, trimmed.replace("-", ""), short):
        if s and TOKEN.fullmatch(s) and s not in out:
            out.append(s)
    return out


# =============================================================================== boards
def board_titles(vendor: str, token: str, http: Any) -> list[str] | None:
    """The job titles a vendor's public API lists for this board, or None when there is no such board."""
    url = API[vendor].format(quote(token, safe=""))
    try:
        data = http.get_json(url, params={"mode": "json"} if vendor == "lever" else None)
    except (FetchError, ValueError):
        return None
    if vendor == "lever":
        jobs, key = (data if isinstance(data, list) else None), "text"
    else:
        jobs, key = (data.get("jobs") if isinstance(data, dict) else None), "title"
    if not isinstance(jobs, list):
        return None
    return [str(j.get(key) or "") for j in jobs if isinstance(j, dict)]


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def matching_title(emp: Employer, titles: Iterable[str]) -> str | None:
    """An alert card title of this employer that the board lists (word for word, case and punctuation aside)."""
    listed = {_words(t) for t in titles if t}
    return next((t for t in emp.titles() if _words(t) and _words(t) in listed), None)


@dataclass
class Found:
    emp: Employer
    vendor: str
    token: str
    how: str                                      # alert-link | guess
    jobs: int
    evidence: str | None                          # the alert card title the board also lists
    status: str                                   # proposed | to_confirm


@dataclass
class Resolution:
    found: list[Found] = field(default_factory=list)
    no_board: list[Employer] = field(default_factory=list)       # every planned lookup made, none a board
    not_reached: list[Employer] = field(default_factory=list)    # the lookup budget ended first
    watched: list[Employer] = field(default_factory=list)        # its card links a board already polled
    lookups: int = 0


def plan_for(emp: Employer) -> list[list[tuple[str, str, str]]]:
    """Rounds of (vendor, token, how): the cards' own board links, then one round per guess (that slug on each vendor)."""
    rounds = [[(v, t, "alert-link") for v, t in emp.links]] if emp.links else []
    for g in guesses(emp):
        rnd = [(v, g, "guess") for v in VENDORS if (v, g) not in emp.links]
        if rnd:
            rounds.append(rnd)
    return rounds


def resolve(emps: list[Employer], http: Any, max_lookups: int = MAX_LOOKUPS,
            watched_tokens: set[tuple[str, str]] | None = None) -> Resolution:
    """Links first (one call each), then guesses breadth first (every employer's round n, a slug on each vendor, before
    anyone's round n+1), until every employer is settled or `max_lookups` calls are spent."""
    res = Resolution()
    watched = set(watched_tokens or set())
    plans = {e.key: plan_for(e) for e in emps}
    tried = {e.key: 0 for e in emps}
    open_ = {e.key: e for e in emps}
    for e in emps:
        if any((v, t.lower()) in watched for rnd in plans[e.key] for v, t, how in rnd if how == "alert-link"):
            res.watched.append(e)                 # the board is polled already, under another name
            open_.pop(e.key)
    step = 0
    while open_ and res.lookups < max_lookups and any(len(plans[k]) > step for k in open_):
        for key in list(open_):
            if len(plans[key]) <= step:
                continue
            if res.lookups >= max_lookups:
                break
            emp = open_[key]
            for vendor, token, how in plans[key][step]:
                if res.lookups >= max_lookups:
                    break
                tried[key] += 1
                if (vendor, token.lower()) in watched:
                    continue                      # a guess that names a polled board is another company's
                res.lookups += 1
                titles = board_titles(vendor, token, http)
                if titles is None or (how == "guess" and not titles):
                    continue
                evidence = matching_title(emp, titles)
                status = "proposed" if (how == "alert-link" or evidence) else "to_confirm"
                res.found.append(Found(emp, vendor, token, how, len(titles), evidence, status))
                watched.add((vendor, token.lower()))
                open_.pop(key)
                break
        step += 1
    for key, emp in open_.items():
        (res.no_board if tried[key] >= sum(len(r) for r in plans[key]) else res.not_reached).append(emp)
    return res


# =============================================================================== proposals
def entry_text(f: Found, day: date) -> str:
    """The companies.yaml entry a person appends (same shape as yc_boards.py's unverified entries)."""
    where = (f"the alert card linked it" if f.how == "alert-link" else f"guessed slug {f.token}")
    seen = (f"; it lists the alert title {json.dumps(f.evidence)}" if f.evidence else "")
    notes = (f"Named in {len(f.emp.cards)} job-alert card(s) on {', '.join(sorted(f.emp.platforms)) or 'alert mail'} "
             f"(e.g. {', '.join(c['id'] for c in f.emp.cards[:3])}). Board found by scripts/alert_boards.py "
             f"({f.how}) on {day.isoformat()}; a person confirms it is this company, then the comment line reads verified.")
    lines = [
        f"  # unverified: proposed by scripts/alert_boards.py {day.isoformat()} ({where}; the {f.vendor} API listed "
        f"{f.jobs} jobs{seen})",
        f"  - name: {json.dumps(f.emp.name)}",
        f"    slug: {json.dumps(f.emp.slug)}",
        "    tier: good",
        "    verified: false",
        f"    ats_vendor: {f.vendor}",
        f"    board_token: {json.dumps(f.token)}",
        f"    careers_url: {json.dumps(BOARD_URL[f.vendor].format(quote(f.token, safe='')))}",
        "    sponsorship_stance: unknown",
        "    h1b_approvals_recent: null",
        f"    notes: {json.dumps(notes)}",
    ]
    return "\n".join(lines) + "\n"


def proposal_row(f: Found, day: date) -> dict[str, Any]:
    return {"date": day.isoformat(), "slug": f.emp.slug, "name": f.emp.name, "ats_vendor": f.vendor,
            "board_token": f.token, "careers_url": BOARD_URL[f.vendor].format(quote(f.token, safe="")),
            "jobs": f.jobs, "how": f.how, "status": f.status, "evidence": f.evidence,
            "alert_jobs": [c["id"] for c in f.emp.cards[:10]], "platforms": sorted(f.emp.platforms),
            "source": SOURCE, "entry": entry_text(f, day)}


def proposals_path(root: Path) -> Path:
    return Path(root) / "state" / "proposals" / "companies-new.jsonl"


def cache_path(root: Path) -> Path:
    return Path(root) / "state" / "alert_boards.json"


def review_path(root: Path, day: date) -> Path:
    return Path(root) / "state" / "review" / f"alert-boards-{day:%Y-%m-%d}.md"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def review_text(day: date, res: Resolution, skipped: dict[str, int], candidates: int) -> str:
    lines = [f"## Run {day.isoformat()}", "",
             f"{candidates} alert employers not on the watchlist; {res.lookups} API lookups; "
             f"{sum(f.status == 'proposed' for f in res.found)} proposed, "
             f"{sum(f.status == 'to_confirm' for f in res.found)} to confirm, {len(res.no_board)} no board, "
             f"{len(res.not_reached)} not reached (budget), {len(res.watched)} linking a board already polled. "
             f"Skipped: {skipped}.", ""]
    for f in res.found:
        lines.append(f"- {f.status}: {f.emp.name} -> {f.vendor}:{f.token} ({f.how}, {f.jobs} jobs"
                     + (f", lists \"{f.evidence}\"" if f.evidence else ", no alert title on the board") + ")")
    if res.found:
        lines += ["", "Entries to append to targets/companies.yaml after a person checks each board is this company:",
                  "", "```yaml"] + [entry_text(f, day).rstrip("\n") for f in res.found] + ["```"]
    return "\n".join(lines) + "\n"


def run(root: Path, http: Any, day: date, write: bool = False, days: int = DAYS, max_lookups: int = MAX_LOOKUPS,
        limit: int | None = None, title_filter: TitleFilter | None = None, min_fit: int = 1) -> dict[str, Any]:
    root = Path(root)
    companies = load_companies(root)
    emps, skipped = employers(root, day, days, companies, title_filter, min_fit)
    try:
        cache = json.loads(cache_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    no_board: dict[str, str] = dict(cache.get("no_board") or {})
    proposed = {str(r.get("slug")) for r in read_jsonl(proposals_path(root))}
    fresh: list[Employer] = []
    cached = earlier = 0
    for e in emps:
        if e.slug in proposed:
            earlier += 1
            continue
        seen = no_board.get(e.slug)
        if seen and date.fromisoformat(seen) > day - timedelta(days=NO_BOARD_DAYS):
            cached += 1
            continue
        fresh.append(e)
    if limit is not None:
        fresh = fresh[:limit]
    _, tokens = _known(companies)
    res = resolve(fresh, http, max_lookups, tokens)
    rows = [proposal_row(f, day) for f in res.found]
    summary = {"alert_jobs": skipped["jobs"], "employers_not_watched": len(emps), "skipped": skipped,
               "already_proposed": earlier, "cached_no_board": cached, "probed_employers": len(fresh),
               "lookups": res.lookups, "proposed": sum(f.status == "proposed" for f in res.found),
               "to_confirm": sum(f.status == "to_confirm" for f in res.found), "no_board": len(res.no_board),
               "not_reached": len(res.not_reached), "board_already_watched": len(res.watched), "written": {},
               "dry_run": not write,
               "found": [{k: r[k] for k in ("name", "ats_vendor", "board_token", "jobs", "how", "status", "evidence")}
                         for r in rows]}
    if write:
        for r in rows:
            append_jsonl(proposals_path(root), r)
        for e in res.no_board:
            no_board[e.slug] = day.isoformat()
        _atomic_write(cache_path(root), json.dumps({"no_board": dict(sorted(no_board.items()))}, indent=1) + "\n")
        path = review_path(root, day)
        head = (f"# Alert employers and their boards, {day.isoformat()}\n\nWritten by scripts/alert_boards.py. Nothing "
                "here is on the watchlist yet: each board was found on the vendor's public API; a person checks it is "
                "the same company, then appends its entry to targets/companies.yaml. Names come from alert cards "
                "(untrusted data).\n\n")
        old = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" if path.is_file() else head
        _atomic_write(path, old + review_text(day, res, skipped, len(emps)))
        summary["written"] = {"proposals": str(proposals_path(root)) if rows else None, "review": str(path),
                              "cache": str(cache_path(root))}
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--days", type=int, default=DAYS, help="ledger rows from the last N days")
    ap.add_argument("--max-lookups", type=int, default=MAX_LOOKUPS, help="vendor API calls at most")
    ap.add_argument("--limit", type=int, help="employers probed at most")
    ap.add_argument("--min-fit", type=int, default=1, help="on-title alert cards an employer needs (0: every employer)")
    ap.add_argument("--today", type=cli_date)
    ap.add_argument("--write", action="store_true", help="append proposals, write the review list and the cache")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    summary = run(args.root, Http(), args.today or today(), write=args.write, days=args.days,
                  max_lookups=args.max_lookups, limit=args.limit, min_fit=args.min_fit)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    for f in summary["found"]:
        print(f"{f['status']}: {f['name']} -> {f['ats_vendor']}:{f['board_token']} ({f['how']}, {f['jobs']} jobs"
              + (f", lists {json.dumps(f['evidence'])})" if f["evidence"] else ")"))
    mode = "written" if args.write else "dry-run (nothing written)"
    print(f"alert_boards {mode}: " + ", ".join(f"{k} {v}" for k, v in summary.items()
                                                if k not in ("found", "written", "skipped", "dry_run")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
