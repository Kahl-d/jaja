#!/usr/bin/env python3
"""finding_metrics.py: how complete and how early is our role finding.

The goal is a system that finds jobs better than anyone and gets interviews as fast as possible, and that
measures itself and improves. scout_coverage.py measures the boards we poll on one day; this report measures what the
boards miss and how early we act, over every record so far. It reads, never writes a record:

1. Coverage. The in-band roles we learn about from outside our boards: job-alert mail (jobs/*.md whose source ends in
   -alert: LinkedIn, ZipRecruiter, Glassdoor, Jobright and Google Jobs mail), roles recorded by hand from those
   platforms (source manual with an aggregator `platform`) and hiring posts (state/posts/records.jsonl). A role is one
   (employer, normalized title): a LinkedIn and a ZipRecruiter card for one opening, or two cities of one title, are
   one role. Each role is at a target (targets/companies.yaml, by slug, name or alias through company_index) or not;
   a target role is "on a board" when a polled board record (fetch_ats) carries the same employer and title. The gap
   is the share of roles at employers not on the watchlist, and the employers that would close most of it, ranked by
   in-band roles, each with its public board when one is known: a token in a job URL (ats_detect signatures),
   state/yc_boards.json, or, only with --probe-budget N, the public Greenhouse, Lever and Ashby board APIs that
   discover_companies.py reads (at most N requests in all; 0 by default, so the report makes no request).
2. Lead time. For each in-band role seen both on a polled board and in alert mail: hours from the board's first_seen
   to the alert's arrival (alert_received_at, else first_seen). Positive: the board saw it first. A board record first
   seen in its company's first sweep is marked: we were not polling that board before, so its lead is a lower bound.
   Also the days from the board's posted_at to the alert's arrival (how stale alert mail is).
3. Freshness at action. For every application with applied_at: hours from the job's first sighting (the earliest
   first_seen, alert_received_at or sightings first_seen in its duplicate group) to the applied event (tracker event
   time, else the applied_at day at noon in the department timezone, marked day-only), and the posting's age (posted_at) at apply time.

In band means: the employer passes fit_gates.gate_employer (staffing, recruiting, talent-marketplace and
unnamed-client roles out; computed now, so a record gated is still filtered), the title passes the
department title filter (fetch_ats.load_title_filter) and hunt_score.role_fit reaches ROLE_MIN (the hunt's role gate),
and the record's stored gate is not fail (seniority, location, citizenship). A hiring post must also be marked hiring
and in band by posts.py; a post with no named employer is counted, never ranked.

Usage: python3 scripts/finding_metrics.py [--root R] [--today YYYY-MM-DD] [--top 20] [--probe-budget 0] [--write]
       [--json]
--write saves state/reports/finding-<date>.md and finding-<date>.json (the same numbers, for the learning loop).
Exit 0 (a report, not a gate); 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunt_score  # noqa: E402
from ats_detect import detect  # noqa: E402
from company_index import CompanyIndex, extends_company  # noqa: E402
from dedup import normalize_location  # noqa: E402
from fetch_ats import SUPPORTED_VENDORS, FetchError, load_companies, load_jobs, load_title_filter, parse_ts  # noqa: E402
from fit_gates import gate_employer  # noqa: E402
from js_common import DEPT_TZ, JS_ROOT, cli_date, normalize_title, read_jsonl, today  # noqa: E402

BOARD_SOURCES = frozenset(SUPPORTED_VENDORS)          # polled by fetch_ats.py
# platforms whose roles reach us by alert mail or a hand-recorded card (targets/platforms.yaml ids)
AGGREGATORS = frozenset({"linkedin", "ziprecruiter", "glassdoor", "jobright", "google-jobs-search", "wellfound",
                         "indeed", "wttj", "hiringcafe", "yc", "himalayas", "simplify"})
PROBE_VENDORS = ("greenhouse", "lever", "ashby")      # the public board APIs discover_companies.py reads
FIRST_SWEEP_HOURS = 1.0                               # a board record this close to its company's first record
LEAD_BUCKETS = ((-72, "alert 3+ days first"), (-24, "alert 1 to 3 days first"), (0, "alert up to 1 day first"),
                (24, "board up to 1 day first"), (72, "board 1 to 3 days first"), (None, "board 3+ days first"))
AGE_BUCKETS = ((24, "within 24 h"), (72, "24 to 72 h"), (168, "3 to 7 days"), (336, "7 to 14 days"),
               (None, "over 14 days"))


# ------------------------------------------------------------------------------ small helpers
def hours(a: datetime, b: datetime) -> float:
    return round((a - b).total_seconds() / 3600, 1)


def stats(values: Iterable[float]) -> dict[str, Any]:
    vals = sorted(values)
    if not vals:
        return {"n": 0, "median": None, "p25": None, "p75": None, "min": None, "max": None}
    q = statistics.quantiles(vals, n=4, method="inclusive") if len(vals) >= 2 else [vals[0]] * 3
    return {"n": len(vals), "median": round(statistics.median(vals), 1), "p25": round(q[0], 1),
            "p75": round(q[2], 1), "min": round(vals[0], 1), "max": round(vals[-1], 1)}


def bucket(values: Iterable[float], edges: tuple) -> dict[str, int]:
    out = {label: 0 for _, label in edges}
    for v in values:
        for edge, label in edges:
            if edge is None or v < edge:
                out[label] += 1
                break
    return out


def earliest(*values: Any) -> datetime | None:
    found = [t for t in (parse_ts(v) for v in values) if t is not None]
    return min(found) if found else None


def channel(rec: dict[str, Any]) -> str | None:
    """alert (job-alert mail), platform (a card from an aggregator recorded by hand), board (polled), else None."""
    src = str(rec.get("source") or "")
    if src.endswith("-alert"):
        return "alert"
    if src == "manual" and str(rec.get("platform") or "") in AGGREGATORS:
        return "platform"
    if src in BOARD_SOURCES:
        return "board"
    return None


class Employers:
    """One slug per employer, and whether it is on the watchlist (targets/companies.yaml)."""

    def __init__(self, root: Path) -> None:
        watch = load_companies(root)
        self.targets = {str(c.get("slug")) for c in watch if c.get("slug")}
        self.watch = CompanyIndex([], watch)
        self.full = CompanyIndex.load(root)

    def slug(self, company: Any, slug: Any = None) -> str:
        for value in (company, slug):
            hit, source = self.watch.resolve(str(value)) if value else (None, None)
            if source == "watchlist":
                return str(hit)
        if slug and str(slug) in self.targets:
            return str(slug)
        hit, _ = self.full.resolve(str(company or slug or ""))
        return str(hit or slug or "")

    def is_target(self, slug: str) -> bool:
        return slug in self.targets

    def extends(self, company: Any) -> str | None:
        """The one watchlist slug whose name `company` starts with ("Acme AI" -> acme), else None. A guess, never
        a join ("Acme Leisure Group" starts with "Acme"): such roles are counted apart and listed to verify."""
        hits = {e["slug"] for e in self.watch.entries for n in e["names"] if extends_company(str(company or ""), n)}
        return hits.pop() if len(hits) == 1 else None


def band(rec: dict[str, Any], jd: str, title_filter: Any) -> tuple[bool, str]:
    """(in band, why not) employer first, then the title filter and role gate, then the stored gate."""
    if any(level == "fail" for level, _, _ in gate_employer(rec, jd)):
        return False, "employer gate"
    title = str(rec.get("title") or "")
    if not title_filter.matches(title):
        return False, "title filter"
    if hunt_score.role_fit(title, jd, title_filter)[0] < hunt_score.ROLE_MIN:
        return False, "role band"
    if rec.get("gate") == "fail":
        why = next((str(r) for r in rec.get("gate_reasons") or []
                    if not str(r).startswith(("note:", "flag:"))), "gate: fail")
        return False, "gate " + why.split(":", 1)[0]
    return True, ""


# ------------------------------------------------------------------------------ inputs
def load_posts(root: Path) -> list[dict[str, Any]]:
    """state/posts/records.jsonl, one record per post key (the last full row wins; `made` rows add the job id)."""
    by_key: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(Path(root) / "state" / "posts" / "records.jsonl"):
        key = str(row.get("key") or "")
        if not key:
            continue
        if "hiring" in row or "role_title" in row:
            by_key[key] = {**by_key.get(key, {}), **row}
        elif row.get("made"):
            by_key.setdefault(key, {"key": key})["job"] = row.get("job")
    return [r for r in by_key.values() if "hiring" in r]


def yc_boards(root: Path) -> dict[str, dict[str, Any]]:
    p = Path(root) / "state" / "yc_boards.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for slug, row in (data.get("to_confirm") or {}).items():
        if isinstance(row, dict) and row.get("vendor"):
            out[slug] = {"vendor": row["vendor"], "token": row.get("token"), "roles": row.get("jobs"),
                         "found_by": "yc_boards.json (to confirm)"}
    for slug, day in (data.get("no_board") or {}).items():
        out.setdefault(slug, {"vendor": None, "token": None, "found_by": f"yc_boards.json: no public board ({day})"})
    return out


def funding(root: Path, employers: Employers) -> dict[str, str]:
    """{employer slug: newest Form D file date} from the newest state/signals/funding-*.json."""
    files = sorted((Path(root) / "state" / "signals").glob("funding-*.json"))
    if not files:
        return {}
    try:
        signals = json.loads(files[-1].read_text(encoding="utf-8")).get("signals") or []
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for sig in signals:
        if not isinstance(sig, dict) or not sig.get("company"):
            continue
        slug = str(sig.get("target_slug") or employers.slug(sig["company"]))
        out[slug] = max(out.get(slug, ""), str(sig.get("file_date") or ""))
    return out


# ------------------------------------------------------------------------------ 1. coverage
def coverage(jobs: dict[str, dict[str, Any]], posts: list[dict[str, Any]], employers: Employers,
             title_filter: Any) -> dict[str, Any]:
    roles: dict[tuple[str, str], dict[str, Any]] = {}
    excluded: Counter = Counter()
    no_employer = 0
    board_keys = {(employers.slug(r.get("company"), r.get("company_slug")), normalize_title(r.get("title")))
                  for r in jobs.values() if channel(r) == "board"}

    def add(slug: str, title: str, ch: str, at: datetime | None, ident: str, company: Any) -> None:
        key = (slug, normalize_title(title))
        target = employers.is_target(slug)
        row = roles.setdefault(key, {"employer": slug, "title": title, "channels": Counter(), "ids": [],
                                     "learned": None, "target": target, "on_board": key in board_keys,
                                     "extends": None if target else employers.extends(company)})
        row["channels"][ch] += 1
        row["ids"].append(ident)
        if at is not None and (row["learned"] is None or at < row["learned"]):
            row["learned"] = at

    for rec in jobs.values():
        ch = channel(rec)
        if ch not in ("alert", "platform"):
            continue
        ok, why = band(rec, str(rec.get("_jd_text") or ""), title_filter)
        if not ok:
            excluded[why] += 1
            continue
        slug = employers.slug(rec.get("company"), rec.get("company_slug"))
        if (rec.get("duplicate_of") and channel(jobs.get(str(rec["duplicate_of"]), {})) == "board"):
            board = jobs[str(rec["duplicate_of"])]
            slug = employers.slug(board.get("company"), board.get("company_slug"))
            add(slug, str(board.get("title") or rec.get("title")), ch,
                earliest(rec.get("alert_received_at"), rec.get("first_seen")), str(rec["id"]), board.get("company"))
            continue
        add(slug, str(rec.get("title") or ""), ch, earliest(rec.get("alert_received_at"), rec.get("first_seen")),
            str(rec["id"]), rec.get("company"))
    for post in posts:
        rec = {"company": post.get("company"), "title": post.get("role_title")}
        if not post.get("hiring") or not post.get("in_band") or post.get("not_hiring"):
            excluded["post: not an in-band hiring post"] += 1
            continue
        ok, why = band(rec, "", title_filter)
        if not ok:
            excluded["post: " + why] += 1
            continue
        if not post.get("company") and not post.get("company_slug"):
            no_employer += 1
            continue
        add(employers.slug(post.get("company"), post.get("company_slug")), str(post.get("role_title") or ""),
            "post", parse_ts(post.get("date")), str(post.get("key")), post.get("company"))
    rows = list(roles.values())
    at_target = [r for r in rows if r["target"]]
    likely = [r for r in rows if not r["target"] and r["extends"]]
    missing = [r for r in rows if not r["target"] and not r["extends"]]
    by_channel = {ch: {"roles": sum(1 for r in rows if r["channels"][ch]),
                       "not_on_watchlist": sum(1 for r in missing if r["channels"][ch])}
                  for ch in ("alert", "platform", "post")}
    return {"roles": len(rows), "at_targets": len(at_target),
            "at_targets_on_board": sum(1 for r in at_target if r["on_board"]),
            "at_targets_not_on_board": sum(1 for r in at_target if not r["on_board"]),
            "not_on_board_roles": sorted(({"employer": r["employer"], "title": r["title"]}
                                          for r in at_target if not r["on_board"]),
                                         key=lambda x: (x["employer"], x["title"])),
            "name_extends_target": len(likely),
            "name_extends_list": sorted({(r["employer"], r["extends"]) for r in likely}),
            "not_on_watchlist": len(missing),
            "gap_share": round(len(missing) / len(rows), 3) if rows else None,
            "employers_not_on_watchlist": len({r["employer"] for r in missing}),
            "by_channel": by_channel, "posts_without_employer": no_employer,
            "excluded": dict(excluded.most_common()), "_rows": rows}


def rank_missing(rows: list[dict[str, Any]], jobs: dict[str, dict[str, Any]], yc: dict[str, dict[str, Any]],
                 funded: dict[str, str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r["target"] and not r["extends"]:
            groups[r["employer"]].append(r)
    out = []
    for slug, roles in groups.items():
        latest = max((r["learned"] for r in roles if r["learned"]), default=None)
        ids = [i for r in roles for i in r["ids"]]
        board = offline_board([jobs[i] for i in ids if i in jobs]) or yc.get(slug)
        chans: Counter = Counter()
        for r in roles:
            chans.update(r["channels"])
        out.append({"employer": slug, "in_band_roles": len(roles),
                    "channels": dict(sorted(chans.items())),
                    "titles": sorted({r["title"] for r in roles})[:3],
                    "latest": latest.date().isoformat() if latest else None,
                    "funded": funded.get(slug), "board": dict(board) if board else None})
    # most in-band roles first, then most sightings, then the most recent, then the slug (a stable order)
    out.sort(key=lambda r: (-r["in_band_roles"], -sum(r["channels"].values()),
                            -date.fromisoformat(r["latest"]).toordinal() if r["latest"] else 0, r["employer"]))
    return out


def offline_board(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A board a job URL already names (ats_detect signatures), with no request."""
    for rec in recs:
        urls = [rec.get("source_url"), rec.get("apply_url")] + [s.get("url") for s in rec.get("sightings") or []
                                                                 if isinstance(s, dict)]
        for url in urls:
            vendor, token, _ = detect(str(url or ""), "")
            if vendor and token:
                return {"vendor": vendor, "token": token, "found_by": "job URL"}
    return None


def probe(rows: list[dict[str, Any]], http: Any, budget: int, title_filter: Any) -> int:
    """Name probes (slug, slug without hyphens) against the public Greenhouse, Lever and Ashby board APIs for ranked
    employers with no known board, in rank order, at most `budget` requests. Returns the requests made."""
    import discover_companies as dc
    used = 0
    for row in rows:
        if row["board"] is not None:
            continue
        slug = row["employer"]
        tries = [(v, t) for t in dict.fromkeys([slug, slug.replace("-", "")]) for v in PROBE_VENDORS]
        errors = []
        for vendor, token in tries:
            if used >= budget:
                break
            used += 1
            try:
                titles = dc._get(http, vendor, token)
            except FetchError as exc:
                errors.append(f"{vendor} {token}: {str(exc).splitlines()[0][:60]}")
                continue
            if titles is not None:
                in_band = sum(1 for t in titles if title_filter.matches(t)
                              and hunt_score.role_fit(t, "", title_filter)[0] >= hunt_score.ROLE_MIN)
                row["board"] = {"vendor": vendor, "token": token, "roles": len(titles), "in_band_roles": in_band,
                                "found_by": "name probe: verify it is this company"}
                break
        else:
            row["board"] = {"vendor": None, "token": None,
                            "found_by": "probed, no public Greenhouse, Lever or Ashby board"
                            + (f" ({'; '.join(errors)})" if errors else "")}
        if used >= budget:
            break
    return used


# ------------------------------------------------------------------------------ 2. lead time
def lead_time(jobs: dict[str, dict[str, Any]], employers: Employers, title_filter: Any) -> dict[str, Any]:
    boards: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    first_sweep: dict[str, datetime] = {}
    for r in jobs.values():
        if channel(r) != "board":
            continue
        slug = employers.slug(r.get("company"), r.get("company_slug"))
        boards[(slug, normalize_title(r.get("title")))].append(r)
        seen = parse_ts(r.get("first_seen"))
        if seen and (slug not in first_sweep or seen < first_sweep[slug]):
            first_sweep[slug] = seen
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in jobs.values():
        if channel(rec) != "alert":
            continue
        ok, _ = band(rec, str(rec.get("_jd_text") or ""), title_filter)
        alert_at = earliest(rec.get("alert_received_at"), rec.get("first_seen"))
        if not ok or alert_at is None:
            continue
        twin = jobs.get(str(rec.get("duplicate_of") or ""))
        if twin is not None and channel(twin) == "board":
            cands, how = [twin], "dedup"
        else:
            slug = employers.slug(rec.get("company"), rec.get("company_slug"))
            cands, how = boards.get((slug, normalize_title(rec.get("title"))), []), "employer and title"
            same = [c for c in cands if normalize_location(c.get("location")) == normalize_location(rec.get("location"))]
            cands = same or cands
        cands = [c for c in cands if parse_ts(c.get("first_seen"))]
        if not cands:
            continue
        board = min(cands, key=lambda c: abs((parse_ts(c["first_seen"]) - alert_at).total_seconds()))
        board_at = parse_ts(board["first_seen"])
        slug = employers.slug(board.get("company"), board.get("company_slug"))
        key = (slug, normalize_title(board.get("title")))
        sweep = first_sweep.get(slug)
        posted = parse_ts(board.get("posted_at"))
        row = {"alert": rec["id"], "board": board["id"], "employer": slug, "title": board.get("title"),
               "platform": rec.get("platform"), "matched_by": how, "board_first_seen": board.get("first_seen"),
               "alert_at": alert_at.isoformat().replace("+00:00", "Z"), "lead_hours": hours(alert_at, board_at),
               "first_sweep": bool(sweep and hours(board_at, sweep) <= FIRST_SWEEP_HOURS),
               "posted_to_alert_days": round((alert_at - posted).total_seconds() / 86400, 1) if posted else None}
        if key not in pairs or alert_at < parse_ts(pairs[key]["alert_at"]):
            pairs[key] = row
    rows = sorted(pairs.values(), key=lambda r: (-r["lead_hours"], r["alert"]))
    steady = [r["lead_hours"] for r in rows if not r["first_sweep"]]
    return {"pairs": len(rows), "board_first": sum(1 for r in rows if r["lead_hours"] > 0),
            "alert_first": sum(1 for r in rows if r["lead_hours"] < 0),
            "all": stats(r["lead_hours"] for r in rows), "after_first_sweep": stats(steady),
            "distribution": bucket((r["lead_hours"] for r in rows), LEAD_BUCKETS),
            "posted_to_alert_days": stats(r["posted_to_alert_days"] for r in rows
                                          if r["posted_to_alert_days"] is not None),
            "rows": rows}


# ------------------------------------------------------------------------------ 3. freshness at action
def _group(jobs: dict[str, dict[str, Any]], jid: str) -> list[dict[str, Any]]:
    head = jobs.get(jid)
    if head is None:
        return []
    canon = str(head.get("duplicate_of") or jid)
    return [r for r in jobs.values() if r["id"] in (jid, canon) or str(r.get("duplicate_of") or "") == canon]


def freshness(root: Path, jobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    import tracker
    rows, no_job = [], []
    for slug, props, body in tracker.iter_applications(root):
        if not props.get("applied_at"):
            continue
        jid = tracker._link_target(props.get("job"))
        group = _group(jobs, str(jid or ""))
        events = [e for e in tracker.parse_events(body) if e.type == "applied"]
        applied = earliest(*(e.at for e in events))
        precision = "event"
        if applied is None:
            day = date.fromisoformat(str(props["applied_at"])[:10])
            applied, precision = datetime.combine(day, time(12), DEPT_TZ).astimezone(timezone.utc), "day"
        if not group:
            no_job.append(slug)
            continue
        first = earliest(*(v for r in group for v in
                           [r.get("first_seen"), r.get("alert_received_at")]
                           + [s.get("first_seen") for s in r.get("sightings") or [] if isinstance(s, dict)]))
        posted = earliest(*(r.get("posted_at") for r in group))
        rows.append({"application": slug, "job": jid, "applied_at": applied.isoformat().replace("+00:00", "Z"),
                     "precision": precision, "first_seen": first.isoformat().replace("+00:00", "Z") if first else None,
                     "hours_from_first_sighting": hours(applied, first) if first else None,
                     "posting_age_hours": hours(applied, posted) if posted else None,
                     "posted_to_first_seen_hours": hours(first, posted) if posted and first else None})
    seen = [r["hours_from_first_sighting"] for r in rows if r["hours_from_first_sighting"] is not None]
    after = [h for h in seen if h >= 0]
    ages = [r["posting_age_hours"] for r in rows if r["posting_age_hours"] is not None
            and r["posting_age_hours"] >= 0]
    found = [r["posted_to_first_seen_hours"] for r in rows if r["posted_to_first_seen_hours"] is not None
             and r["posted_to_first_seen_hours"] >= 0]
    return {"applied": len(rows) + len(no_job), "measured": len(after),
            "record_after_apply": sum(1 for h in seen if h < 0), "no_job_record": no_job,
            "day_precision": sum(1 for r in rows if r["precision"] == "day"),
            "from_first_sighting": stats(after), "within_72h": sum(1 for h in after if h <= 72),
            "distribution": bucket(after, AGE_BUCKETS), "posting_age": stats(ages),
            "posting_age_distribution": bucket(ages, AGE_BUCKETS), "posted_to_first_seen": stats(found),
            "rows": sorted(rows, key=lambda r: r["application"])}


# ------------------------------------------------------------------------------ report
def report(root: Path, day: date, top: int = 20, probe_budget: int = 0, http: Any = None,
           title_filter: Any = None) -> dict[str, Any]:
    root = Path(root)
    tf = title_filter or load_title_filter(root)
    jobs = load_jobs(root) if (root / "jobs").is_dir() else {}
    employers = Employers(root)
    cov = coverage(jobs, load_posts(root), employers, tf)
    ranked = rank_missing(cov.pop("_rows"), jobs, yc_boards(root), funding(root, employers))
    shown = ranked[:top]
    used = 0
    if probe_budget > 0:
        if http is None:
            from fetch_ats import Http
            http = Http(retries=0)                    # one request per probe: the budget is a count of requests
        used = probe(shown, http, probe_budget, tf)
    gap_roles = sum(r["in_band_roles"] for r in shown)
    cov["top_close"] = {"employers": len(shown), "roles": gap_roles,
                        "share_of_gap": round(gap_roles / cov["not_on_watchlist"], 3)
                        if cov["not_on_watchlist"] else None,
                        "with_board": sum(1 for r in shown if r["board"] and r["board"].get("vendor"))}
    return {"date": day.isoformat(), "generated_by": "scripts/finding_metrics.py", "requests": used,
            "coverage": cov, "missing_employers": shown, "lead_time": lead_time(jobs, employers, tf),
            "freshness": freshness(root, jobs)}


def _h(v: Any) -> str:
    return "none" if v is None else f"{v:g} h"


def _pct(n: int, d: int) -> str:
    return f"{n} of {d}" + (f" ({n / d:.0%})" if d else "")


def render(r: dict[str, Any]) -> str:
    c, lt, fr = r["coverage"], r["lead_time"], r["freshness"]
    out = [f"# Finding metrics · {r['date']}", "",
           "How complete and how early our role finding is. Built by scripts/finding_metrics.py from jobs/, "
           "state/posts/records.jsonl and applications/; in-band roles only (employer gate, title filter, "
           "role gate, stored gate). " + (f"{r['requests']} board API requests." if r["requests"] else
                                           "No network request."), "",
           "## Coverage", "",
           f"In-band roles learned outside our boards (alert mail, platform cards, hiring posts): **{c['roles']}**. "
           f"At watchlist employers: {c['at_targets']} ({c['at_targets_on_board']} also on a polled board, "
           f"{c['at_targets_not_on_board']} not seen on one). Employer name starts with a watchlist name (likely "
           f"the target, to verify): {c['name_extends_target']}. **Not on the watchlist: "
           f"{_pct(c['not_on_watchlist'], c['roles'])}**, at {c['employers_not_on_watchlist']} employers. "
           f"Hiring posts with no named employer: {c['posts_without_employer']}.", "",
           "| channel | in-band roles | not on the watchlist |", "|---|--:|--:|"]
    out += [f"| {k} | {v['roles']} | {v['not_on_watchlist']} |" for k, v in c["by_channel"].items()]
    out += ["", "Excluded before counting: " + (", ".join(f"{k} {v}" for k, v in c["excluded"].items()) or "none")
            + ".", ""]
    if c["name_extends_list"]:
        out += ["Names that start with a watchlist name (verify, then add an alias or a new entry): "
                + ", ".join(f"{e} (starts with {t})" for e, t in c["name_extends_list"]) + ".", ""]
    if c["not_on_board_roles"]:
        out += ["Watchlist roles no polled board has shown (a reader, token or title-filter miss): "
                + "; ".join(f"{x['employer']}: {x['title']}" for x in c["not_on_board_roles"]) + ".", ""]
    out += [
            f"## Employers that would close most of the gap (top {c['top_close']['employers']})", "",
            f"These {c['top_close']['employers']} employers hold {c['top_close']['roles']} of the "
            f"{c['not_on_watchlist']} roles not on the watchlist"
            + (f" ({c['top_close']['share_of_gap']:.0%})" if c["top_close"]["share_of_gap"] is not None else "")
            + f"; {c['top_close']['with_board']} have a known public board. Candidates only: the Recruiter adds a "
            "watchlist entry by hand.", "",
            "| employer | in-band roles | channels | latest | board | funded | titles |", "|---|--:|---|---|---|---|---|"]
    for m in r["missing_employers"]:
        b = m["board"] or {}
        board = (f"{b['vendor']} `{b['token']}`" + (f" ({b['found_by']})" if b.get("found_by") else "")
                 if b.get("vendor") else (b.get("found_by") or "not known"))
        chans = ", ".join(f"{k} {v}" for k, v in m["channels"].items())
        out.append(f"| {m['employer']} | {m['in_band_roles']} | {chans} | {m['latest'] or ''} | {board} | "
                   f"{m['funded'] or ''} | {'; '.join(m['titles'])} |")
    a, s = lt["all"], lt["after_first_sweep"]
    out += ["", "## Lead time: board against alert mail", "",
            f"Roles seen on both: {lt['pairs']}. The board saw it first: {lt['board_first']}; the alert first: "
            f"{lt['alert_first']}. Median lead {_h(a['median'])} (middle half {_h(a['p25'])} to {_h(a['p75'])}); "
            f"after each board's first sweep ({s['n']} roles): median {_h(s['median'])}. Positive hours: the board "
            f"was earlier. Alert mail arrived a median {lt['posted_to_alert_days']['median']} days after the "
            f"board's posted date ({lt['posted_to_alert_days']['n']} roles with a posted date).", "",
            "| lead | roles |", "|---|--:|"]
    out += [f"| {k} | {v} |" for k, v in lt["distribution"].items()]
    if lt["rows"]:
        out += ["", "| employer | title | platform | lead (h) | first sweep | matched by |", "|---|---|---|--:|---|---|"]
        out += [f"| {x['employer']} | {x['title']} | {x['platform']} | {x['lead_hours']:g} | "
                f"{'yes' if x['first_sweep'] else 'no'} | {x['matched_by']} |" for x in lt["rows"][:40]]
    f = fr["from_first_sighting"]
    out += ["", "## Freshness at action", "",
            f"Applications with applied_at: {fr['applied']}; measured {fr['measured']} (job record created after "
            f"the application: {fr['record_after_apply']}; no job record: {len(fr['no_job_record'])}; applied time "
            f"from the day only: {fr['day_precision']}). From our first sighting to applied: median "
            f"{_h(f['median'])} (middle half {_h(f['p25'])} to {_h(f['p75'])}); within 72 h: "
            f"{_pct(fr['within_72h'], fr['measured'])}. Posting age at apply: median "
            f"{_h(fr['posting_age']['median'])} ({fr['posting_age']['n']} with a posted date); from the posted date to "
            f"our first sighting: median {_h(fr['posted_to_first_seen']['median'])}. A vendor's posted date can be "
            "its last update rather than its first publication, so read posting age as a bound.", "",
            "| since first sighting | applications | posting age | applications |", "|---|--:|---|--:|"]
    out += [f"| {k} | {v} | {k} | {fr['posting_age_distribution'][k]} |" for k, v in fr["distribution"].items()]
    if fr["no_job_record"]:
        out += ["", "No job record: " + ", ".join(fr["no_job_record"]) + "."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None, http: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--probe-budget", type=int, default=0,
                    help="at most this many public board API requests for ranked employers with no known board")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.top < 1 or a.probe_budget < 0:
        print("--top must be 1 or more and --probe-budget 0 or more", file=sys.stderr)
        return 2
    day = a.today or today()
    r = report(a.root, day, a.top, a.probe_budget, http)
    text = render(r)
    if a.write:
        from discover_companies import write
        base = a.root / "state" / "reports" / f"finding-{day.isoformat()}"
        write(base.with_suffix(".json"), json.dumps(r, indent=2, default=str) + "\n")
        write(base.with_suffix(".md"), text)
        print(f"wrote {base.with_suffix('.md').relative_to(a.root)} and .json")
    print(json.dumps(r, indent=2, default=str) if a.json else text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
