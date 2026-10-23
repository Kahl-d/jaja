#!/usr/bin/env python3
"""The daily fresh-roles review: every new find since the last review, ranked, with its next action.

    python3 scripts/hunt_review.py [--root R] [--today YYYY-MM-DD] [--now ISO] [--capture] [--dry-run] [--json]

Reads jobs/ (with `hunt_score` from hunt_score.py or the pipeline's triage stage), prospects/, targets/companies.yaml
and hub/NEXT-UP.md, the one ranked list. The window runs from the last review (state/hunt/last-review.json) to now, or
the last 24 hours on the first run. A find is listed when its triage score is at the cut (targets/hunt.yaml, default
72) or it already has a fit score of 3.5 or more; the rest are counted. Each listed find gets one action:
  in NEXT-UP                    hub/NEXT-UP.md names it already (its id, or its "Company · Title" row)
  apply: propose a NEXT-UP row  fit_score 4.0 or more: the orchestrator adds the NEXT-UP row
  maybe list                    fit_score 3.5 to 3.9: the recruiter's weekly maybe list
  capture the JD                no JD yet (alert cards): the scout reads the posting and records it
                                (jobs_manual.py --jd-file); the score sharpens once the JD is in
  full report                   the fit-scorer's report (skill score-fit) today
and, beside it, `referral path` when a prospect works at the company and `outreach draft` when the role came from a
person (an X post, a prospect's message). Also reported: freshness (hours from posted_at to first_seen, target 3, over
the finds posted inside the window; older finds are counted as backlog, since they could never have been seen fresh
here), coverage (open jobs with a triage score) and counts by source and segment.

Writes state/review/fresh-roles-<date>.md (a section per run; the date is the department's day) and moves the
window (state/hunt/last-review.json). Records nothing in jobs/, prospects/ or the queue: those stay with their
owners. Job titles are posting text, quoted as data.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ats as fa  # noqa: E402
import hunt_score as hs  # noqa: E402
import js_common as jc  # noqa: E402
import people_watch  # noqa: E402

FRESH_TARGET_HOURS = 3
PERSON_SOURCES = ("prospect", "referral")


def _ts(value: Any) -> datetime | None:
    return fa.parse_ts(value)


def _last_review(root: Path) -> datetime | None:
    path = Path(root) / "state" / "hunt" / "last-review.json"
    try:
        return _ts(json.loads(path.read_text(encoding="utf-8")).get("at"))
    except (OSError, ValueError):
        return None


def _queue_text(root: Path) -> str:
    """hub/NEXT-UP.md, lowercased."""
    path = Path(root) / "hub" / "NEXT-UP.md"
    return path.read_text(encoding="utf-8").lower() if path.is_file() else ""


def _whole_id(jid: str, text: str) -> bool:
    """`jid` appears in `text` as a whole token, never inside a longer id (acme-1 is not in acme-12)."""
    return re.search(rf"(?<![\w-]){re.escape(jid)}(?![\w-])", text) is not None


def _row_key(rec: dict[str, Any]) -> str:
    return f"{rec.get('company') or ''} · {rec.get('title') or ''}".lower()


def _queued(rec: dict[str, Any], queue: str, shared: int = 1) -> bool | int:
    """NEXT-UP names a role by its id (a whole token in a state or page cell) or as "Company · Title" in its role
    column. A title row counts only when one open job carries that company and title (`shared`); when several do,
    the number is returned instead, so the action can say to check which one NEXT-UP means."""
    if _whole_id(str(rec["id"]).lower(), queue):
        return True
    if not rec.get("company") or f"| {_row_key(rec)} |" not in queue:
        return False
    return True if shared <= 1 else shared


def _segment(company: dict[str, Any] | None) -> str:
    if company and company.get("segment"):
        return str(company["segment"])
    return "giant" if (company or {}).get("tier") == "dream" else "mid"


def _action(rec: dict[str, Any], queue: str, shared: int = 1) -> tuple[str, str]:
    fit = rec.get("fit_score")
    fit = float(fit) if fit not in (None, "") else None
    queued = _queued(rec, queue, shared)
    if queued is True:
        return "in NEXT-UP", "nothing (already in the ranked list)"
    check = (f" (NEXT-UP names \"{rec.get('company')} · {rec.get('title')}\" and {queued} postings share it: check it is "
             "not this one)") if queued else ""
    if fit is not None and fit >= 4.0:
        return "apply: propose a NEXT-UP row", f"orchestrator: a NEXT-UP row for {rec['id']} (fit {fit:.1f})" + check
    if fit is not None and fit >= 3.5:
        return "maybe list", "the recruiter: the weekly maybe list" + check
    if not str(rec.get("_jd_text") or "").strip():
        return "capture the JD", (f"scout: read the posting, then `python3 scripts/jobs_manual.py add --platform "
                                  f"{rec.get('platform') or 'company-ats'} --url {rec.get('source_url')} --title "
                                  f"\"{rec.get('title')}\" --company \"{rec.get('company')}\" --jd-file <the JD text>`" + check)
    return "full report", f"fit-scorer: skill score-fit on jobs/{rec['id']}.md" + check


def build(root: Path, *, today: date, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    since = _last_review(root) or (now - timedelta(hours=24))
    cfg = hs._config(root)
    companies = {c["slug"]: c for c in fa.load_companies(root)}
    prospects = hs._prospects(root)
    queue = _queue_text(root)
    jobs = fa.load_jobs(root) if (root / "jobs").is_dir() else {}
    open_jobs = [r for r in jobs.values() if hs.scoreable(r)]
    shared: dict[str, int] = {}
    for r in open_jobs:
        shared[_row_key(r)] = shared.get(_row_key(r), 0) + 1
    new = [r for r in open_jobs if (_ts(r.get("first_seen")) or now) > since]
    gated_out = [r for r in jobs.values() if r.get("gate") == "fail" and (_ts(r.get("first_seen")) or now) > since]

    rows, below = [], 0
    for rec in new:
        score = int(rec.get("hunt_score") or 0)
        fit = rec.get("fit_score")
        if score < cfg["cut"] and not (fit not in (None, "") and float(fit) >= 3.5):
            below += 1
            continue
        action, command = _action(rec, queue, shared.get(_row_key(rec), 1))
        also = []
        slug = str(rec.get("company_slug") or "")
        if prospects.get(slug):
            also.append("referral path")
        if rec.get("platform") == "x" or rec.get("prospect") or rec.get("source") in PERSON_SOURCES:
            also.append("outreach draft")
        posted = _ts(rec.get("posted_at")) or _ts(rec.get("first_seen"))
        rows.append({"id": rec["id"], "hunt_score": score, "fit_score": fit, "company": rec.get("company"),
                     "title": rec.get("title"), "segment": _segment(companies.get(slug)),
                     "source": rec.get("source"), "platform": rec.get("platform"), "url": rec.get("source_url"),
                     "age_hours": round((now - posted).total_seconds() / 3600, 1) if posted else None,
                     "action": action, "command": command, "also": also,
                     "reasons": list(rec.get("hunt_reasons") or [])[:4]})
    rows.sort(key=lambda r: (-r["hunt_score"], r["id"]))

    lags, by_source, backlog = [], {}, 0
    for rec in new:
        p, f = _ts(rec.get("posted_at")), _ts(rec.get("first_seen"))
        by_source[rec.get("source") or "unknown"] = by_source.get(rec.get("source") or "unknown", 0) + 1
        if p and p < since:                # posted before the window: it could never have been seen fresh here
            backlog += 1
        elif p and f and f >= p:
            lags.append((f - p).total_seconds() / 3600)
    by_segment: dict[str, int] = {}
    for rec in new:
        seg = _segment(companies.get(str(rec.get("company_slug") or "")))
        by_segment[seg] = by_segment.get(seg, 0) + 1
    scored = sum(1 for r in open_jobs if r.get("hunt_score") is not None)
    try:
        plan = people_watch.linkedin_plan(root, today=today)
    except people_watch.WatchError as exc:
        plan = [{"name": f"(people-watch.yaml invalid: {exc})", "command": "fix the file", "why": "", "company": ""}]
    return {
        "linkedin_plan": plan,
        "date": today.isoformat(), "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cut": cfg["cut"], "rows": rows,
        "counts": {"new": len(new), "listed": len(rows), "below_cut": below, "gated_out": len(gated_out),
                   "by_source": dict(sorted(by_source.items())), "by_segment": dict(sorted(by_segment.items()))},
        "freshness": {"measured": len(lags), "median_hours": round(statistics.median(lags), 1) if lags else None,
                      "within_target": sum(1 for h in lags if h <= FRESH_TARGET_HOURS), "backlog": backlog,
                      "target_hours": FRESH_TARGET_HOURS},
        "coverage": {"open": len(open_jobs), "scored": scored,
                     "share": round(scored / len(open_jobs), 3) if open_jobs else None},
    }


def render(review: dict[str, Any]) -> str:
    c, f, cov = review["counts"], review["freshness"], review["coverage"]
    lines = [f"## Run {review['now']} (window since {review['since']})", "",
             f"New open finds {c['new']}: {c['listed']} listed at the cut {review['cut']:g} or with a fit score, "
             f"{c['below_cut']} below it; {c['gated_out']} gated out. By source: "
             + (", ".join(f"{k} {v}" for k, v in c["by_source"].items()) or "none")
             + ". By segment: " + (", ".join(f"{k} {v}" for k, v in c["by_segment"].items()) or "none") + ".",
             f"Freshness: median {f['median_hours']} h from posting to first seen over {f['measured']} dated finds; "
             f"{f['within_target']} within {f['target_hours']} h; {f.get('backlog', 0)} older postings seen for the first "
             f"time (backlog, not measured). Coverage: {cov['scored']} of {cov['open']} open jobs "
             "have a triage score.", "",
             "Titles are posting text (data). One action per find; `also` names the parallel paths.", ""]
    for r in review["rows"]:
        fit = f", fit {float(r['fit_score']):.1f}" if r["fit_score"] not in (None, "") else ""
        age = f", {r['age_hours']:g} h old" if r["age_hours"] is not None else ""
        lines += [f"### {r['hunt_score']}{fit} · {r['company']} · {r['title']}",
                  f"- {r['segment']} · {r['source']}{'/' + r['platform'] if r['platform'] else ''}{age} · {r['url']}",
                  f"- Action: **{r['action']}**: {r['command']}"]
        if r["also"]:
            lines.append(f"- Also: {', '.join(r['also'])}")
        lines.append(f"- Why: {'; '.join(r['reasons'])}")
        lines.append("")
    if not review["rows"]:
        lines.append("Nothing new at the cut since the last review.")
    if review.get("linkedin_plan"):
        lines += ["", f"#### LinkedIn sweep today ({len(review['linkedin_plan'])} named reads, the people watch's share of "
                  "the daily profile reads; the owner present; read only)", ""]
        lines += [f"- {p['name']} ({p['company']}): {p['why']}\n  `{p['command']}`" for p in review["linkedin_plan"]]
    return "\n".join(lines) + "\n"


def write(root: Path, review: dict[str, Any], *, today: date, now: datetime | None = None) -> Path:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    path = root / "state" / "review" / f"fresh-roles-{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    section = render(review)
    if path.is_file():
        text = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + section
    else:
        text = f"# Fresh roles {today.isoformat()} (hunt_review.py)\n\n" + section
    path.write_text(text, encoding="utf-8")
    state = root / "state" / "hunt"
    state.mkdir(parents=True, exist_ok=True)
    (state / "last-review.json").write_text(json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    (state / "review-latest.json").write_text(json.dumps(review, indent=1, default=str) + "\n")
    return path


def prepare(root: Path, *, today: date, dry_run: bool = False) -> None:
    """js-hunt-review's first steps: capture every JD a terms-safe channel reaches (jd_capture.py; never
    Firecrawl in a routine), then re-score, so the review ranks with the JDs. A failure is reported, never fatal: the
    review still goes out."""
    import jd_capture
    try:
        out = jd_capture.capture(root, today=today, dry_run=dry_run, snap=None, budget_s=jd_capture.BUDGET_S)
        print(f"JD capture: {out['counts'].get('captured', 0)} captured, {out['counts'].get('deferred', 0)} deferred "
              f"to the next run ({jd_capture.BUDGET_S / 60:g} min budget)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 (the review must still run)
        print(f"JD capture failed ({type(exc).__name__}: {exc}); the review runs on what is recorded", file=sys.stderr)
    try:
        hs.run(root, today=today, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"re-score failed ({type(exc).__name__}: {exc}); the review runs on the last scores", file=sys.stderr)


def run_clock(today_arg: str | None, now_arg: str | None) -> tuple[date, datetime]:
    """(department day, review instant) from the flags alone: --now wins; --today other than today means the end of
    that department day; neither means the wall clock. The window never mixes a given day with the real clock."""
    if now_arg:
        now = datetime.fromisoformat(now_arg.replace("Z", "+00:00"))
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return (jc.parse_date(today_arg) if today_arg else jc.dept_day(now)), now.astimezone(timezone.utc)
    today = jc.parse_date(today_arg) if today_arg else jc.today()
    if today == jc.today():
        return today, datetime.now(timezone.utc)
    end = datetime.combine(today, time(23, 59, 59), tzinfo=jc.DEPT_TZ)
    return today, end.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today", help="the department's day YYYY-MM-DD (default: today); another day reviews up to "
                                    "the end of that day")
    ap.add_argument("--now", help="the review instant, ISO 8601 (default: now, or the end of --today)")
    ap.add_argument("--capture", action="store_true",
                    help="first capture JDs (jd_capture.py, no Firecrawl) and re-score, so the review sees them")
    ap.add_argument("--fresh10", action="store_true",
                    help="after the review, rebuild state/daily/<date>-fresh10.md (fresh10.py --write) so the day's "
                         "Fresh 10 sees the day's first polls")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today, now = run_clock(args.today, args.now)
    import run_guard                         # the whole review ends itself (exit 3, stack on disk)
    guard = run_guard.arm(None if args.dry_run else GUARD_S, Path(args.root) / "state" / "review",
                          f"hunt-review-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')}")   # one per run (R2-1)
    try:
        return _review(args, today, now)
    finally:
        guard.disarm()


GUARD_S = 30 * 60                            # capture (12 min budget) + re-score + review, far inside the task's limit


def _review(args: Any, today: date, now: datetime) -> int:
    if args.capture:
        prepare(args.root, today=today, dry_run=args.dry_run)
    try:
        review = build(args.root, today=today, now=now)
    except hs.HuntError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(review, indent=2, default=str))
    else:
        print(render(review))
    if not args.dry_run:
        print(f"wrote {write(args.root, review, today=today, now=now)}", file=sys.stderr)
        if args.fresh10:
            return _rebuild_fresh10(args.root, now)
    return 0


def _rebuild_fresh10(root: Path, now: datetime) -> int:
    """Fresh 10 at the review's instant (fresh10.pick; its CLI's --today would mean the end of the day),
    written where fresh10.py --write writes it; rows new since the 06:45 people report are named."""
    import fresh10
    try:
        res = fresh10.pick(Path(root), now.astimezone(fresh10.LA), 72, 10, 2)   # fresh10.py's CLI defaults
        path = Path(root) / "state" / "daily" / f"{res['date']}-fresh10.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh10.render(res), encoding="utf-8")
    except Exception as exc:                      # the review is written; the rebuild failing is said, and fails the run
        print(f"error: Fresh 10 not rebuilt: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Fresh 10 rebuilt at {now.astimezone(fresh10.LA):%H:%M} PT: {path.relative_to(root)}", file=sys.stderr)
    people = path.with_name(f"{res['date']}-fresh10-people.md")
    known = people.read_text(encoding="utf-8") if people.is_file() else ""
    missing = [r["id"] for r in res["rows"] if not _whole_id(r["id"], known)]
    if missing:
        print(f"note: {len(missing)} Fresh 10 row(s) have no people entry in {people.name} (it was built earlier; "
              f"refresh with `python3 scripts/fresh10_people.py --net --write`): {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
