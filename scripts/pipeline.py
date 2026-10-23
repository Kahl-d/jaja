#!/usr/bin/env python3
"""The daily deterministic discovery harness (routine js-scout).

  fetch (ATS boards in targets/companies.yaml) + hn (monthly) + adzuna (only with keys)
    + himalayas (keyless remote-jobs API; no network: skipped)
    + aidevjobs
    + remote_feeds
    + alerts (job-alert emails in the classified harvest, scripts/job_alerts.py; no harvest: skipped)
    -> dedup (keys, reposts, ghost score, state/seen.jsonl)
    -> h1b (employer sponsorship from legal/evidence/h1b caches)
    -> gates (scripts/fit_gates.py on new or JD-changed postings)
    -> queue (gate pass/review: status fit_checked, fit_verdict unscored for the fit-scorer agent;
              gate fail: status skipped, fit_verdict skip)
    -> triage (scripts/hunt_score.py, the 0 to 100 triage score on every open, gated job)
    -> funding (optional, off unless sources.yaml `funding` says enabled: true: SEC EDGAR Form D raises by
                target companies and AI/ML filers, scripts/funding_signals.py; writes state/signals/funding-<date>.md
                and .json, never job records)
    -> news (scripts/news_watch.py, listing and news pages through web_snapshot.py; only pages in
            targets/news-watch.yaml whose terms were checked; writes state/review/news-watch-<date>.md)
    -> dashboard (DASHBOARD.md + state/dashboard.json)

Query terms: adzuna and himalayas search with the `queries:` list of their sources.yaml entry when it has one
(himalayas: strings; adzuna: strings or {what, where} mappings), else with the adapter's built-in list; an
unreadable list falls back to the built-in one and the receipt says so. job-scout owns those lists.

Records flow in memory between stages, so --dry-run computes every stage and writes nothing.
A real run saves changed job records, appends ledger rows (state/seen.jsonl, state/job-alerts.jsonl),
writes the alert review list (state/review/job-alerts-<date>.md), updates sources.yaml (last_run,
yield_30d, in place) and state/hn_state.json, renders the dashboard, and appends a receipt to
<os-root>/logs/runs/js-scout/<YYYY-MM-DD>.md (per-stage counts, errors, notes). A failing
board or stage is recorded and the run continues (status partial).

The routine verdict (receipt["routine"]: status, sentinel, note) is what a scheduled routine's receipt says: ok; warn
when only boards erred (at most BOARD_ERROR_LIMIT; the note names each and counts the boards that read fine); failed
(NEEDS_YOU) on a stage error, no board polled, or more board errors than the limit. --routine-receipt NAME appends it
to logs/runs/NAME/<date>.md through the kernel's watchdog.py (js-hunt-boards). With it, a `started`
section (run_id, planned stages) is written before any stage and the verdict carries the same run_id; a crash writes a
failed verdict; a kill leaves "started" with no end, which watchdog.py reports as ERROR after its grace.

CLI:  python pipeline.py [--root R] [--os-root OS]
                         [--stages fetch,hn,adzuna,himalayas,aidevjobs,remote_feeds,alerts,dedup,h1b,gates,queue,triage,funding,news,dashboard]
                         [--force-hn] [--max-pages N] [--dry-run] [--json] [--routine-receipt NAME]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adzuna  # noqa: E402
import dashboard  # noqa: E402
import dedup  # noqa: E402
import fit_gates  # noqa: E402
import funding_signals  # noqa: E402
import h1b_lookup  # noqa: E402
import aidevjobs  # noqa: E402
import remote_feeds  # noqa: E402
import himalayas  # noqa: E402
import hn_hiring  # noqa: E402
import job_alerts  # noqa: E402
from fetch_ats import (SUPPORTED_VENDORS, Http, close_missing_records, fetch_company, iso_utc,  # noqa: E402
                       load_companies, load_jobs, load_title_filter, merge_records, parse_ts, save_job)
from js_common import os_root_for, JS_ROOT, append_jsonl, dept_day, fm, read_jsonl  # noqa: E402
import hunt_score  # noqa: E402
import news_watch  # noqa: E402
import fetch_ats  # noqa: E402

STAGES = ("fetch", "hn", "adzuna", "himalayas", "aidevjobs", "remote_feeds", "alerts", "dedup", "h1b", "gates", "queue", "triage", "funding",
          "news", "feeds", "discover", "dashboard", "coverage")
OPT_IN_STAGES = ("feeds", "discover")   # run only when --stages names them (js-hunt-signals)
DEFAULT_STAGES = tuple(s for s in STAGES if s not in OPT_IN_STAGES)   # a run without --stages
ROUTINE = "js-scout"
FETCH_SHARE = 0.6               # fetch gets this share of the run budget; the cheap stages are never cut
# The yield of each board (new postings, seconds) over its last YIELD_KEEP runs; a board
# that cost SLOW_LANE_MIN_S or more and gave nothing new on its last SLOW_LANE_RUNS runs is read once in SLOW_LANE_H
YIELD_KEEP = 6
# measured: a sitemap board fetches one page per ~6.5 s, so its page cap could outlast the fetch share; the page
# crawlers go after the API boards and each gets at most SITEMAP_BOARD_S a run (pages left wait, newest first)
PAGE_CRAWLERS = ("sitemap",)
SITEMAP_BOARD_S = 150.0
TIER_RANK = {"dream": 0, "strong": 1, "good": 2, "fallback": 3}     #: the fetch order's tier key (no tier: good)
SLOW_LANE_RUNS = 2
SLOW_LANE_MIN_S = 60.0
SLOW_LANE_H = 20
DISCOVER_LIMIT = 40             # new companies probed per run (discover_companies.py's own --limit default)
UNREAD_LIMIT_H = 24             # a board deferred so long that it went a day unread fails the run (the daily promise)
BOARD_ERROR_LIMIT = 5           # more boards erring than this in one run is a failed run (the routine's needs-you rule)
# js-hunt-boards names only fetch and the cheap stages, so without this the feeds would never run on a schedule.
# --daily names feed stages that run at most once a department day: a stage is
# skipped when one of its sources' `last_run` in sources.yaml (written by the real run that read it) is today, so the
# first scheduled run of the day reads the feeds and a missed 07:00 run hands them to the next one. Each feed is a few
# requests (HN job stories 60 items, Himalayas 3 pages a query, AI Dev Jobs 2), after fetch, outside its share.
DAILY_SOURCES = {"hn": ("hn",), "adzuna": ("adzuna",), "himalayas": ("himalayas",), "aidevjobs": ("aidevjobs",),
                 "remote_feeds": tuple(remote_feeds.FEEDS)}


@dataclass
class PipelineResult:
    receipt: dict[str, Any]
    workset: dict[str, dict[str, Any]]


def _default_http() -> Any:
    return Http()


# ------------------------------------------------------------------------------ sources.yaml
def load_sources(root: Path) -> dict[str, dict[str, Any]]:
    path = Path(root) / "sources.yaml"
    if not path.is_file():
        return {}
    data = fm.parse_yaml(path.read_text(encoding="utf-8"))
    return {str(s.get("id")): s for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("id")}


def source_enabled(sources: Mapping[str, dict[str, Any]], sid: str) -> bool:
    return sources.get(sid, {}).get("enabled", True) is not False


def source_queries(sources: Mapping[str, dict[str, Any]], sid: str, where_allowed: bool = False) -> list[Any] | None:
    """A source's `queries:` in sources.yaml: None when absent or empty (the adapter's built-in list). Strings, or
    with where_allowed (adzuna) {what, where} mappings, returned as {"what": ..., "where": ...} dicts for adzuna and
    plain strings otherwise. Raises ValueError, naming the problem, when the list cannot be read."""
    raw = sources.get(sid, {}).get("queries")
    if raw in (None, [], ""):
        return None
    if not isinstance(raw, list):
        raise ValueError("queries must be a list")
    out: list[Any] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append({"what": item.strip()} if where_allowed else item.strip())
        elif where_allowed and isinstance(item, dict) and str(item.get("what") or "").strip() \
                and set(item) <= {"what", "where"}:
            out.append({k: str(v).strip() for k, v in item.items() if str(v or "").strip()})
        else:
            raise ValueError(f"entry {len(out) + 1} is not " + ("a text or a {what, where} mapping" if where_allowed
                                                                 else "a text"))
    return out


def update_sources_yaml(path: Path, updates: Mapping[str, Mapping[str, Any]]) -> None:
    """Rewrite only `last_run:` / `yield_30d:` lines inside each `- id:` entry; comments survive."""
    if not path.is_file() or not updates:
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current = None
    for i, line in enumerate(lines):
        m = re.match(r"^\s*-\s+id:\s*['\"]?([\w.-]+)", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r"^(\s+)(last_run|yield_30d):[^\n#]*?(\s+#[^\n]*)?(\n?)$", line)
        if current in updates and m and m.group(2) in updates[current]:
            value = updates[current][m.group(2)]
            rendered = "null" if value is None else (f"'{value}'" if isinstance(value, str) else str(value))
            lines[i] = f"{m.group(1)}{m.group(2)}: {rendered}{m.group(3) or ''}{m.group(4)}"
    path.write_text("".join(lines), encoding="utf-8")


def yield_30d(workset: Mapping[str, dict[str, Any]], source: str, now: datetime) -> int:
    n = 0
    for rec in workset.values():
        seen = parse_ts(rec.get("first_seen"))
        if rec.get("source") == source and seen and (now - seen).days < 30 and rec.get("gate") in ("pass", "review"):
            n += 1
    return n


# ------------------------------------------------------------------------------ receipt
def _fmt(result: dict[str, Any]) -> str:
    return ", ".join(f"{k} {v}" for k, v in result.items()) or "-"


def write_receipt(os_root: Path, receipt: dict[str, Any], now: datetime) -> Path:
    day = dept_day(now)                      # the department's day (America/Los_Angeles), never the UTC date
    path = Path(os_root) / "logs" / "runs" / ROUTINE / f"{day:%Y-%m-%d}.md"
    secs = receipt.get("stage_s") or {}
    rows = "\n".join(f"| {name} | {_fmt(res)} | {secs[name]:.1f} |" if name in secs else f"| {name} | {_fmt(res)} | - |"
                     for name, res in receipt["stages"].items())
    section = (f"## Run {receipt['started']} · status {receipt['status']}\n\n"
               f"| stage | result | s |\n|---|---|--:|\n{rows}\n\n"
               "Errors:\n" + ("".join(f"- {e}\n" for e in receipt["errors"]) or "- none\n") +
               "\nNotes:\n" + ("".join(f"- {n}\n" for n in receipt["notes"]) or "- none\n"))
    if path.is_file():
        text = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + section
    else:
        text = fm.dump({"routine": ROUTINE, "date": f"{day:%Y-%m-%d}"},
                       f"# {ROUTINE} receipts {day:%Y-%m-%d}\n\n" + section)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------ the run
def _fingerprint(rec: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in rec.items() if not k.startswith("_") or k == "_body"},
                      sort_keys=True, default=str)


def run_pipeline(root: Path, os_root: Path, now: datetime | None = None, http: Any = None,
                 stages: list[str] | None = None, dry_run: bool = False, env: Mapping[str, str] | None = None,
                 force_hn: bool = False, max_pages: int = 20, workday_page_size: int = 20,
                 edgar: Any = None, budget_min: float | None = None,
                 clock: Callable[[], float] = time.monotonic, workers: int = 1,
                 daily: list[str] | tuple[str, ...] = ()) -> PipelineResult:
    wall_start = datetime.now(timezone.utc)
    now = now or wall_start
    root, os_root = Path(root), Path(os_root)
    env = os.environ if env is None else env
    selected = list(stages) if stages else list(DEFAULT_STAGES)
    receipt: dict[str, Any] = {"routine": ROUTINE, "started": iso_utc(now), "dry_run": dry_run, "stages": {},
                               "errors": [], "board_errors": [], "notes": [], "status": "ok", "receipt_path": None,
                               "stage_s": {}, "deferred_boards": [], "cut_boards": [], "unread_24h": [], "slow_lane": []}
    run_t0 = clock()
    workset = load_jobs(root) if (root / "jobs").is_dir() else {}
    before = {rid: _fingerprint(r) for rid, r in workset.items()}
    sources = load_sources(root)
    ctx: dict[str, Any] = {"ran": set(), "jd_changed": set(), "ledger_rows": [], "hn_thread": None, "alert_run": None,
                           "cursor": None}
    net: list[Any] = [http]

    def client() -> Any:
        if net[0] is None:
            net[0] = _default_http()
        return net[0]

    def stage(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        if name not in selected:
            receipt["stages"][name] = {"skipped": "not selected"}
            return
        if name in daily and ran_today(name):
            receipt["stages"][name] = {"skipped": f"already ran today ({dept_day(now)}; --daily: once a day)"}
            return
        t0 = time.monotonic()
        try:
            receipt["stages"][name] = fn()
        except Exception as exc:          # one broken stage must not kill the daily run
            receipt["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
            receipt["stages"][name] = {"error": type(exc).__name__}
        finally:                          # every stage that ran carries its seconds
            receipt["stage_s"][name] = round(time.monotonic() - t0, 1)

    def ran_today(name: str) -> bool:
        """A --daily stage ran today when one of its sources' last_run (sources.yaml) falls on today."""
        for sid in DAILY_SOURCES.get(name, (name,)):
            last = parse_ts((sources.get(sid) or {}).get("last_run"))
            if last is not None and dept_day(last) == dept_day(now):
                return True
        return False

    def do_fetch() -> dict[str, Any]:
        companies = load_companies(root)
        polled = []
        for c in companies:
            vendor = c.get("ats_vendor")
            if vendor in SUPPORTED_VENDORS and not source_enabled(sources, vendor):
                receipt["notes"].append(f"{vendor} disabled in sources.yaml: skipped {c['slug']}")
                continue
            polled.append(c)
        tf = load_title_filter(root)
        known = {str(r["source_url"]) for r in workset.values() if r.get("source_url")}
        cache_dir = None if dry_run else root / "state" / "sitemap"     # pages read that did not match (sitemap)

        def kw(c: dict[str, Any]) -> dict[str, Any]:
            if c.get("ats_vendor") == "workday":
                return {"max_pages": max_pages, "page_size": workday_page_size, "known_urls": known}
            return fetch_ats._vendor_kw(str(c.get("ats_vendor")), max_pages, known, cache_dir, c.get("slug"))
        cursor = _load_cursor(root)
        if budget_min:                       # API boards, then page crawlers; each the boards left first
            polled = fetch_order(polled, cursor, now)
        slow = slow_lane(cursor, now)
        polled, resting = [c for c in polled if c["slug"] not in slow], [c["slug"] for c in polled if c["slug"] in slow]
        deadline = run_t0 + budget_min * 60 * FETCH_SHARE if budget_min else None
        fetch_ats.set_deadline(clock, deadline)
        try:                                 #: boards of different hosts at once (workers), output in this order
            # a cut counts as the run's (run_cut) when the run's deadline was the binding one (a wait that would
            # end past it cuts while the clock is still before it); a page crawler stopped by its own cap has made a read
            reads = fetch_ats.fetch_boards(polled, client() if polled else None, now, tf, kw, workers=workers, clock=clock,
                                           deadline=deadline, own_deadline=lambda c: (board_deadline(c, clock, deadline)
                                                                                      if budget_min else deadline),
                                           fetch=lambda *a, **k: fetch_company(*a, **k),
                                           must_read=must_read if budget_min else None,
                                           host_slots=fetch_ats.HOST_SLOTS)
        finally:
            fetch_ats.set_deadline(clock, None)
        results = [r.result for r in reads if r.result is not None]
        deferred = [r.company["slug"] for r in reads if r.result is None]
        spent = {r.company["slug"]: r.seconds for r in reads if r.result is not None}
        cut = [r.company["slug"] for r in reads if r.run_cut]
        # only a clean, whole read moves last_read and leaves a yield record. The boards never reached lead the
        # next run and the cut ones follow them: a board cut first in line every run would starve all the others
        clean = [r for r in results if not r.errors and r.company_slug not in cut]
        receipt["deferred_boards"], receipt["cut_boards"] = deferred, cut
        receipt["slow_lane"] = resting
        new_by = {res.company_slug: sum(1 for r in res.records if not r.get("_resight")
                                        and str(r.get("source_url") or "") not in known) for res in clean}
        receipt["unread_24h"], ctx["cursor"] = _update_cursor(
            cursor, clean, deferred + cut, now, yields={s_: (n_, spent.get(s_, 0.0)) for s_, n_ in new_by.items()})
        if deferred:
            receipt["notes"].append(f"deferred to the next run (run budget {budget_min:g} min): {', '.join(deferred)}")
        if resting:
            receipt["notes"].append(
                f"slow lane (nothing new on the last {SLOW_LANE_RUNS} runs at {SLOW_LANE_MIN_S:g} s or more each; read once "
                f"in {SLOW_LANE_H} h): {', '.join(resting)}. Recruiter: these boards cost time and gave no new posting")
        records = [r for res in results for r in res.records]
        out = merge_records(workset, records, now)
        moved: list[str] = []
        closed = close_missing_records(workset, results, now,
                                       configured={c["slug"]: str(c.get("ats_vendor") or "") for c in companies},
                                       flagged=moved)
        if moved:                                # never silent; a person re-links them
            receipt["notes"].append(f"board moved, re-link (queued or applied for, left open): {', '.join(sorted(moved))}")
        for res in results:
            receipt["errors"].extend(res.errors)
            receipt["board_errors"].extend(res.errors)
            receipt["notes"].extend(res.notes)
            if not res.errors and res.vendor in SUPPORTED_VENDORS:
                ctx["ran"].add(res.vendor)
        ctx["jd_changed"] |= set(out["jd_changed"])
        by_vendor: dict[str, dict[str, int]] = {}
        vendor_s: dict[str, dict[str, Any]] = {}       #: board seconds per vendor (boards overlap: sums exceed wall)
        for res in results:
            row = by_vendor.setdefault(res.vendor, {"boards": 0, "matched": 0, "errors": 0})
            row["boards"] += 1
            row["matched"] += len(res.records)
            row["errors"] += 1 if res.errors else 0
            secs = spent.get(res.company_slug, 0.0)
            vs = vendor_s.setdefault(res.vendor, {"s": 0.0, "max_s": 0.0, "slowest": None})
            vs["s"] = round(vs["s"] + secs, 1)
            if vs["slowest"] is None or secs > vs["max_s"]:
                vs["max_s"], vs["slowest"] = secs, res.company_slug
        return {"by_vendor": dict(sorted(by_vendor.items())),
                "vendor_s": dict(sorted(vendor_s.items(), key=lambda kv: (-kv[1]["s"], kv[0]))), "workers": workers,
                "boards": len(companies), "polled": len(results), "on_boards": sum(len(r.all_urls) for r in results),
                "matched": len(records), "new": len(out["new"]), "updated": len(out["updated"]),
                "jd_changed": len(out["jd_changed"]), "closed": len(closed),
                "board_errors": sum(1 for r in results if r.errors), "deferred": len(deferred), "cut": len(cut),
                "slow_lane": len(resting)}

    def do_hn() -> dict[str, Any]:
        if not source_enabled(sources, "hn"):
            return {"skipped": "disabled in sources.yaml"}
        # YC job stories are new every day; the Who-is-hiring thread is monthly
        try:
            js_records, js_info = hn_hiring.collect_jobstories(client(), now, load_title_filter(root))
            js_out = merge_records(workset, js_records, now)
            jobstories: dict[str, Any] = {**js_info, "new": len(js_out["new"]), "updated": len(js_out["updated"])}
            ctx["ran"].add("hn")
        except Exception as exc:              # the monthly thread still runs when job stories fail
            jobstories = {"error": f"{type(exc).__name__}: {exc}"}
            receipt["notes"].append(f"hn jobstories: {type(exc).__name__}: {exc}")
        if not force_hn and not hn_hiring.due_this_month(root, now):
            return {"skipped": f"already ran this month ({now:%Y-%m})", "jobstories": jobstories}
        records, info = hn_hiring.collect(client(), now, load_title_filter(root))
        out = merge_records(workset, records, now)
        ctx["hn_thread"] = info["thread_id"]
        ctx["ran"].add("hn")
        return {"thread": info["thread_id"], "comments": info["comments"], "relevant": info["relevant"],
                "new": len(out["new"]), "updated": len(out["updated"]), "jobstories": jobstories}

    def queries(sid: str, where_allowed: bool = False) -> list[Any] | None:
        try:
            return source_queries(sources, sid, where_allowed)
        except ValueError as exc:
            receipt["notes"].append(f"sources.yaml {sid} queries unreadable ({exc}): built-in queries used")
            return None

    def do_adzuna() -> dict[str, Any]:
        if not source_enabled(sources, "adzuna"):
            return {"skipped": "disabled in sources.yaml"}
        records, info = adzuna.collect(client() if adzuna.credentials(env) else None, now, env=env,
                                       queries=queries("adzuna", where_allowed=True),
                                       title_filter=load_title_filter(root))
        if info.get("skipped"):
            return {"skipped": info["skipped"]}
        out = merge_records(workset, records, now)
        ctx["ran"].add("adzuna")
        return {"results": info["results"], "kept": info["kept"], "new": len(out["new"]), "updated": len(out["updated"])}

    def do_himalayas() -> dict[str, Any]:
        if not source_enabled(sources, "himalayas"):
            return {"skipped": "disabled in sources.yaml"}
        records, info = himalayas.collect(client(), now, queries=queries("himalayas"),
                                          title_filter=load_title_filter(root))
        if info.get("skipped"):
            return {"skipped": info["skipped"]}
        receipt["notes"].extend(f"himalayas: {n}" for n in info["notes"])
        out = merge_records(workset, records, now)
        ctx["ran"].add("himalayas")
        return {"queries": info["queries"], "requests": info["requests"], "results": info["results"],
                "kept": info["kept"], "new": len(out["new"]), "updated": len(out["updated"])}

    def do_aidevjobs() -> dict[str, Any]:
        # off until sources.yaml lists `aidevjobs` (a missing entry is off)
        if "aidevjobs" not in sources:
            return {"skipped": "no aidevjobs entry in sources.yaml"}
        if not source_enabled(sources, "aidevjobs"):
            return {"skipped": "disabled in sources.yaml"}
        records, info = aidevjobs.collect(client(), now, queries=queries("aidevjobs"), title_filter=load_title_filter(root))
        if info.get("skipped"):
            return {"skipped": info["skipped"]}
        receipt["notes"].extend(f"aidevjobs: {n}" for n in info["notes"])
        out = merge_records(workset, records, now)
        ctx["ran"].add("aidevjobs")
        return {"queries": info["queries"], "requests": info["requests"], "results": info["results"],
                "kept": info["kept"], "new": len(out["new"]), "updated": len(out["updated"])}

    def do_remote_feeds() -> dict[str, Any]:
        # each feed only when sources.yaml lists it and it is enabled
        listed = [f for f in remote_feeds.FEEDS if f in sources]
        if not listed:
            return {"skipped": "neither remotive nor remoteok is in sources.yaml"}
        feeds = [f for f in listed if source_enabled(sources, f)]
        if not feeds:
            return {"skipped": "disabled in sources.yaml"}
        records, info = remote_feeds.collect(client(), now, root, feeds=feeds, title_filter=load_title_filter(root))
        out = merge_records(workset, records, now)
        ctx["ran"].update(f for f in feeds if not info.get(f, {}).get("skipped"))
        return {"feeds": info, "new": len(out["new"]), "updated": len(out["updated"])}

    def do_alerts() -> dict[str, Any]:
        enabled = [s for s in job_alerts.ALERT_SOURCES if source_enabled(sources, s)]
        if not enabled:
            return {"skipped": "every alert source is disabled in sources.yaml"}
        files = job_alerts.harvest_files(root)
        if not files:
            return {"skipped": "no classified harvest (state/harvest/*.classified.jsonl)"}
        platforms = job_alerts.load_platforms(root)
        if not platforms:
            return {"skipped": "no targets/platforms.yaml (alert senders unmapped)"}
        run = job_alerts.collect(root, now, files=files, platforms=platforms, title_filter=load_title_filter(root),
                                 enabled_sources=enabled)
        out = job_alerts.merge_into(workset, run.records, now)
        ctx["alert_run"] = run
        ctx["ran"].update(enabled)
        if run.review:
            receipt["notes"].append(f"job alerts: {len(run.review)} for review in "
                                    f"state/review/job-alerts-{dept_day(now):%Y-%m-%d}.md")
        per = run.platforms.values()
        return {"files": len(files), "alerts": run.alerts, "cards": sum(p["cards"] for p in per),
                "jobs": len(run.records), "new": len(out["new"]), "updated": len(out["updated"]),
                "known": len(out["known"]), "filtered": sum(p["filtered"] for p in per),
                "unparsed": sum(p["unparsed"] for p in per), "stale": run.stale, "unmapped": run.unmapped,
                "unlabeled": run.unlabeled, "disabled": run.disabled, "review": len(run.review)}

    def do_dedup() -> dict[str, Any]:
        out = dedup.apply_dedup(list(workset.values()), read_jsonl(dedup.ledger_path(root)), now)
        ctx["ledger_rows"] = out["new_rows"]
        return out["summary"]

    def do_h1b() -> dict[str, Any]:
        index = h1b_lookup.load_index(h1b_lookup.cache_dir(root))
        targets = [r for r in workset.values() if r.get("status") != "closed"]
        h1b_lookup.enrich(targets, index, load_companies(root), today=dept_day(now))
        if index.empty:
            receipt["notes"].append("h1b cache empty: run `h1b_lookup.py download` (monthly js-h1b-cache-refresh)")
        return {"enriched": len(targets), "cache_files": len(index.files),
                "sponsorship_yes": sum(1 for r in targets if r.get("sponsorship") == "yes")}

    def do_gates() -> dict[str, Any]:
        todo = [r for r in workset.values() if r.get("status") == "discovered" or r["id"] in ctx["jd_changed"]]
        return fit_gates.apply_gates(todo, fit_gates.load_config(root), load_companies(root))

    def do_queue() -> dict[str, Any]:
        queued = 0
        for r in workset.values():
            if r.get("status") == "discovered" and r.get("gate") in ("pass", "review"):
                r["status"] = "fit_checked"
                if r.get("fit_verdict") in (None, ""):
                    r["fit_verdict"] = "unscored"
                queued += 1
        return {"queued": queued, "skipped_by_gates": sum(1 for r in workset.values() if r.get("gate") == "fail")}

    stage("fetch", do_fetch)
    stage("hn", do_hn)
    stage("adzuna", do_adzuna)
    stage("himalayas", do_himalayas)
    stage("aidevjobs", do_aidevjobs)
    stage("remote_feeds", do_remote_feeds)
    stage("alerts", do_alerts)
    stage("dedup", do_dedup)
    stage("h1b", do_h1b)
    stage("gates", do_gates)
    stage("queue", do_queue)

    def do_triage() -> dict[str, Any]:
        # the deterministic triage score on every open, gated job (scripts/hunt_score.py)
        hctx = hunt_score.build_context(root, today=dept_day(now))
        ranked = hunt_score.apply_scores(list(workset.values()), hctx)
        return {"scored": len(ranked), "above_cut": sum(1 for r in ranked if r["hunt_score"] >= hctx["cut"]),
                "top": [{"id": r["id"], "hunt_score": r["hunt_score"], "company": r.get("company"),
                         "title": r.get("title")} for r in ranked[:5]]}

    stage("triage", do_triage)

    def do_funding() -> dict[str, Any]:
        # its own SEC client (User-Agent with the job-search contact, 8 requests a second); `edgar` injects a
        # transport in tests. Off unless sources.yaml says `enabled: true` (a missing entry is off too).
        out = funding_signals.run_stage(root, now, transport=edgar, dry_run=dry_run, jobs=list(workset.values()))
        if "skipped" not in out and not dry_run:
            update_sources_yaml(root / "sources.yaml", {"funding": {"last_run": iso_utc(now)}})
        if out.get("written"):
            receipt["notes"].append(f"funding signals: {out['written']}")
        return out

    stage("funding", do_funding)

    def do_news() -> dict[str, Any]:
        # listing and news pages through web_snapshot.py (targets/news-watch.yaml; terms-checked pages only)
        out = news_watch.run(root, today=dept_day(now), dry_run=dry_run)
        pages = out["pages"].values()
        return {"due": len(out["due"]), "read": sum(1 for p in pages if "error" not in p),
                "candidates": sum(len(p.get("candidates", [])) for p in pages),
                "signals": sum(len(p.get("signals", [])) for p in pages), "skipped": len(out["skipped"]),
                "errors": sum(1 for p in pages if "error" in p)}

    stage("news", do_news)

    def do_feeds() -> dict[str, Any]:
        # the companies' own blog and careers feeds (feed_watch.py), read by fixed rules
        import feed_watch
        out = feed_watch.watch(root, dept_day(now))
        res = {"feeds_read": out["feeds_read"], "posts": len(out["posts"]), "notes": len(out["notes"])}
        if not dry_run:
            path = root / "state" / "feeds" / f"feed-watch-{out['date']}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(feed_watch.render(out), encoding="utf-8")
            res["report"] = str(path.relative_to(root))
        return res

    stage("feeds", do_feeds)

    def do_discover() -> dict[str, Any]:
        # boards for the companies that posted yesterday and are not targets (discover_companies.py);
        # the whole previous day, since the 07:00 run has seen almost none of today's
        import discover_companies
        import scout_coverage
        covered = dept_day(now) - timedelta(days=1)          # named by the day its companies posted
        slugs = [c["slug"] for c in scout_coverage.report(covered, root, os_root)["new_companies"]]
        rows = discover_companies.discover(root, slugs[:DISCOVER_LIMIT], client(), load_title_filter(root))
        res = {"companies": len(rows), "boards": sum(1 for r in rows if r.get("vendor"))}
        if not dry_run:
            path = root / "state" / "reports" / f"new-companies-{covered.isoformat()}.md"
            discover_companies.write(path, discover_companies.render(rows, covered))
            res["report"] = str(path.relative_to(root))
        return res

    stage("discover", do_discover)

    if not dry_run:
        for rid, rec in workset.items():
            if before.get(rid) != _fingerprint(rec):
                save_job(root, rec)
        if ctx["cursor"] is not None:          # "read" only once what was read is saved
            _write_cursor(root, ctx["cursor"])
        for row in ctx["ledger_rows"]:
            append_jsonl(dedup.ledger_path(root), row)
        if ctx["hn_thread"] is not None:
            hn_hiring.save_state(root, ctx["hn_thread"], now)
        if ctx["alert_run"] is not None:
            job_alerts.write_outputs(root, ctx["alert_run"], now)
        updates = {sid: {"last_run": iso_utc(now), "yield_30d": yield_30d(workset, sid, now)} for sid in ctx["ran"]}
        update_sources_yaml(root / "sources.yaml", updates)

    def do_dashboard() -> dict[str, Any]:
        if dry_run:
            return {"written": False, "note": "dry-run"}
        dashboard.write(root, dashboard.build(root, os_root, now, focus=True))   #: the focus-next section stays
        return {"written": True}

    stage("dashboard", do_dashboard)
    ran_stages = [s for s in selected if s in STAGES]
    failed = [s for s in ran_stages if "error" in receipt["stages"].get(s, {})]
    receipt["status"] = "ok" if not receipt["errors"] else ("failed" if ran_stages and len(failed) == len(ran_stages) else "partial")
    receipt["duration_s"] = round((datetime.now(timezone.utc) - wall_start).total_seconds(), 1)
    receipt["routine"] = routine_verdict(receipt)
    if not dry_run:
        receipt["receipt_path"] = str(write_receipt(os_root, receipt, now))
    t0 = time.monotonic()
    receipt["stages"]["coverage"] = (do_coverage(root, os_root, now, dry_run) if "coverage" in selected
                                     else {"skipped": "not selected"})
    if "coverage" in selected:
        receipt["stage_s"]["coverage"] = round(time.monotonic() - t0, 1)
    if "line" in receipt["stages"]["coverage"]:
        receipt["routine"]["note"] += "; " + receipt["stages"]["coverage"].pop("line")
    return PipelineResult(receipt, workset)


def do_coverage(root: Path, os_root: Path, now: datetime, dry_run: bool) -> dict[str, Any]:
    """ row 1: scout_coverage.py over the day's receipts, this run's included (it runs after the receipt is
    written); the day's report is overwritten on each run, so the last run of the day leaves the day's measure."""
    import contextlib
    import io
    import scout_coverage
    day = dept_day(now)
    rel = Path("state") / "reports" / f"scout-coverage-{day.isoformat()}.md"
    try:
        if dry_run:
            r = scout_coverage.report(day, root, os_root)
        else:                                 # the CLI's --write --write-json (the dashboard reads the JSON)
            with contextlib.redirect_stdout(io.StringIO()):
                scout_coverage.main(["--date", day.isoformat(), "--root", str(root), "--os-root", str(os_root),
                                     "--write", "--write-json"])
            r = json.loads((Path(root) / rel.with_suffix(".json")).read_text(encoding="utf-8"))
    except Exception as exc:                  # a report, never a reason to fail the hunt; said in the note
        return {"error": f"{type(exc).__name__}: {exc}", "line": f"coverage: not measured ({type(exc).__name__})"}
    st = r["states"]
    out = {"coverage": r["coverage"], "read": st.get("read", 0), "capped": st.get("capped", 0),
           "error": st.get("error", 0), "no_reader": st.get("no-reader", 0), "targets": r["targets"]}
    pct = "no run" if r["coverage"] is None else f"{r['coverage']:.0%}"
    out["line"] = f"coverage {pct} (read {out['read']}, capped {out['capped']}, no reader {out['no_reader']})"
    if not dry_run:
        out["report"], out["report_json"] = str(rel), str(rel.with_suffix(".json"))
    return out


def _cursor_path(root: Path) -> Path:
    return Path(root) / "state" / "pipeline" / "fetch-cursor.json"


def _load_cursor(root: Path) -> dict[str, Any]:
    try:
        data = json.loads(_cursor_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def fetch_order(polled: list[dict[str, Any]], cursor: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """The boards waiting SLOW_LANE_H hours or more (deferred that long, or read that long ago) first, page crawlers
    included, so none starves; then the API boards the last run deferred (in their order, whatever their
    tier, so every board is read at least every second run; otherwise the tiers could keep the same boards in front
    and defer the rest run after run); then by tier (dream, strong, good or none,
    fallback), so a deadline reaches the dream boards last; within a tier the API boards before the page crawlers;
    within each, the boards deferred last run first (in their order), then the longest unread. Ahead of all of it, the
    must-read boards (must_read: dream and the one-request API boards), which are never deferred."""
    first = [s for s in cursor.get("deferred") or [] if any(c["slug"] == s for c in polled)]
    last_read = cursor.get("last_read") or {}
    since = cursor.get("deferred_since") or {}

    def waiting(slug: str) -> bool:                  # from the older of its last read and its first deferral
        if now is None:
            return False
        refs = [t for t in (parse_ts(last_read.get(slug)), parse_ts(since.get(slug)) if slug in first else None) if t]
        return bool(refs) and now - min(refs) >= timedelta(hours=SLOW_LANE_H)
    return sorted(polled, key=lambda c: (not must_read(c), not waiting(c["slug"]),
                                         not (c["slug"] in first and c.get("ats_vendor") not in PAGE_CRAWLERS),
                                         TIER_RANK.get(str(c.get("tier") or "").lower(), 2),
                                         c.get("ats_vendor") in PAGE_CRAWLERS,
                                         c["slug"] not in first, first.index(c["slug"]) if c["slug"] in first else 0,
                                         str(last_read.get(c["slug"]) or "")))


def must_read(company: dict[str, Any]) -> bool:
    """Dream and watchlist boards must never be deferred: a dream board (any reader) and every
    one-request API board (Ashby, Greenhouse, Lever and the other API_HOSTS vendors, a second or so each) is read every
    run, past the run deadline if need be (fetch_ats.MUST_READ_GRACE_S); only the many-request boards of other tiers
    (Workday searches, sitemap crawls, Oracle, ...) can wait for the next run."""
    vendor = str(company.get("ats_vendor") or "").lower()
    if str(company.get("tier") or "").lower() == "dream":
        return True
    return vendor in fetch_ats.API_HOSTS and vendor not in fetch_ats.HEAVY_VENDORS


def board_deadline(company: dict[str, Any], clock: Any, deadline: float | None) -> float | None:
    """A page crawler's own deadline: SITEMAP_BOARD_S from now, never past the run's fetch deadline."""
    if company.get("ats_vendor") not in PAGE_CRAWLERS:
        return deadline
    own = clock() + SITEMAP_BOARD_S
    return own if deadline is None else min(deadline, own)


def slow_lane(cursor: dict[str, Any], now: datetime) -> set[str]:
    """Boards resting this run: nothing new on each of their last SLOW_LANE_RUNS runs, each costing SLOW_LANE_MIN_S or
    more, and read within the last SLOW_LANE_H hours."""
    out = set()
    last_read = cursor.get("last_read") or {}
    for slug, rows in (cursor.get("yield") or {}).items():
        tail = list(rows or [])[-SLOW_LANE_RUNS:]
        if len(tail) < SLOW_LANE_RUNS or any(int(r.get("new") or 0) > 0 or float(r.get("s") or 0) < SLOW_LANE_MIN_S
                                             for r in tail):
            continue
        read = parse_ts(last_read.get(slug))
        if read is not None and now - read < timedelta(hours=SLOW_LANE_H):
            out.add(slug)
    return out


def _update_cursor(cursor: dict[str, Any], results: list[Any], deferred: list[str], now: datetime,
                   yields: dict[str, tuple[int, float]] | None = None) -> tuple[list[str], dict[str, Any]]:
    """Record the boards read (last_read: clean, whole reads only), deferred (with deferred_since) and each read board's
    yield (new postings and seconds, the last YIELD_KEEP runs); returns the deferred boards unread for UNREAD_LIMIT_H
    hours (from their last read, else from when they were first deferred) and the new cursor, which the run writes
    only after its jobs are saved (_write_cursor)."""
    stamp = iso_utc(now)
    yield_ = {k: list(v) for k, v in (cursor.get("yield") or {}).items()}
    for slug, (new, secs) in (yields or {}).items():
        yield_[slug] = (yield_.get(slug, []) + [{"at": stamp, "new": new, "s": secs}])[-YIELD_KEEP:]
    last_read = dict(cursor.get("last_read") or {})
    since = dict(cursor.get("deferred_since") or {})
    for res in results:
        last_read[res.company_slug] = stamp
        since.pop(res.company_slug, None)
    for slug in deferred:
        since.setdefault(slug, stamp)
    limit = now - timedelta(hours=UNREAD_LIMIT_H)
    late = []
    for slug in deferred:
        ref = parse_ts(last_read.get(slug)) or parse_ts(since.get(slug))
        if ref is not None and ref <= limit:
            late.append(slug)
    return late, {"deferred": deferred, "deferred_since": since, "last_read": last_read, "yield": yield_}


def _write_cursor(root: Path, data: dict[str, Any]) -> None:
    """Written atomically, under a temporary name of this process's own (a fixed name could let two writers swap files)."""
    path = _cursor_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def routine_verdict(receipt: dict[str, Any], limit: int | None = None) -> dict[str, str]:
    """The scheduled routine's status (ok, warn, failed), sentinel and one-line note for this run. Board errors alone,
    up to `limit`, are a warning: the boards that read fine still fed the run."""
    limit = BOARD_ERROR_LIMIT if limit is None else limit
    boards = list(receipt.get("board_errors") or [])
    others = [e for e in receipt.get("errors") or [] if e not in boards]
    fetch = (receipt.get("stages") or {}).get("fetch") or {}
    polled = fetch.get("polled")
    triage = (receipt.get("stages") or {}).get("triage") or {}
    why = []
    if receipt.get("status") == "failed":
        why.append("every stage failed")
    if others:
        why.append("stage errors: " + "; ".join(others))
    if polled == 0:
        why.append("no board polled")
    if len(boards) > limit:
        why.append(f"{len(boards)} board errors (over {limit})")
    late = list(receipt.get("unread_24h") or [])
    if late:
        why.append(f"boards unread for {UNREAD_LIMIT_H} h (deferred run after run): {', '.join(late)}")
    deferred = list(receipt.get("deferred_boards") or [])
    cut = list(receipt.get("cut_boards") or [])
    status = "failed" if why else ("warn" if boards or deferred or cut else "ok")
    top = "; ".join(f"{t.get('hunt_score')} {t.get('company')}: {t.get('title')}" for t in (triage.get("top") or [])[:3])
    parts = [f"exit {1 if receipt.get('status') == 'failed' else 0}"]
    if isinstance(polled, int):
        fine = max(polled - len(boards) - len(cut), 0)
        parts.append(f"boards polled {polled}; boards read fine {fine} of {polled}")
    if "new" in fetch:
        parts.append(f"new {fetch.get('new')}; closed {fetch.get('closed')}")
    parts.append(f"board_errors {len(boards)}" + (f" ({'; '.join(boards)})" if boards else ""))
    if deferred:
        parts.append(f"deferred {len(deferred)} boards to the next run: {', '.join(deferred[:8])}"
                     + (f" and {len(deferred) - 8} more" if len(deferred) > 8 else ""))
    if cut:
        parts.append(f"cut {len(cut)} at the run deadline: {', '.join(cut[:8])}" + (f" and {len(cut) - 8} more" if len(cut) > 8 else ""))
    if "above_cut" in triage:
        parts.append(f"above cut {triage['above_cut']}")
    if top:
        parts.append(f"top 3: {top}")
    secs = receipt.get("stage_s") or {}
    if secs:
        slow = max(secs, key=secs.get)
        parts.append(f"slowest: {slow} {secs[slow]:.0f} s; run {receipt.get('duration_s', sum(secs.values())):.0f} s")
    vendor_s = fetch.get("vendor_s") or {}
    if vendor_s:                         #: the fetch's wall time and where it went (board seconds; boards overlap)
        wall = f"fetch {secs['fetch']:.0f} s with {fetch.get('workers', 1)} workers; " if "fetch" in secs else ""
        parts.append(wall + "board seconds by vendor: " + ", ".join(
            f"{v} {row['s']:.0f} (max {row['slowest']} {row['max_s']:.0f})" for v, row in list(vendor_s.items())[:4]))
    if why:
        parts.append("failed because " + "; ".join(why))
    return {"status": status, "sentinel": "NEEDS_YOU" if status == "failed" else "NO_ACTION", "note": "; ".join(parts)}


def _kernel_watchdog() -> Any:
    """The kernel's watchdog.py, loaded by path (a PyPI package of the same name must not shadow it); it writes under
    whatever os root it is given."""
    import importlib.util
    from js_common import _KERNEL
    spec = importlib.util.spec_from_file_location("os_kernel_watchdog", _KERNEL / "watchdog.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("os_kernel_watchdog", module)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None, now: datetime | None = None, env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--os-root", type=Path, default=None, help="runtime root (default: derived from --root)")
    ap.add_argument("--stages", default="", help="comma list, subset of: " + ",".join(STAGES))
    ap.add_argument("--force-hn", action="store_true", help="run the HN stage even if it ran this month")
    ap.add_argument("--daily", default="", help="comma list of selected feed stages run at most once a department "
                    "day (their sources.yaml last_run): " + ",".join(DAILY_SOURCES))
    ap.add_argument("--max-pages", type=int, default=20, help="workday page cap")
    ap.add_argument("--workday-page-size", type=int, default=20, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the receipt as JSON")
    ap.add_argument("--routine-receipt", metavar="NAME", help="append the routine verdict as NAME's run receipt")
    ap.add_argument("--workers", type=int, default=fetch_ats.FETCH_WORKERS,
                    help="boards read at once, one per host (default %(default)s; 1: one board at a time)")
    ap.add_argument("--budget-min", type=float, default=20.0,
                    help="run budget in minutes (fetch gets 60%%, the boards left lead the next run; 0: none)")
    args = ap.parse_args(argv)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()] or None
    unknown = [s for s in stages or [] if s not in STAGES]
    if unknown:
        ap.error(f"unknown stages {unknown}; choose from {', '.join(STAGES)}")
    args.daily = [s.strip() for s in args.daily.split(",") if s.strip()]
    if [s for s in args.daily if s not in DAILY_SOURCES]:
        ap.error(f"--daily takes feed stages only: {', '.join(DAILY_SOURCES)}")
    if args.os_root is None:
        args.os_root = os_root_for(Path(args.root))
    if args.dry_run:                         # a dry run writes nothing, so it never waits for the lock
        return _main_run(args, stages, now, env)
    lock, why = _take_lock(Path(args.root), stages or list(DEFAULT_STAGES))
    if lock is None:
        return _skipped(args, stages, now, why)
    try:
        return _main_run(args, stages, now, env)
    finally:
        _release_lock(lock)


LOCK_WAIT_S = 30 * 60           # how long a run that is not a second poll waits for the lock
_lock_sleep, _lock_clock = time.sleep, time.monotonic


def _take_lock(root: Path, stages: list[str]) -> tuple[Any, str]:
    """One run at a time: overlapping runs would lose the first run's cursor writes and write stale job records.
    An exclusive flock on state/pipeline/run.lock, released by the kernel
    when the process ends, however it ends (a kill leaves no stale lock). The holder writes its stages into the
    file. A poll (`fetch`) that finds a poll holding the lock (or a holder that wrote nothing) skips at once: the same
    work is under way. Any other overlap (js-hunt-signals and the 07:00 poll) waits up to LOCK_WAIT_S for the lock.
    Returns (the open lock file, "") or (None, the reason to skip)."""
    import errno
    import fcntl
    path = Path(root) / "state" / "pipeline" / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    t0, said, confirmed = _lock_clock(), False, False
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):   # not "held": a real failure
                fh.close()
                raise
            held = _lock_holder(path)
            if "fetch" in stages and (held is None or "fetch" in held):
                if not confirmed:                # a holder may be between taking the lock and writing its stages
                    confirmed = True
                    _lock_sleep(1)
                    continue
                fh.close()
                return None, "skipped: previous run in progress (state/pipeline/run.lock is held)"
            waited = _lock_clock() - t0
            if not said:
                print(f"waiting for the lock (held by a run of {', '.join(held or [])}), up to {LOCK_WAIT_S:g} s",
                      file=sys.stderr)
                said = True
            if waited >= LOCK_WAIT_S:
                fh.close()
                return None, f"skipped: waited {waited:.0f} s for the lock (held by a run of {', '.join(held or [])})"
            _lock_sleep(5)
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "stages": list(stages)}))
        fh.flush()
    except BaseException:                    # a failed holder record never leaks the lock
        fh.close()
        raise
    return fh, ""


def _release_lock(fh: Any) -> None:
    """Clear the holder record (no stale stages for the next reader), then release the lock."""
    try:
        fh.seek(0)
        fh.truncate()
    except OSError:
        pass
    fh.close()


def _lock_holder(path: Path) -> list[str] | None:
    """The stages the lock's holder wrote; None when it wrote none (an older run, or not written yet)."""
    try:
        stages = json.loads(path.read_text(encoding="utf-8") or "null").get("stages")
        return [str(s) for s in stages] if isinstance(stages, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def _started(args: Any, stages: list[str] | None, started: datetime) -> tuple[Any, str]:
    """Write "started" first, so a run killed before its verdict is seen (watchdog: started with no end = ERROR)."""
    wd = _kernel_watchdog()
    run_id = started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = [f"run_id: {run_id}", f"stages: {','.join(stages or DEFAULT_STAGES)}"]
    if getattr(args, "budget_min", None):
        notes.append(f"budget_min: {args.budget_min:g}")
    wd.write_receipt(args.os_root, args.routine_receipt, started, status="started", notes=notes, day=dept_day(started))
    return wd, run_id


def _arm_guard(args: Any, wd: Any, run_id: str | None, started: datetime) -> Any:
    """A real run (not a dry run) with a budget gets the whole-run limit (run_guard.limit_s). At the
    limit the guard writes every thread's stack to state/pipeline/hang-<run_id>.txt, this run's failed receipt naming
    the frame it hung in, and exits 3: a hang is a true failure in about 35 minutes, never two silent hours."""
    import run_guard
    limit = None if args.dry_run else run_guard.limit_s(args.budget_min)
    label = run_id or started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def expire(top: str, stack: Path) -> None:
        if wd is None:
            return
        wd.write_receipt(args.os_root, args.routine_receipt, datetime.now(timezone.utc), status="failed",
                         sentinel="NEEDS_YOU",
                         notes=[f"exit {run_guard.EXIT_CODE}; stopped by its run guard after {run_guard.span(limit)} "
                                f"(budget {args.budget_min:g} min): hung in {top}; stack {stack}",
                                f"run_id: {run_id}"],
                         day=dept_day(started))

    return run_guard.arm(limit, Path(args.root) / "state" / "pipeline", label, expire)


def _skipped(args: Any, stages: list[str] | None, now: datetime | None, msg: str) -> int:
    """Another run holds the lock: a warn receipt closes this run's started line, exit 0 (nothing was wrong). With --json the skip prints its JSON too, so a caller never reads an empty stdout
    (the shape of a crash) for a skip."""
    print(msg, file=sys.stderr)
    if getattr(args, "json", False):
        print(json.dumps({"routine": ROUTINE, "status": "skipped", "skipped": msg}, indent=2))
    if args.routine_receipt:
        started = now or datetime.now(timezone.utc)
        wd, run_id = _started(args, stages, started)
        wd.write_receipt(args.os_root, args.routine_receipt, now or datetime.now(timezone.utc), status="warn",
                         sentinel="NO_ACTION", notes=[msg, f"run_id: {run_id}"], day=dept_day(started))
    return 0


def _main_run(args: Any, stages: list[str] | None, now: datetime | None, env: Mapping[str, str] | None) -> int:
    wd, run_id, started = None, None, now or datetime.now(timezone.utc)
    if args.routine_receipt and not args.dry_run:
        wd, run_id = _started(args, stages, started)
    guard = _arm_guard(args, wd, run_id, started)
    try:
        return _run_and_report(args, stages, now, env, wd, run_id, started)
    finally:                                 # every way out, an interrupt included, disarms
        guard.disarm()


def _run_and_report(args: Any, stages: list[str] | None, now: datetime | None, env: Mapping[str, str] | None,
                    wd: Any, run_id: str | None, started: datetime) -> int:
    try:
        res = run_pipeline(args.root, args.os_root, now, http=None, stages=stages, dry_run=args.dry_run, env=env,
                           force_hn=args.force_hn, max_pages=args.max_pages, workday_page_size=args.workday_page_size,
                           budget_min=args.budget_min or None, workers=args.workers,
                           daily=getattr(args, "daily", None) or ())
    except Exception as exc:                 # a crash still ends the run it started (a kill cannot: the watchdog sees it)
        if wd is not None:
            when = now or datetime.now(timezone.utc)
            wd.write_receipt(args.os_root, args.routine_receipt, when, status="failed", sentinel="NEEDS_YOU",
                             notes=[f"exit 1; the run raised {type(exc).__name__}: {exc}", f"run_id: {run_id}"],
                             day=dept_day(started))
        print(f"ERROR the run raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    r = res.receipt
    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        for name, result in r["stages"].items():
            print(f"{name:9} {_fmt(result)}")
        for e in r["errors"]:
            print(f"ERROR {e}")
        print(f"status {r['status']}" + (" (dry-run, nothing written)" if args.dry_run else f"; receipt {r['receipt_path']}"))
    if args.routine_receipt and not args.dry_run:
        v = r["routine"]
        when = now or datetime.now(timezone.utc)
        path = wd.write_receipt(args.os_root, args.routine_receipt, when, status=v["status"],
                                notes=[v["note"], f"run_id: {run_id}"], sentinel=v["sentinel"], day=dept_day(started))
        if not args.json:
            print(f"routine {args.routine_receipt}: {v['status']} ({v['sentinel']}); receipt {path}")
    return 0 if r["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
