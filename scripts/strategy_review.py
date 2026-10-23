#!/usr/bin/env python3
"""strategy_review.py - the Lead Recruiter's daily strategy numbers: what worked, and rule flags. Reads the records;
writes two files, state/recruiter/review-<date>.md and proposals-<date>.md (--json writes neither).

For yesterday, the last 7 days and the last 30 days (a cohort: applications by their applied date, outcomes as of
today): applications, replies, screens, interviews, rejections and silent-after-10-business-days, overall and by
  tier dream, focused, mass: the newest apply-batch sheet that lists the application
             (agency_status.batches, the same rule as the agency brief), else the tier rule marked "(inferred)":
             dream = fit at TIER_DREAM_FIT or more, or a dream company; focused = fit at TIER_FOCUSED_FIT or more;
             mass = below that; unknown without a fit and not a dream company (policy.strategy.tier_dream_fit,
             tier_focused_fit)
  segment    targets/companies.yaml `segment` (scripts/segment_companies.py), else the same recorded evidence for a
             company off the watchlist (segment_companies.segment_for), else prospects.infer_company_type, else unknown
  focus      the focus profile focus_profiles.pick gives the title, else unknown
  channel    the recorded `path` first: cold = cold apply, unknown = unknown; person = the recorded
             `channel` when present, else who carried it: referral (applied_via referral, or the via_prospect or a
             linked prospect tagged referrer), recruiter firm (applied_via recruiter, or a prospect at a recruiting
             firm), founder note (a founder), else "person (kind not recorded)". Without `path`, the recorded
             `channel`, else the linked prospects as before: referral, recruiter firm, founder note, else cold apply
  fit_band   the job's fit_score: under 3.5, 3.5-4.0, 4.0-4.5, 4.5+, unknown
plus the median days to the first reply and the share of applied roles at fit 4.0+ (among those with a fit
score; the count is shown).
A reply is an inbound event (tracker.INBOUND) or a company stage (tracker.RESPONDED) after the application.

Flags, each with the number behind it: a bucket with NO_REPLY_TRIES or more applications in 30 days and no reply;
dream-list share (targets/companies.yaml tier dream) under DREAM_SHARE_MIN of 30-day applications; big-company share
(big-tech and enterprise) over BIG_SHARE_MAX; no interview by day INTERVIEW_BY_DAY of the job clock (clock.yaml); median
fit of 30-day applications under FIT_MEDIAN_MIN. The limits come from the policy.strategy config section. Every count comes from tracker.py's events and projection and the job loader; no record is re-parsed here.

Learned: the shared learning record state/learn/<role>.jsonl (scripts/learn.py) is summarised
in the review: rows by status and the candidates still proposed in the last 7 days, invalid lines counted.

Writes state/recruiter/review-<date>.md; --json prints the same data as JSON instead.

The Decide section is the Recruiter's, never this script's: when review-<date>.md or
proposals-<date>.md already holds a line "## Decide", everything from that line to the end of the file is kept verbatim
and appended after the regenerated content; a new file ends with a "## Decide" heading and a one-line placeholder.

Ranking weight proposals: each run also writes state/recruiter/proposals-<date>.md, a PROPOSAL for the
Recruiter and the owner, never applied (targets/hunt.yaml is not written; it is created only on the owner's word). Rules, from
interviews.rates (applications that reached an interview, by `path`: person = warm, cold = cold) and tracker.metrics
(apply latency):
  warm        only when BOTH sides have MIN_N (15) applications or more (two outcomes never drive a multiplier): a warm path worth WARM_RATIO_MIN (2x) or more a cold application
              (interviews.rates, shrunk) doubles hunt_score's warm weight, at most once per review. Below MIN_N the
              rates are shown with their n and no ratio is stated or proposed
  freshness   a median posting-to-application time over FRESH_HOURS_MAX (72 h) adds FRESH_STEP (4) to freshness
  mass        while the pause ruling named by policy.strategy.pause_ruling stands (hub/rulings.yaml status standing),
              a status line counts the 30-day mass-tier applications and their replies
A number that is not measured changes nothing. The effect is measured by rescoring the open jobs with hunt_score under
both weight sets: jobs over the cut before and after, the top 10 kept, in and out, and a cut that keeps as many jobs
(counted after the rescore, ties included). --no-effect skips the rescore; --no-effect still writes both files, but
never replaces a proposals file for the same day that holds a measured effect.

CLI:  python3 scripts/strategy_review.py [--root R] [--today YYYY-MM-DD] [--json] [--no-effect]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_comms_util as cu  # noqa: E402
import tracker as tr  # noqa: E402
from fetch_ats import load_companies, load_jobs  # noqa: E402
from js_common import (DEPT_TZ, JS_ROOT, MIN_TRIES, cli_date, normalize_company, os_root_for, today,  # noqa: E402
                       weekly_interview_floor)
import agency_config as cfg  # noqa: E402

WINDOWS = (("yesterday", 1), ("7d", 7), ("30d", 30))
DIMENSIONS = ("tier", "segment", "focus", "channel", "fit_band")
OUTCOMES = ("applications", "replies", "screens", "interviews", "rejections", "silent_10bd")
# the owner's strategy limits: policy.strategy.* in the config (defaults in config/defaults.d)
NO_REPLY_TRIES = int(cfg.get("policy.strategy.no_reply_tries", 15))
DREAM_SHARE_MIN = float(cfg.get("policy.strategy.dream_share_min", 0.20))
BIG_SHARE_MAX = float(cfg.get("policy.strategy.big_share_max", 0.60))
INTERVIEW_BY_DAY = int(cfg.get("policy.strategy.interview_by_day", 21))
FIT_MEDIAN_MIN = float(cfg.get("policy.strategy.fit_median_min", 4.0))
TIER_DREAM_FIT = float(cfg.get("policy.strategy.tier_dream_fit", 4.5))      # fit at or over this: tier dream
TIER_FOCUSED_FIT = float(cfg.get("policy.strategy.tier_focused_fit", 4.0))  # fit at or over this: tier focused
SEGMENT_OF_TYPE = {"ai-lab": "ai-lab", "bigtech": "big-tech", "startup": "startup", "scaleup": "growth",
                   "enterprise": "enterprise"}
INTERVIEW_STAGES = ("oa", "interviewing", "final")
WARM_RATIO_MIN = 2.0
WARM_MAX_STEP = 2.0             # the warm weight at most doubles in one review (small samples)
FRESH_HOURS_MAX = 72
FRESH_STEP = 4
SMALL_N = 10
MIN_N = MIN_TRIES               # about 15 tries a side before a ratio drives anything;
                                # one constant with lessons.py (js_common.MIN_TRIES)
CHANNEL_NAMES = {"referral": "referral", "recruiter": "recruiter firm", "recruiter firm": "recruiter firm",
                 "founder": "founder note", "founder note": "founder note", "post": "post author",
                 "post author": "post author", "cold": "cold apply", "cold apply": "cold apply"}
DECIDE = "## Decide"
DECIDE_PLACEHOLDER = ("- not written yet: the Recruiter writes the decision here (what changes, on whose word, the "
                      "evidence and a review date); this script never writes this section.")
CUT_STEPS = 20                                  # the proposed cut moves down at most this many points


def business_days_back(day: date, n: int) -> date:
    d = day
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def fit_band(fit: Any) -> str:
    try:
        f = float(fit)
    except (TypeError, ValueError):
        return "unknown"
    return "under 3.5" if f < 3.5 else "3.5-4.0" if f < 4.0 else "4.0-4.5" if f < 4.5 else "4.5+"


def _tiers(root: Path) -> dict[str, str]:
    import agency_status
    out: dict[str, str] = {}
    for sheet in agency_status.batches(root):          # sorted by name = date: the newest sheet wins
        for slug in sheet.get("slugs") or []:
            out[str(slug)] = str(sheet.get("tier") or "mass")
    return out


def _prospects(root: Path) -> dict[str, dict[str, Any]]:
    import prospects
    try:
        rows = prospects.list_prospects(cu.Roots(js=root, os=os_root_for(root)))
    except Exception:                                  # a broken prospect file must not hide the numbers
        return {}
    return {str(p.get("id")): p for p in rows if p.get("id")}


def _channel(props: dict[str, Any], people: dict[str, dict[str, Any]]) -> str:
    path = str(props.get("path") or "").strip().lower()
    recorded = str(props.get("channel") or "").strip().lower()
    if path == "cold":
        return "cold apply"
    if path == "unknown":
        return "unknown"
    if recorded:
        return CHANNEL_NAMES.get(recorded, recorded)
    ids = list(props.get("prospects") or []) + ([props["via_prospect"]] if props.get("via_prospect") else [])
    linked = [p for p in (people.get(str(pid)) for pid in ids) if p]
    via = str(props.get("applied_via") or "").lower()
    if "referral" in via or any("referrer" in (p.get("tags") or []) for p in linked):
        return "referral"
    if via == "recruiter" or any(p.get("company_type") == "recruiting-firm" for p in linked):
        return "recruiter firm"
    if any(p.get("role") == "founder" for p in linked):
        return "founder note"
    return "person (kind not recorded)" if path == "person" else "cold apply"


def _segment(company: str, slug: str, recorded: dict[str, str], ev: Any = None) -> str:
    """targets/companies.yaml `segment` first, then the same recorded evidence for a company off the watchlist
    (segment_companies.segment_for: dossiers, job records ...), else prospects.infer_company_type."""
    seg = recorded.get(slug) or recorded.get(normalize_company(company))
    if seg:
        return seg
    if ev is not None:
        found, _ = ev.decide(company, slug or "")
        if found:
            return found
    import prospects
    return SEGMENT_OF_TYPE.get(prospects.infer_company_type(company, None, "", None), "unknown")


def infer_tier(fit: Any, dream_company: bool) -> str:
    """The tier rule for an application no apply-batch sheet lists: dream = fit at TIER_DREAM_FIT or more, or a dream
    company; focused = fit at TIER_FOCUSED_FIT or more; mass = below that; marked inferred. Never written to a record."""
    try:
        f = float(fit)
    except (TypeError, ValueError):
        f = None
    if dream_company or (f is not None and f >= TIER_DREAM_FIT):
        return "dream (inferred)"
    if f is None:
        return "unknown"
    return "focused (inferred)" if f >= TIER_FOCUSED_FIT else "mass (inferred)"


def dream_names(root: Path) -> set[str]:
    """Dream companies: targets/companies.yaml tier dream, the one source. The research
    lists (research/dream/DREAM-COMPANIES.md, DREAM-BOARD.md) are research, not the tier."""
    return {normalize_company(c["name"]) for c in load_companies(root) if str(c.get("tier")) == "dream"} - {""}


def dream_slugs(root: Path, index: Any = None) -> set[str]:
    """The tier-dream companies as slugs. A record naming one by another spelling
    ("Acme" for "Acme Corporation") resolves through company_index at the call site."""
    from company_index import CompanyIndex
    ix = index or CompanyIndex.load(root)
    out = {str(c.get("slug")) for c in load_companies(root) if str(c.get("tier")) == "dream" and c.get("slug")}
    return out | {ix.resolve(n)[0] for n in dream_names(root)} - {None, ""}   # names resolve to the same slugs


_PROSPECTS: dict[str, tuple[tuple, list]] = {}


def _prospect_metas(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """prospects/ parsed once per change: keyed by every file's name, mtime and size (fresh10 asks once per row)."""
    import js_common
    folder = Path(root) / "prospects"
    files = sorted(folder.glob("*.md")) if folder.is_dir() else []
    sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    hit = _PROSPECTS.get(str(folder.resolve()))
    if hit and hit[0] == sig:
        return hit[1]
    rows = [(path, props) for path, props, _ in js_common.iter_records(folder)]
    _PROSPECTS[str(folder.resolve())] = (sig, rows)
    return rows


def is_warm(props: dict[str, Any]) -> bool:
    """A prospect is a warm path: warmth 1+ or tagged referrer, never do_not_contact."""
    if str(props.get("status") or "") == "do_not_contact":
        return False
    try:
        warmth = float(props.get("warmth") or 0)
    except (TypeError, ValueError):
        warmth = 0.0
    return warmth >= 1 or "referrer" in (props.get("tags") or [])


def warm_paths(root: Path, index: Any, company_slug: str) -> list[str]:
    """Prospects at the company that are a warm path (is_warm): a cold target is not a warm path."""
    out = []
    for path, props in _prospect_metas(root):
        if not is_warm(props):
            continue
        slug = index.resolve(str(props.get("company_slug") or "") or str(props.get("company") or ""))[0]
        if slug and slug == company_slug:
            out.append(str(props.get("id") or path.stem))
    return sorted(out)


def infer_application_tier(root: Path, slug: str) -> tuple[str, str]:
    """The tier of one application and why: dream = a dream company, a warm path, or fit at TIER_DREAM_FIT or
    more; focused = fit at TIER_FOCUSED_FIT or more; mass = under that. The fit is fit.md's `score`, else the job record's fit_score. Raises tr.TrackerError when
    there is no record, or no fit and no dream signal (never a guess)."""
    from company_index import CompanyIndex
    from js_common import fm
    path = root / "applications" / slug / "application.md"
    if not path.is_file():
        raise tr.TrackerError(f"refused: no application record for {slug!r}")
    props = fm.load(path)[0]
    fit, fit_src = None, None
    fit_md = root / "applications" / slug / "fit.md"
    if fit_md.is_file():
        try:
            fprops = fm.load(fit_md)[0]
            fit, fit_src = fprops.get("score", fprops.get("fit_score")), "fit.md"
        except ValueError:
            fit = None
    if fit is None:
        job = load_jobs(root).get(tr._link_target(props.get("job")) or "", {})
        fit, fit_src = job.get("fit_score"), "the job record"
    try:
        fit = float(fit) if fit is not None else None
    except (TypeError, ValueError):
        fit = None
    index = CompanyIndex.load(root)
    company = index.resolve(str(props.get("company_slug") or "") or str(props.get("company") or ""))[0]
    listed = bool(company) and company in dream_slugs(root, index)
    warm = warm_paths(root, index, company) if company else []
    parts = ["dream company (companies.yaml tier dream)" if listed else "not a dream company",
             f"fit {fit:g} ({fit_src})" if fit is not None else "no fit score",
             f"warm path: {', '.join(warm)}" if warm else "warm path: none"]
    if listed or warm or (fit is not None and fit >= TIER_DREAM_FIT):
        tier = "dream"
    elif fit is None:
        raise tr.TrackerError(f"refused: cannot infer {slug}'s tier: {'; '.join(parts)} (the rule needs a fit or a "
                              "dream signal); set it with --source")
    else:
        tier = "focused" if fit >= TIER_FOCUSED_FIT else "mass"
    return tier, "inferred by the tier rule (tracker.py tier --infer): " + "; ".join(parts)


def application_tier(props: dict[str, Any], slug: str, sheet_tiers: dict[str, str], fit: Any, dream: bool) -> str:
    """The recorded `tier` (tracker.py tier; "(inferred)" when its tier_source says inferred), else the newest
    apply-batch sheet's, else infer_tier, marked "(inferred)"."""
    recorded = str(props.get("tier") or "")
    if recorded in tr.APP_TIERS:                      # a backfill from the rule stays marked until someone sets it
        return recorded + (" (inferred)" if str(props.get("tier_source") or "").startswith("inferred") else "")
    return sheet_tiers.get(slug) or infer_tier(fit, dream)


def _focus(root: Path, title: str, cache: dict[str, str]) -> str:
    if title not in cache:
        import focus_profiles
        try:
            ranked = focus_profiles.pick(root, title)
        except Exception:
            ranked = []
        cache[title] = ranked[0]["family"] if ranked else "unknown"
    return cache[title]


def collect(root: Path, day: date) -> list[dict[str, Any]]:
    """One row per application with an applied time: its buckets and outcomes as of `day`."""
    now = datetime(day.year, day.month, day.day, 23, 59, tzinfo=timezone.utc)
    jobs = load_jobs(root)
    tiers, people, focus_cache = _tiers(root), _prospects(root), {}
    companies = load_companies(root)
    from company_index import CompanyIndex
    index = CompanyIndex.load(root)
    dream = dream_slugs(root, index)
    import segment_companies
    ev = segment_companies.Evidence(root)
    recorded = {}
    for c in companies:
        if c.get("segment"):
            recorded[str(c.get("slug"))] = str(c["segment"])
            recorded[normalize_company(c["name"])] = str(c["segment"])
    silent_cut = business_days_back(day, 10)
    rows = []
    for slug, props, body in tr.iter_applications(root) or []:
        events = tr.parse_events(body)
        proj = tr.project(events, now, props.get("ghost_after_days"), props.get("last_inbound_at"))
        applied = proj.get("applied_at")
        if applied is None:
            continue
        replies = sorted(at for at in (tr.parse_ts(e.at) for e in events
                                       if e.type.strip().lower() in tr.INBOUND
                                       or tr.EVENT_STAGE.get(e.type.strip().lower()) in tr.RESPONDED)
                         if at and at >= applied)
        job = jobs.get(tr._link_target(props.get("job")) or "", {})
        company, title = str(props.get("company") or job.get("company") or ""), str(props.get("title") or "")
        is_dream = index.resolve(str(props.get("company_slug") or "") or company)[0] in dream if company else False
        entered = proj.get("entered") or {}
        rows.append({
            "application": slug, "company": company or "unknown", "applied": tr._day(applied),
            "tier": application_tier(props, slug, tiers, job.get("fit_score"), is_dream),
            "segment": _segment(company, str(props.get("company_slug") or ""), recorded, ev) if company else "unknown",
            "focus": _focus(root, title, focus_cache) if title else "unknown", "channel": _channel(props, people),
            "fit": job.get("fit_score"), "fit_band": fit_band(job.get("fit_score")),
            "dream_list": is_dream,
            "replies": bool(replies), "first_reply_days": (replies[0] - applied).total_seconds() / 86400 if replies else None,
            "screens": "screen" in entered, "interviews": any(s in entered for s in INTERVIEW_STAGES),
            "rejections": "rejected" in entered,
            "silent_10bd": not replies and applied.date() <= silent_cut,
        })
    return rows


def _tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"applications": len(rows), **{k: sum(1 for r in rows if r[k]) for k in OUTCOMES[1:]}}


def window(rows: list[dict[str, Any]], day: date, days: int) -> dict[str, Any]:
    if days == 1:
        sel = [r for r in rows if r["applied"] == day - timedelta(days=1)]
    else:
        sel = [r for r in rows if day - timedelta(days=days) <= r["applied"] <= day]
    by: dict[str, dict[str, dict[str, int]]] = {}
    for dim in DIMENSIONS:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in sel:
            groups[str(r[dim])].append(r)
        by[dim] = {k: _tally(v) for k, v in sorted(groups.items())}
    waits = [r["first_reply_days"] for r in sel if r["first_reply_days"] is not None]
    fits = [float(r["fit"]) for r in sel if fit_band(r["fit"]) != "unknown"]
    return {"totals": _tally(sel), "by": by,
            "time_to_first_reply_days": {"median": round(statistics.median(waits), 1) if waits else None,
                                         "measured": len(waits)},
            "fit_4_plus_share": round(sum(1 for f in fits if f >= 4.0) / len(fits), 4) if fits else None,
            "fit_measured": len(fits),                 # applications whose job carries a fit score
            "median_fit": round(statistics.median(fits), 2) if fits else None,
            "dream_list_share": round(sum(r["dream_list"] for r in sel) / len(sel), 4) if sel else None,
            "big_company_share": round(sum(r["segment"] in ("big-tech", "enterprise") for r in sel) / len(sel), 4)
            if sel else None}


def clock_info(root: Path, day: date) -> dict[str, Any]:
    """The job clock's day (clock.yaml through clock.py): {"day": n}, {"state": "absent"} or {"error": why}."""
    import clock as job_clock
    try:
        cfg = job_clock.load(root)
    except job_clock.ClockError as exc:
        return {"error": str(exc)}
    return {"state": "absent"} if cfg is None else {"day": job_clock.status(cfg, day).get("day")}


def flags(root: Path, day: date, rows: list[dict[str, Any]], w30: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for dim, groups in w30["by"].items():
        for bucket, t in groups.items():
            if t["applications"] >= NO_REPLY_TRIES and t["replies"] == 0:
                out.append({"rule": "no-replies", "value": t["applications"],
                            "detail": f"{dim} {bucket}: {t['applications']} tries, 0 replies in the last 30 days"})
    n = w30["totals"]["applications"]
    if n and w30["dream_list_share"] is not None and w30["dream_list_share"] < DREAM_SHARE_MIN:
        out.append({"rule": "dream-share", "value": w30["dream_list_share"],
                    "detail": f"dream-list share {w30['dream_list_share']:.0%} of {n} applications (under {DREAM_SHARE_MIN:.0%})"})
    if n and w30["big_company_share"] is not None and w30["big_company_share"] > BIG_SHARE_MAX:
        out.append({"rule": "big-company-share", "value": w30["big_company_share"],
                    "detail": f"big-company share {w30['big_company_share']:.0%} of {n} applications (over {BIG_SHARE_MAX:.0%})"})
    clock_day = clock_info(root, day).get("day")
    if clock_day and clock_day >= INTERVIEW_BY_DAY and not any(r["interviews"] for r in rows):
        out.append({"rule": f"no-interview-by-day-{INTERVIEW_BY_DAY}", "value": clock_day,
                    "detail": f"day {clock_day} of the job clock and no interview yet"})
    if w30["median_fit"] is not None and w30["median_fit"] < FIT_MEDIAN_MIN:
        out.append({"rule": "median-fit", "value": w30["median_fit"],
                    "detail": f"median fit {w30['median_fit']} of {n} applications (under {FIT_MEDIAN_MIN:g})"})
    return out


def review(root: Path, day: date) -> dict[str, Any]:
    root = Path(root)
    rows = collect(root, day)
    windows = {name: window(rows, day, days) for name, days in WINDOWS}
    return {"date": day.isoformat(), "applications_on_record": len(rows), "clock": clock_info(root, day),
            "windows": windows, "interviews": interviews_view(root, day, windows["7d"]),
            "flags": flags(root, day, rows, windows["30d"]), "learned": learned_view(root, day)}


def learned_view(root: Path, day: date) -> dict[str, Any]:
    """The shared learning record (state/learn, learn.py): rows by status now, the proposed ones of the last 7 days."""
    import learn
    rows, invalid = learn.read(root)
    cur = learn.current(rows)
    since = (day - timedelta(days=7)).isoformat()
    return {"by_status": {s: sum(1 for r in cur if r["status"] == s) for s in learn.STATUSES},
            "proposed_7d": [r for r in cur if r["status"] == "proposed" and since <= r["date"] <= day.isoformat()],
            "invalid": len(invalid)}


def interviews_view(root: Path, day: date, w7: dict[str, Any]) -> dict[str, Any]:
    """Interviews booked against the target, the measured channel rates, and proposals (never applied): the
    weekly target the rates support, and how many warm and cold applications a target needs. The rates come by `path`
    (interviews.rates); a ratio between the paths is stated and proposed only with MIN_N applications on each side,
    else `small_n` says so with the counts."""
    import interviews as iv
    from datetime import datetime
    r = iv.rates(root, datetime.now(DEPT_TZ))
    b = iv.booked(root, day)
    applied_7d = (w7.get("totals") or {}).get("applications", 0)
    enough = r["warm_n"] >= MIN_N and r["cold_n"] >= MIN_N        # no multiplier below MIN_N a side
    ratio = round(r["warm"] / r["cold"], 1) if r["cold"] and enough else None
    out = {"booked": b, "target": weekly_interview_floor(root), "rates": r, "applied_7d": applied_7d,
           "warm_vs_cold": ratio,
           "min_n": MIN_N, "small_n": None, "proposals": []}
    if not enough:
        out["small_n"] = (f"n is too small for a multiplier: person {r['warm_k']} of {r['warm_n']}, cold "
                          f"{r['cold_k']} of {r['cold_n']}; the paths need {MIN_N} applications on each side "
                          "before a ratio between the paths is stated or drives a weight")
    if ratio and ratio >= 2:
        out["proposals"].append(f"a warm path is worth about {ratio}x a cold application (warm {r['warm_k']} of "
                                f"{r['warm_n']}, cold {r['cold_k']} of {r['cold_n']}, shrunk): weigh the warm path in "
                                "hunt_score and Fresh 10 at least that much, and name a person before applying")
    small = "" if enough else f": under {MIN_N} a side, a small sample"
    for t in (2, 3):
        warm_needed = max(0, round((t - r["cold"] * 10) / r["warm"])) if r["warm"] else None
        out["proposals"].append(f"a target of {t} interviews a week needs about {warm_needed} warm applications a week "
                                f"plus 10 cold ones at the measured rates (person n={r['warm_n']}, cold "
                                f"n={r['cold_n']}{small})")
    return out


def pause_stands(root: Path) -> bool:
    """The pause ruling named by policy.strategy.pause_ruling (applications only through the focused lanes) holds
    while hub/rulings.yaml records it as standing. Off when no ruling is named."""
    import yaml
    ruling_id = str(cfg.get("policy.strategy.pause_ruling", "") or "")
    if not ruling_id:
        return False
    try:
        data = yaml.safe_load((Path(root) / "hub" / "rulings.yaml").read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return False
    rulings = data if isinstance(data, list) else data.get("rulings") or []
    return any(isinstance(x, dict) and x.get("id") == ruling_id and x.get("status") == "standing" for x in rulings)


def _n_note(n: int) -> str:
    return ", a small sample" if n < SMALL_N else ""


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"


def weight_proposal(current: dict[str, float], rates: dict[str, Any] | None, latency: dict[str, Any] | None,
                    mass: dict[str, int], paused: bool = False, *, k206: bool | None = None) -> dict[str, Any]:
    """The proposed hunt_score weights and why, from the measured rates and apply speed. Pure: reads nothing.
    `k206` is the former name of `paused`, still accepted for callers not yet renamed."""
    paused = bool(paused if k206 is None else k206)
    proposed = {k: (int(v) if float(v).is_integer() else round(v, 1)) for k, v in current.items()}
    changes, held = [], []
    if rates and rates.get("cold") and rates.get("warm_n") and rates.get("cold_n"):
        ratio = rates["warm"] / rates["cold"]
        counts = (f"warm {rates['warm_k']} of {rates['warm_n']}{_n_note(rates['warm_n'])}, cold {rates['cold_k']} of "
                  f"{rates['cold_n']}{_n_note(rates['cold_n'])}, shrunk to {rates['warm']:.0%} and {rates['cold']:.0%}")
        if rates["warm_n"] < MIN_N or rates["cold_n"] < MIN_N:      # two outcomes never drive a multiplier
            held.append(f"warm unchanged: n is too small for a multiplier ({counts}); a ratio needs {MIN_N} "
                        "applications on each side")
        elif ratio >= WARM_RATIO_MIN:
            new = round(proposed["warm"] * min(ratio, WARM_MAX_STEP), 1)
            new = int(new) if float(new).is_integer() else new
            changes.append({"weight": "warm", "from": proposed["warm"], "to": new,
                            "why": f"a warm path is worth {ratio:.1f}x a cold application ({counts}); the weight at "
                                   f"most doubles per review"})
            proposed["warm"] = new
        else:
            held.append(f"warm unchanged: a warm path is worth {ratio:.1f}x a cold application ({counts}), under "
                        f"{WARM_RATIO_MIN:.0f}x")
    elif rates:
        held.append(f"warm unchanged: the warm and cold interview rates are not measured (warm {rates.get('warm_k', 0)} "
                    f"of {rates.get('warm_n', 0)}, cold {rates.get('cold_k', 0)} of {rates.get('cold_n', 0)}: one side "
                    f"has no applications)")
    else:
        held.append("warm unchanged: the warm and cold interview rates are not measured")
    med = (latency or {}).get("median")
    if med is None:
        held.append("freshness unchanged: the posting-to-application time is not measured")
    else:
        speed = (f"median {med:g} h from posting to application, {latency.get('within_72h', 0)} of "
                 f"{latency.get('measured', 0)} within 72 h{_n_note(latency.get('measured', 0))}")
        if med > FRESH_HOURS_MAX:
            changes.append({"weight": "freshness", "from": proposed["freshness"],
                            "to": proposed["freshness"] + FRESH_STEP,
                            "why": f"{speed}; a higher freshness weight puts new postings higher in the list"})
            proposed["freshness"] += FRESH_STEP
        else:
            held.append(f"freshness unchanged: {speed}")
    lines = []
    if paused:
        lines.append(f"The applications pause stands (applications through the focused lanes only): "
                     f"{_plural(mass.get('applications', 0), 'mass-tier application')} in the last 30 days, "
                     f"{_plural(mass.get('replies', 0), 'reply', 'replies')}. No weight change is proposed for it.")
    return {"current": dict(current), "proposed": proposed, "changes": changes, "held": held, "mass": lines}


def proposal_effect(jobs: list[dict[str, Any]], current: dict[str, float], proposed: dict[str, float], cut: float,
                    scorer: Any) -> dict[str, Any]:
    """Rescore the jobs under both weight sets: scorer(rec, weights, cut) -> score (the cut feeds hunt_score's gates)."""
    before = sorted(((scorer(j, current, cut), str(j.get("id"))) for j in jobs), key=lambda t: (-t[0], t[1]))
    after = sorted(((scorer(j, proposed, cut), str(j.get("id"))) for j in jobs), key=lambda t: (-t[0], t[1]))
    top_b, top_a = [i for _, i in before[:10]], [i for _, i in after[:10]]
    n_before = sum(1 for sc, _ in before if sc >= cut)
    # the whole-number cut proposed: start at the n-th score after, rescore with hunt_score's gates at that cut (an
    # off-role job is capped at cut - 1, so it never counts) and step down until the cut keeps as many jobs as before,
    # ties included. Only jobs scoring at or over the candidate under the old cut can reach it.
    new_cut, keeps = None, None
    if n_before:
        first = {i: sc for sc, i in after}
        new_cut = math.floor(after[n_before - 1][0])
        for _ in range(CUT_STEPS):
            cand = [j for j in jobs if first[str(j.get("id"))] >= new_cut]
            keeps = sum(1 for j in cand if scorer(j, proposed, new_cut) >= new_cut)
            if keeps >= n_before:
                break
            new_cut -= 1
    return {"scored": len(jobs), "cut": cut,
            "cut_same_count": after[n_before - 1][0] if n_before else None,
            "cut_new": new_cut, "cut_new_keeps": keeps,
            "over_cut_before": n_before,
            "over_cut_after": sum(1 for sc, _ in after if sc >= cut),
            "top10_kept": len(set(top_b) & set(top_a)),
            "top10_in": [i for i in top_a if i not in top_b], "top10_out": [i for i in top_b if i not in top_a]}


def live_effect(root: Path, day: date, current: dict[str, float], proposed: dict[str, float]) -> dict[str, Any]:
    """proposal_effect over the open, scoreable jobs with hunt_score's own score()."""
    import hunt_score as hs
    ctx = hs.build_context(root, today=day)

    def norm(w: dict[str, float]) -> dict[str, float]:
        total = sum(w.values())
        return {k: v * 100.0 / total for k, v in w.items()}

    def scorer(rec: dict[str, Any], w: dict[str, float], cut: float) -> float:
        return hs.score(dict(rec), {**ctx, "weights": norm(w), "cut": cut})["hunt_score"]
    jobs = [r for r in load_jobs(root).values() if hs.scoreable(r)]
    return proposal_effect(jobs, current, proposed, ctx["cut"], scorer)


MEASURED = "open jobs rescored"                 # the effect line render_proposals writes when the rescore ran


def render_proposals(day: date, p: dict[str, Any], effect: dict[str, Any] | None) -> str:
    lines = [f"# Ranking weight proposals {day.isoformat()}", "",
             "Built by `scripts/strategy_review.py` from interviews.rates (person path and "
             "cold applications that reached an interview) and tracker.metrics (the posting-to-application time). A proposal for the Recruiter and the owner, never applied by "
             "this script: hunt_score keeps its current weights until targets/hunt.yaml exists, and that file is "
             "created only on the owner's word.", "", "## Proposed changes", ""]
    lines += [f"- {c['weight']}: {c['from']} to {c['to']}. {c['why']}." for c in p["changes"]] or ["- none"]
    lines += ["", "## Held", ""] + ([f"- {h}." for h in p["held"]] or ["- none"])
    if p["mass"]:
        lines += ["", "## Mass tier", ""] + [f"- {m}" for m in p["mass"]]
    lines += ["", "## Effect on the open jobs", ""]
    if effect is None and not p["changes"]:
        lines += ["- nothing to measure: no weight change is proposed"]
    elif effect is None:
        lines += ["- not measured in this run (--no-effect)"]
    else:
        lines += [f"- jobs at or over the cut ({effect['cut']:g}): {effect['over_cut_before']} to "
                  f"{effect['over_cut_after']}, of {effect['scored']} {MEASURED}",
                  f"- the top 10 by hunt score: {effect['top10_kept']} kept; in: "
                  f"{', '.join(effect['top10_in']) or 'none'}; out: {', '.join(effect['top10_out']) or 'none'}"]
        new_cut = effect.get("cut_new")
        if new_cut is not None and new_cut != effect["cut"]:
            lines += [f"- cut: {effect['cut']:g} to {new_cut} keeps {effect['cut_new_keeps']} jobs at or over it "
                      f"({effect['over_cut_before']} before) "
                      f"(the weights would sum to {sum(p['proposed'].values()):g}, and hunt_score rescales them to 100, "
                      f"so every score moves); proposed with the weights"]
    lines += ["", "## To apply (on the owner's word only)", "",
              "Write targets/hunt.yaml with these weights, then `python3 scripts/hunt_score.py` rescores:", "",
              "```yaml", "weights:"] + [f"  {k}: {v}" for k, v in p["proposed"].items()]
    if effect and effect.get("cut_new") is not None and effect["cut_new"] != effect["cut"]:
        lines += [f"cut: {effect['cut_new']}"]
    lines += ["```"]
    return "\n".join(lines) + "\n"


def write_proposals(root: Path, day: date, text: str) -> Path:
    path = Path(root) / "state" / "recruiter" / f"proposals-{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def proposals(root: Path, day: date, r: dict[str, Any], effect: bool = True) -> tuple[dict[str, Any], dict | None]:
    """The proposal from this review's numbers: rates as interviews_view measured them, latency from tracker.metrics."""
    import hunt_score as hs
    m = tr.metrics(Path(root), datetime(day.year, day.month, day.day, 23, 59, tzinfo=timezone.utc)) or {}
    rates = (r.get("interviews") or {}).get("rates")
    by_tier = r["windows"]["30d"]["by"]["tier"]
    mass = {"applications": sum(v["applications"] for k, v in by_tier.items() if k.startswith("mass")),
            "replies": sum(v["replies"] for k, v in by_tier.items() if k.startswith("mass"))}
    current = hs._config(Path(root))["weights"]
    current = {k: (int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 1)) for k, v in current.items()}
    p = weight_proposal(current, rates, m.get("apply_latency_hours"), mass, pause_stands(root))
    eff = live_effect(Path(root), day, current, p["proposed"]) if effect and p["changes"] else None
    return p, eff


def render(r: dict[str, Any]) -> str:
    lines = [f"# Strategy review {r['date']}", "",
             f"Built by `scripts/strategy_review.py` from tracker.py events, the apply-batch sheets and the job "
             f"records: {r['applications_on_record']} applications on record. A window is a cohort by applied date; "
             "outcomes are as of today. `unknown` means no record holds the value.", "",
             f"- Job clock: {r['clock'].get('day') and 'day ' + str(r['clock']['day']) or r['clock'].get('error') or 'no clock.yaml'}",
             ""]
    iv_ = r.get("interviews") or {}
    if iv_:
        bk, rt = iv_["booked"], iv_["rates"]
        lines += ["## Interviews (the goal)", "",
                  f"- booked this week (from {bk['week_of']}): {bk['this_week']}; last week {bk['last_week']}; weekly "
                  f"floor {iv_['target'] if iv_['target'] else 'not set (the owner sets it from a proposal below)'} "
                  "(targets/goals.yaml; no ceiling)",
                  f"- interview rate per application by path: person {rt['warm_k']} of {rt['warm_n']} (n={rt['warm_n']}), "
                  f"cold {rt['cold_k']} of {rt['cold_n']} (n={rt['cold_n']}), unknown {rt.get('unknown_n', 0)} not "
                  f"counted (shrunk toward the overall rate: {rt['warm']:.0%} and {rt['cold']:.0%})",
                  f"- applications in the last 7 days: {iv_['applied_7d']}"]
        lines += [f"- {iv_['small_n']}"] if iv_.get("small_n") else []
        lines += ["", "Proposals (never applied by this script; the Recruiter takes them to the owner):", ""]
        lines += [f"- {p_}" for p_ in iv_["proposals"]] or ["- none"]
        lines += [""]
    lines += ["## Flags", ""]
    lines += [f"- {f['rule']}: {f['detail']}" for f in r["flags"]] or ["- none"]
    lv = r.get("learned")
    if lv:
        lines += ["", "## Learned (state/learn, scripts/learn.py)", "",
                  "- " + ", ".join(f"{k} {v}" for k, v in lv["by_status"].items())
                  + (f"; {lv['invalid']} invalid lines" if lv["invalid"] else "")]
        lines += [f"- proposed {x['date']} by {x['role']}: {x['observation']} (evidence: {x['evidence']})"
                  for x in lv["proposed_7d"]]
    titles = {"yesterday": "Yesterday", "7d": "Last 7 days", "30d": "Last 30 days"}
    for name, w in r["windows"].items():
        t = w["totals"]
        lines += ["", f"## {titles[name]}", "",
                  "- " + ", ".join(f"{k.replace('_', ' ')} {t[k]}" for k in OUTCOMES),
                  f"- time to first reply: median "
                  f"{'n/a' if w['time_to_first_reply_days']['median'] is None else str(w['time_to_first_reply_days']['median']) + ' days'} "
                  f"({w['time_to_first_reply_days']['measured']} measured); fit 4.0+ share "
                  f"{'n/a' if w['fit_4_plus_share'] is None else format(w['fit_4_plus_share'], '.0%')} of the "
                  f"{w['fit_measured']} with a fit score; dream-list "
                  f"share {'n/a' if w['dream_list_share'] is None else format(w['dream_list_share'], '.0%')}"]
        if not t["applications"]:
            continue
        for dim, groups in w["by"].items():
            lines += ["", f"| {dim} | " + " | ".join(OUTCOMES) + " |", "|---|" + "---|" * len(OUTCOMES)]
            lines += [f"| {k} | " + " | ".join(str(v[o]) for o in OUTCOMES) + " |" for k, v in groups.items()]
    return "\n".join(lines) + "\n"


def with_decide(path: Path, text: str) -> str:
    """The regenerated text plus the Decide section: from the first line "## Decide" of the file already at `path` to
    its end, kept verbatim; else the heading and a one-line placeholder."""
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    m = re.search(r"(?m)^## Decide(?![A-Za-z])", old)
    kept = old[m.start():] if m else f"{DECIDE}\n\n{DECIDE_PLACEHOLDER}\n"
    return text.rstrip("\n") + "\n\n" + kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-effect", action="store_true", help="skip rescoring the open jobs; still writes the review and the proposals, "
                    "but keeps a proposals file for the day that holds a measured effect")
    args = ap.parse_args(argv)
    day = args.today or today()
    r = review(args.root, day)
    if args.json:
        print(json.dumps(r, indent=1, default=str))
        return 0
    path = Path(args.root) / "state" / "recruiter" / f"review-{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(with_decide(path, render(r)), encoding="utf-8")
    os.replace(tmp, path)
    print(f"wrote {path.relative_to(args.root)}: {len(r['flags'])} flags")
    prop, eff = proposals(args.root, day, r, effect=not args.no_effect)
    ppath = Path(args.root) / "state" / "recruiter" / f"proposals-{day.isoformat()}.md"
    if args.no_effect and ppath.is_file() and MEASURED in ppath.read_text(encoding="utf-8"):
        print(f"kept {ppath.relative_to(args.root)}: it holds a measured effect and --no-effect measured none")
        return 0
    ppath = write_proposals(args.root, day, with_decide(ppath, render_proposals(day, prop, eff)))
    print(f"wrote {ppath.relative_to(args.root)}: {len(prop['changes'])} changes (a proposal, never applied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
