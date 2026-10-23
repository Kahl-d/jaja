#!/usr/bin/env python3
"""scout_coverage.py: how much of the known job surface the scouts checked on a day, and what they found.

The scouts should cover the known surface every day and find fresh openings, by scrapers and APIs (the browser
only where nothing else can read), deterministically and never by guesswork. This report is the daily measure of that
surface. It reads, never fetches:
  - targets/companies.yaml: every target company and its access (ats_vendor, board_token, careers_url);
  - the day's runs in logs/runs/js-scout/<date>.md (the pipeline's receipt): which boards erred, which were
    skipped and why, which hit a page cap;
  - sources.yaml: every feed and API source, enabled or not, and its last run;
  - jobs/*.md: postings first seen that day, by source, and the companies among them that are not targets yet.

Per target company the state is one of:
  read     polled through its public API or sitemap, no error, no cap
  capped   polled, but pages were left for a later run (the board is larger than one run reads)
  error    polled and failed (the receipt's Errors)
  deferred not read in any run of the day: the run budget deferred it or the slow lane rested it
  no-reader its access has no adapter yet (ats_vendor other, unknown, icims, taleo ...) or no verified board token:
           the surface we know about and do not read; each needs an adapter, a feed, or a manual mark
Coverage = (read + capped) / targets. A day with no run reports "no run", never zero.

Usage: python3 scripts/scout_coverage.py [--date YYYY-MM-DD] [--write] [--json]
--write saves state/reports/scout-coverage-<date>.md. Exit 0 (a report, not a gate); 2 on a usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
from agency_paths import RUNTIME_ROOT as OS_ROOT  # noqa: E402
import agency_config as agency_cfg  # noqa: E402
LA = ZoneInfo(str(agency_cfg.get("identity.timezone", "UTC") or "UTC"))      # the local day of the report
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
RUN = re.compile(r"^## Run (\S+) · status (\S+)", re.M)
NOTE = re.compile(r"^- ([a-z0-9][a-z0-9-]*)(?: \(([a-z]+)\))?: (.*)$", re.M)
# boards a run did not read: the run budget deferred them, or the slow lane rested them
DEFERRED = re.compile(r"^- deferred to the next run \([^)]*\): (.*)$", re.M)
RESTING = re.compile(r"^- slow lane \(.*?\): (.*?)\. Recruiter", re.M)


def targets(root: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load((root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    rows = data.get("companies") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("slug")]


def runs_of(day: date, os_root: Path) -> list[dict[str, Any]]:
    """The pipeline runs whose local date (identity.timezone) is `day`, from the receipts of that UTC day and the next."""
    out = []
    for d in (day, day + timedelta(days=1)):
        p = os_root / "logs" / "runs" / "js-scout" / f"{d.isoformat()}.md"
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        marks = list(RUN.finditer(text))
        for i, m in enumerate(marks):
            try:
                at = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.astimezone(LA).date() != day:
                continue
            body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            errors = body.split("\nErrors:", 1)[1].split("\nNotes:", 1)[0] if "\nErrors:" in body else ""
            notes = body.split("\nNotes:", 1)[1] if "\nNotes:" in body else ""
            out.append({"at": m.group(1), "status": m.group(2), "fetched": "| fetch |" in body
                        and "| fetch | skipped" not in body,
                        "errors": {n.group(1): n.group(3) for n in NOTE.finditer(errors)},
                        "notes": {n.group(1): n.group(3) for n in NOTE.finditer(notes)},
                        "unread": {s.strip() for m in (*DEFERRED.finditer(notes), *RESTING.finditer(notes))
                                   for s in m.group(1).split(",") if s.strip()}})
    return out


def company_state(slug: str, runs: list[dict[str, Any]]) -> tuple[str, str]:
    fetch_runs = [r for r in runs if r["fetched"]]
    if not fetch_runs:
        return "no run", ""
    reading = [r for r in fetch_runs if slug not in r.get("unread", ())]
    if not reading:                                          # deferred or resting in every run of the day
        return "deferred", "not read today (run budget or slow lane)"
    if any(slug not in r["errors"] for r in reading):        # one clean read in the day is a read
        ok_runs = [r for r in reading if slug not in r["errors"]]
        note = next((r["notes"][slug] for r in reversed(ok_runs) if slug in r["notes"]), "")
        if "unsupported ats_vendor" in note or "no board_token" in note or "skipped" in note:
            return "no-reader", note
        if "page cap" in note or "left for the next run" in note:
            return "capped", note
        return "read", note
    return "error", reading[-1]["errors"][slug]


def new_jobs(root: Path, day: date) -> list[dict[str, Any]]:
    out = []
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for p in (root / "jobs").glob("*.md"):
        head = p.read_text(encoding="utf-8", errors="replace")[:4000]
        if "first_seen" not in head:
            continue
        m = FM.match(p.read_text(encoding="utf-8", errors="replace"))
        try:
            meta = yaml.load(m.group(1), Loader=loader) if m else None  # noqa: S506 (a safe loader)
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        seen = str(meta.get("first_seen") or "")
        try:
            local = datetime.fromisoformat(seen.replace("Z", "+00:00"))
            local = (local if local.tzinfo else local.replace(tzinfo=timezone.utc)).astimezone(LA).date()
        except ValueError:
            continue
        if local == day:
            out.append({"id": p.stem, "company": meta.get("company"), "slug": meta.get("company_slug"),
                        "source": meta.get("source") or "?", "gate": meta.get("gate"), "title": meta.get("title")})
    return out


def report(day: date, root: Path = JS_ROOT, os_root: Path = OS_ROOT) -> dict[str, Any]:
    rows = targets(root)
    runs = runs_of(day, os_root)
    states: dict[str, list[dict[str, str]]] = defaultdict(list)
    no_reader = Counter()
    for r in rows:
        st, why = company_state(r["slug"], runs)
        states[st].append({"slug": r["slug"], "vendor": str(r.get("ats_vendor")), "why": why})
        if st == "no-reader":
            no_reader[str(r.get("ats_vendor"))] += 1
    src_path = root / "sources.yaml"
    srcs = ((yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}).get("sources") or []) if src_path.is_file() else []
    feeds = [{"id": s.get("id"), "kind": s.get("kind"), "enabled": bool(s.get("enabled")),
              "last_run": str(s.get("last_run") or "")[:19]} for s in srcs if isinstance(s, dict)]
    fresh = new_jobs(root, day)
    target_slugs = {r["slug"] for r in rows}
    found = Counter(j["slug"] for j in fresh if j["slug"] and j["slug"] not in target_slugs)
    n = len(rows) or 1
    covered = len(states.get("read", [])) + len(states.get("capped", []))
    return {"date": day.isoformat(), "runs": len(runs), "run_status": [f"{r['at']} {r['status']}" for r in runs],
            "targets": len(rows), "coverage": None if not runs else round(covered / n, 3),
            "states": {k: len(v) for k, v in states.items()}, "no_reader_by_vendor": dict(no_reader.most_common()),
            "capped": states.get("capped", []), "errors": states.get("error", []),
            "no_reader": states.get("no-reader", []),
            "feeds": feeds, "new_postings": len(fresh),
            "new_by_source": dict(Counter(j["source"] for j in fresh).most_common()),
            "new_passing_gates": sum(1 for j in fresh if j["gate"] in ("pass", "review")),
            "new_companies": [{"slug": s, "postings": c} for s, c in found.most_common()]}


def render(r: dict[str, Any]) -> str:
    cov = "no run" if r["coverage"] is None else f"{r['coverage']:.0%}"
    out = [f"# Scout coverage · {r['date']} ", "",
           f"Runs: {r['runs']} ({', '.join(r['run_status']) or 'none'}). Targets: {r['targets']}. "
           f"Coverage (read or capped): **{cov}**.", "",
           "| state | companies |", "|---|--:|"]
    out += [f"| {k} | {v} |" for k, v in sorted(r["states"].items())]
    out += ["", f"**No reader yet, by access:** " + (", ".join(f"{k} {v}" for k, v in r["no_reader_by_vendor"].items())
                                                    or "none"), ""]
    if r["capped"]:
        out += ["**Capped (more pages than one run reads):** " + ", ".join(f"{c['slug']} ({c['why'][:40]})"
                                                                            for c in r["capped"]), ""]
    if r["errors"]:
        out += ["**Errors:** " + "; ".join(f"{c['slug']}: {c['why'][:80]}" for c in r["errors"]), ""]
    out += [f"## Found", "", f"New postings first seen: {r['new_postings']} "
            f"({r['new_passing_gates']} pass or review the gates). By source: "
            + (", ".join(f"{k} {v}" for k, v in r["new_by_source"].items()) or "none") + ".", ""]
    out += ["New companies (postings from companies not in targets/companies.yaml): "
            + (", ".join(f"{c['slug']} ({c['postings']})" for c in r["new_companies"][:30]) or "none") + ".", ""]
    out += ["## Feeds and APIs", "", "| source | kind | enabled | last run |", "|---|---|---|---|"]
    out += [f"| {f['id']} | {f['kind']} | {'yes' if f['enabled'] else 'no'} | {f['last_run'] or 'never'} |"
            for f in r["feeds"]]
    return "\n".join(out) + "\n"


def latest_json(root: Path) -> Path | None:
    """The newest saved report (state/reports/scout-coverage-<date>.json), by date in the name."""
    found = sorted((Path(root) / "state" / "reports").glob("scout-coverage-*.json"))
    return found[-1] if found else None


def summary(root: Path) -> dict[str, Any]:
    """The six numbers the dashboard and the agency brief show, from the newest saved JSON (never recomputed).
    Missing is None: no file, or a day with no scout run (coverage None), never zero."""
    p = latest_json(root)
    out: dict[str, Any] = {k: None for k in ("date", "coverage", "read", "capped", "no_reader", "new_postings",
                                             "new_companies", "source")}
    if p is None:
        return out
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    ran = r.get("coverage") is not None
    states = r.get("states") or {}
    companies = r.get("new_companies")
    out.update(date=r.get("date"), coverage=r.get("coverage"), new_postings=r.get("new_postings"),
               new_companies=len(companies) if isinstance(companies, list) else None,
               read=states.get("read", 0) if ran else None, capped=states.get("capped", 0) if ran else None,
               no_reader=states.get("no-reader", 0) if ran else None,
               source=p.relative_to(root).as_posix())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=None)
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--os-root", type=Path, default=OS_ROOT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-json", action="store_true",
                    help="also save state/reports/scout-coverage-<date>.json (the dashboard and brief read it)")
    a = ap.parse_args(argv)
    try:
        day = date.fromisoformat(a.date) if a.date else datetime.now(LA).date()
    except ValueError:
        print(f"--date expects YYYY-MM-DD, got {a.date!r}", file=sys.stderr)
        return 2
    r = report(day, a.root, a.os_root)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    if a.write_json:
        import os
        import tempfile
        jp = a.root / "state" / "reports" / f"scout-coverage-{day.isoformat()}.json"
        jp.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=jp.parent)
        os.write(fd, json.dumps(r, indent=2).encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, jp)
        print(f"wrote {jp.relative_to(a.root)}")
    text = render(r)
    if a.write:
        p = a.root / "state" / "reports" / f"scout-coverage-{day.isoformat()}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(a.root)}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
