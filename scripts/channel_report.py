#!/usr/bin/env python3
"""channel_report.py - the weekly channel report (agency charter 0.1 item 7, section 13): which way in gets screens.

    python3 scripts/channel_report.py [--today YYYY-MM-DD] [--write] [--json] [--root JS]

Every application with an applied event (tracker.py projection) is put in one channel:
  recruiter  path person, and the via_prospect is on targets/agency-recruiters.yaml or its record says role recruiter or
             sourcer, or company_type recruiting-firm or agency
  post       path person through a prospect with a hiring_post_url, or any application whose job carries a
             hiring_signal
  person     path person, any other prospect (referrals, introductions, people in conversation)
  cold       path cold
  unknown    no path recorded (tracker.py path backfills it)
For each channel, this week (applied in the 7 days ending --today) and all time: n, replies (the company answered:
tracker.py RESPONDED, a rejection included) and screens (the application reached a screen or further, ever; a screen
later rejected still counts), with screens per 10 tries. Fewer than 10 tries is marked "too few to call".

Base order (tracking which layout works): the order of the resume each application sent: its `base_order`
frontmatter field when the mass lane recorded one (education_first or experience_first), else read from the
resume.yaml of its resume_variant folder (the first of `education` or `experience` under cv.sections), else unknown. Compared on the base pages (resumes/bases/ or a variant folder named base*: the
mass A/B) and on every page. The record of which order each application used is the per-application table of the
report.

Writes state/review/channel-report-<date>.md with --write (the Recruiter's evening wrap runs it); otherwise prints it.
Reads only records; never sends or changes a record.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import tracker  # noqa: E402

CHANNELS = ("person", "recruiter", "post", "cold", "unknown")
ORDERS = ("education_first", "experience_first")
RECRUITER_ROLES = {"recruiter", "sourcer"}
RECRUITER_TYPES = {"recruiting-firm", "agency"}
SMALL_N = 10


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    meta, _ = jc.read_record(path)
    return meta or {}


def listed_recruiters(root: Path) -> set[str]:
    path = root / "targets" / "agency-recruiters.yaml"
    if not path.is_file():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(r.get("prospect")) for r in data.get("recruiters") or [] if r.get("prospect")}


def channel_of(root: Path, meta: dict[str, Any], listed: set[str]) -> str:
    job = _read(root / "jobs" / f"{tracker._link_target(meta.get('job')) or '_none'}.md")
    path = meta.get("path")
    if path == "person":
        pid = str(meta.get("via_prospect") or "")
        p = _read(root / "prospects" / f"{pid}.md") if pid else {}
        if pid in listed or p.get("role") in RECRUITER_ROLES or p.get("company_type") in RECRUITER_TYPES:
            return "recruiter"
        if p.get("hiring_post_url") or job.get("hiring_signal"):
            return "post"
        return "person"
    if job.get("hiring_signal"):
        return "post"
    return "cold" if path == "cold" else "unknown"


def _order_word(value: Any) -> str | None:
    v = str(value or "").strip().lower()
    if v.startswith("edu"):
        return "education_first"
    if v.startswith("exp"):
        return "experience_first"
    return None


def order_of(root: Path, variant: Any, recorded: Any = None) -> str:
    """education_first, experience_first or unknown: the application's own `base_order` (written by the mass lane when
    it sends) wins; else the sections order of the resume.yaml in its resume_variant folder."""
    word = _order_word(recorded)
    if word:
        return word
    if not variant:
        return "unknown"
    path = root / str(variant).rstrip("/") / "resume.yaml"
    if not path.is_file():
        return "unknown"
    try:
        sections = ((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cv") or {}).get("sections") or {}
    except yaml.YAMLError:
        return "unknown"
    for key in sections:
        k = str(key).lower()
        if k.startswith("education"):
            return "education_first"
        if k.startswith("experience"):
            return "experience_first"
    return "unknown"


def classify(root: Path, meta: dict[str, Any], listed: set[str] | None = None) -> tuple[str, str]:
    """(channel, base order) of one application record; tracker.py metrics reports both next to `path`."""
    listed = listed_recruiters(root) if listed is None else listed
    return channel_of(root, meta, listed), order_of(root, meta.get("resume_variant"), meta.get("base_order"))


def is_base(variant: Any) -> bool:
    v = str(variant or "").rstrip("/")
    return v.startswith("resumes/bases/") or Path(v).name.startswith("base")


def _blank() -> dict[str, int]:
    return {"n": 0, "replies": 0, "screens": 0}


def report(root: Path, today: date) -> dict[str, Any]:
    root = Path(root)
    now = datetime.combine(today, time(23, 59), tzinfo=timezone.utc)
    week_start = today - timedelta(days=6)
    listed = listed_recruiters(root)
    screen_rank = tracker.PROGRESS.index("screen")
    channels = {w: {c: _blank() for c in CHANNELS} for w in ("week", "all")}
    orders = {s: {o: _blank() for o in ORDERS} for s in ("base", "all")}
    unknown_order, rows = 0, []
    for slug, meta, body in tracker.iter_applications(root) or []:
        events = tracker.parse_events(body)
        proj = tracker.project(events, now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        if proj["applied_at"] is None:
            continue
        replied = any(tracker.EVENT_STAGE.get(e.type.strip().lower()) in tracker.RESPONDED
                      or (e.type.lower().startswith("override:") and e.type.split(":", 1)[1].strip() in tracker.RESPONDED)
                      for e in events)
        screened = tracker.PROGRESS.index(proj["max_progress"]) >= screen_rank
        channel = channel_of(root, meta, listed)
        order = order_of(root, meta.get("resume_variant"), meta.get("base_order"))
        applied_day = jc.dept_day(proj["applied_at"])
        hit = {"n": 1, "replies": int(replied), "screens": int(screened)}
        buckets = [channels["all"][channel]] + ([channels["week"][channel]] if applied_day >= week_start else [])
        if order in ORDERS:
            buckets.append(orders["all"][order])
            if is_base(meta.get("resume_variant")):
                buckets.append(orders["base"][order])
        else:
            unknown_order += 1
        for b in buckets:
            for k, v in hit.items():
                b[k] += v
        rows.append({"slug": slug, "applied": applied_day.isoformat(), "channel": channel, "order": order,
                     "base": is_base(meta.get("resume_variant")), "replied": replied, "screened": screened})
    return {"date": today.isoformat(), "week_start": week_start.isoformat(), "channels": channels,
            "base_order": {**orders, "unknown": unknown_order}, "applications": rows}


def _per10(c: dict[str, int]) -> str:
    return f"{10 * c['screens'] / c['n']:.1f}" if c["n"] else "-"


def _table(rows: dict[str, dict[str, int]], label: str) -> list[str]:
    out = [f"| {label} | n | replies | screens | screens per 10 |", "|---|---|---|---|---|"]
    for name, c in rows.items():
        out.append(f"| {name} | {c['n']} | {c['replies']} | {c['screens']} | {_per10(c)} |")
    small = [n for n, c in rows.items() if 0 < c["n"] < SMALL_N]
    if small:
        out += ["", f"Under {SMALL_N} tries, too few to call: {', '.join(small)}."]
    return out


def render(rep: dict[str, Any]) -> str:
    ch, bo = rep["channels"], rep["base_order"]
    L = [f"# Channel report {rep['date']}", "",
         "Which way in gets screens (agency charter 0.1 item 7). Replies count any answer from the company, a rejection "
         "included; screens count an application that ever reached a screen or further. Built by "
         "scripts/channel_report.py from the application records.", "",
         f"## This week ({rep['week_start']} to {rep['date']})", "", *_table(ch["week"], "channel"), "",
         "## All time", "", *_table(ch["all"], "channel"), "",
         "## Base order: education first against experience first", "",
         "### Base pages (the mass A/B)", "", *_table(bo["base"], "order"), "",
         "### Every page", "", *_table(bo["all"], "order"), "",
         f"Order unknown (no resume_variant with a resume.yaml): {bo['unknown']} applications.", "",
         "## Per application", "", "| application | applied | channel | order | base | replied | screen |",
         "|---|---|---|---|---|---|---|"]
    for r in sorted(rep["applications"], key=lambda r: (r["applied"], r["slug"])):
        L.append(f"| {r['slug']} | {r['applied']} | {r['channel']} | {r['order']} | {'yes' if r['base'] else 'no'} | "
                 f"{'yes' if r['replied'] else 'no'} | {'yes' if r['screened'] else 'no'} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today", type=jc.cli_date, default=None)
    ap.add_argument("--write", action="store_true", help="write state/review/channel-report-<date>.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = args.today or jc.today()
    rep = report(args.root, today)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0
    text = render(rep)
    if args.write:
        out = Path(args.root) / "state" / "review" / f"channel-report-{rep['date']}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    week, allt = rep["channels"]["week"], rep["channels"]["all"]
    for c in CHANNELS:
        print(f"{c}: week n {week[c]['n']} screens {week[c]['screens']} · all n {allt[c]['n']} replies "
              f"{allt[c]['replies']} screens {allt[c]['screens']}")
    if not args.write:
        print("dry run: nothing written (--write writes the report)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
