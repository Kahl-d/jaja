#!/usr/bin/env python3
"""agency_status.py - the job-search agency's health in at most 15 lines, for the daily brief. Read only.

Lines: the job clock; the funnel (tracker.py metrics); the funnel by tier (dream, focused, mass; a role's tier is
the tier of the newest apply-batch sheet that lists it, state/apply-batches/*.md, else untiered); the last batch; queue
depth by state of hub/NEXT-UP.md's ranked list; today's queue (open applications whose
next action is due); follow-ups due (scripts/follow_ups.py); approvals waiting on the owner; routines (enabled specs under
routines/ and domains/*/projects/*/routines/, with a receipt today, and the stale ones: no receipt (or install) within a day
for a daily schedule, 7 days for a weekly one, 31 for a monthly one); the Scout's open lessons (scripts/hunt_lessons.py:
count and the oldest open id with its date); one line for the focus and reader work: focus profiles (profile/lenses/*.md
through scripts/focus_profiles.py; a vision line is approved once its section 7 no longer carries the draft marker,
policy.focus.draft_marker) and reader models (research/targets/*.md packages with a "## 6 Reader model" section, and how many pass
scripts/reader_model.py check); lint and sync (from the newest nightly-sweep receipt, or --live to run lint.py and
sync.py --check, which takes longer).

Absent is not empty: a missing source prints "no data yet".

CLI:  python3 scripts/agency_status.py [--root JS] [--os-root OS] [--today YYYY-MM-DD] [--live]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import clock as job_clock  # noqa: E402
import follow_ups  # noqa: E402
import hunt_lessons  # noqa: E402
import focus_profiles  # noqa: E402
import reader_model  # noqa: E402
import tracker as tr  # noqa: E402
import js_common as jc  # noqa: E402
from js_common import JS_ROOT, cli_date, dept_day, fm, os_root_for  # noqa: E402

NO_DATA = "no data yet"
DRAFT_MARKER = str(cfg.get("policy.focus.draft_marker", "draft for the owner")).lower()   # text that marks a vision line as unapproved
TIERS = ("dream", "focused", "mass")
APPLIED = set(tr.PROGRESS[tr.PROGRESS.index("applied"):])
SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|\s*$")


def clock_line(js: Path, today: date) -> str:
    try:
        cfg = job_clock.load(js)
    except job_clock.ClockError as exc:
        return f"Clock: unreadable ({exc})"
    return f"Clock: {job_clock.status(cfg, today)['line']}" if cfg else f"Clock: {NO_DATA}"


def batches(js: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((js / "state" / "apply-batches").glob("*.md")) if (js / "state" / "apply-batches").is_dir() else []:
        try:
            meta, _ = fm.load(p)
        except Exception:
            continue
        if meta.get("type") == "apply-batch":
            out.append({**meta, "path": p.relative_to(js).as_posix()})
    return out


def _tier_inference(js: Path):
    """strategy_review.application_tier bound to this tree's jobs, watchlist and dream list (loaded once)."""
    import strategy_review as sr
    from company_index import CompanyIndex
    from fetch_ats import load_jobs
    jobs, index = load_jobs(js), CompanyIndex.load(js)
    dream = sr.dream_slugs(js, index)

    def tier(meta: dict[str, Any], slug: str, sheets: dict[str, str]) -> str:
        job = jobs.get(tr._link_target(meta.get("job")) or "", {})
        company = str(meta.get("company_slug") or "") or str(meta.get("company") or job.get("company") or "")
        return sr.application_tier(meta, slug, {k: v for k, v in sheets.items() if v in TIERS}, job.get("fit_score"),
                                   bool(company) and index.resolve(company)[0] in dream)
    return tier


def funnel_and_tiers(js: Path, today: date) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    sheets = batches(js)
    tier_of: dict[str, str] = {}
    for b in sheets:                                   # sorted by file name = date, then number: newest wins
        for slug in b.get("slugs") or []:
            tier_of[str(slug)] = str(b.get("tier") or "mass")
    now = tr.run_now(today)
    apps = list(tr.iter_applications(js) or [])
    tiers: dict[str, Any] = {t: {"batched": 0, "applied": 0, "inferred": 0} for t in TIERS}
    tiers["untiered"] = {"applied": 0}
    for t in tier_of.values():
        if t in tiers:
            tiers[t]["batched"] += 1
    due, infer = 0, None
    for slug, meta, body in apps:
        proj = tr.project(tr.parse_events(body), now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        if proj["max_progress"] in APPLIED:          # the recorded tier, the sheet's, else the rule (inferred)
            if infer is None:
                infer = _tier_inference(js)
            label = infer(meta, slug, tier_of)
            base = label.split(" ")[0]
            if base not in TIERS:
                tiers["untiered"]["applied"] += 1
            else:
                tiers[base]["inferred" if label.endswith("(inferred)") else "applied"] += 1
        action, when = tr.next_action_for(proj, now)
        if action and when and when <= today.isoformat() and proj["stage"] not in ("on_hold", "accepted", *tr.TERMINAL):
            due += 1
    if not apps:
        return None, tiers, sheets
    m = tr.metrics(js, now)
    f = m["funnel"]
    funnel = {"applications": m["applications"], **{k: f.get(k, 0) for k in ("applied", "screen", "interviewing",
                                                                            "final", "offer")},
              "ended": sum(m["outcomes"].values()), "due_today": due}
    return funnel, tiers, sheets


def queue(js: Path) -> dict[str, int] | None:
    """Rows of hub/NEXT-UP.md's ranked list counted by the first word of their state column
    (READY, PEOPLE FIRST, PAGE, JOB, SCORE, BLOCKED); None when the file or the list is missing."""
    path = js / "hub" / "NEXT-UP.md"
    if not path.is_file():
        return None
    counts: dict[str, int] = {}
    in_list, cols, found = False, None, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_list = line[3:].strip().lower().startswith("the list")
            found = found or in_list
            cols = None
            continue
        if not in_list or not line.startswith("|") or SEPARATOR_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cols is None:
            cols = [c.lower() for c in cells]
            continue
        if "state" not in cols or len(cells) <= cols.index("state"):
            continue
        word = re.match(r"[A-Z]+(?: [A-Z]+)*(?![a-z])", cells[cols.index("state")])   # the state words, PEOPLE FIRST whole
        if word:
            key = word.group(0).upper()
            counts[key] = counts.get(key, 0) + 1
    return counts if found else None


def approvals_waiting(os_root: Path) -> int | None:
    folder = os_root / "work" / "approvals"
    if not folder.is_dir():
        return None
    n = 0
    for p in folder.glob("A-*.md"):
        try:
            meta, _ = fm.load(p)
        except Exception:
            continue
        if meta.get("status") == "pending" and meta.get("project") == "job-search":
            n += 1
    return n


def _allowed_days(schedule: str) -> int:
    parts = str(schedule or "").split()
    if len(parts) != 5:
        return 1
    dom, dow = parts[2], parts[4]
    return 1 if dom == "*" and dow == "*" else 7 if dom == "*" else 31


def routines(os_root: Path, today: date) -> dict[str, Any] | None:
    specs = sorted((os_root / "routines").glob("*.yaml")) + (sorted(jc.CODE_ROOT.glob("routines/*.yaml")) if Path(os_root).resolve() == jc.OS_ROOT.resolve() else []) + sorted(os_root.glob("domains/*/projects/*/routines/*.yaml"))
    if not specs:
        return None
    enabled, fresh, stale = 0, 0, []
    for p in specs:
        try:
            spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if spec.get("enabled") is not True:
            continue
        enabled += 1
        name = str(spec.get("name") or p.stem)
        runs = os_root / "logs" / "runs" / name
        dates = sorted(f.stem[:10] for f in runs.glob("*.md")) if runs.is_dir() else []
        newest = dates[-1] if dates else None
        # `installed:` restarts the clock as in the watchdog; a future or unreadable value is ignored
        installed = str(spec.get("installed") or "")[:10]
        try:
            installed = installed if date.fromisoformat(installed) <= today else ""
        except ValueError:
            installed = ""
        anchor = max(d for d in (newest, installed) if d) if (newest or installed) else None
        if newest == today.isoformat():
            fresh += 1
        elif anchor is None or (today - date.fromisoformat(anchor)).days > _allowed_days(spec.get("schedule")):
            stale.append((name, newest or "never"))
    return {"enabled": enabled, "today": fresh, "stale": stale}


def lint_status(os_root: Path, live: bool) -> dict[str, str] | None:
    if live:
        def last(cmd: list[str]) -> str:
            r = subprocess.run(cmd, cwd=str(os_root), capture_output=True, text=True, timeout=600)
            lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
            return lines[-1] if lines else f"exit {r.returncode}"
        return {"source": "live", "lint": last([sys.executable, "scripts/kernel/lint.py"]),
                "sync": last([sys.executable, "scripts/kernel/sync.py", "--check"])}
    runs = os_root / "logs" / "runs" / "nightly-sweep"
    files = sorted(runs.glob("*.md")) if runs.is_dir() else []
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8")
    last_run = text.rsplit("## Run", 1)[-1]
    get = lambda key: (re.search(rf"^- {re.escape(key)}: (.+)$", last_run, re.M) or [None, "not reported"])[1]   # noqa: E731
    return {"source": f"nightly-sweep {files[-1].stem[:10]}", "lint": get("lint"), "sync": get("sync --check")}


def reader_models(js: Path) -> dict[str, int] | None:
    folder = js / "research" / "targets"
    if not folder.is_dir():
        return None
    packages = clean = 0
    for p in sorted(folder.glob("*.md")):
        try:
            res = reader_model.check(p)
        except (OSError, UnicodeDecodeError):
            continue
        if res["errors"][:1] and res["errors"][0].startswith("no `## 6 Reader model`"):
            continue                                     # no section 6: not a reader-model package yet
        packages += 1
        clean += not res["errors"]
    return {"packages": packages, "clean": clean}


def focus(js: Path) -> dict[str, int] | None:
    rows = focus_profiles.lenses(js) if (js / "profile" / "lenses").is_dir() else []
    if not rows:
        return None
    return {"profiles": len(rows),
            "approved": sum(1 for r in rows if r["section7"] and DRAFT_MARKER not in r["section7"].lower())}


def lessons(js: Path) -> dict[str, Any] | None:
    res = hunt_lessons.collect(js)
    if not res["source_present"]:
        return None
    open_rows = sorted((r for r in res["rows"] if not r["answer"]), key=lambda r: r["date"] or "9999")   # stable: file order within a day
    return {"open": len(open_rows), "oldest": (open_rows[0]["id"], open_rows[0]["date"]) if open_rows else None}


def _scouts(js: Path) -> dict[str, Any] | None:
    import scout_coverage
    return scout_coverage.summary(js)


def _interviews(js: Path, today: date) -> str | None:
    """The first line of the brief: interviews booked this week against the owner's weekly target."""
    try:
        import interviews
        return interviews.line(js, today)
    except Exception:  # noqa: BLE001  (missing is not empty: the brief says the line could not be computed)
        return None


def collect(js: Path, os_root: Path, today: date, live: bool = False) -> dict[str, Any]:
    funnel, tiers, sheets = funnel_and_tiers(js, today)
    fu = follow_ups.collect(js, today)
    return {"date": today.isoformat(), "interviews": _interviews(js, today), "clock": clock_line(js, today),
            "funnel": funnel, "tiers": tiers,
            "last_batch": sheets[-1] if sheets else None, "queue": queue(js),
            "follow_ups": {"due": len(fu["due"]), "exhausted": len(fu["exhausted"]), "windows": len(fu["windows"])},
            "approvals": approvals_waiting(os_root), "routines": routines(os_root, today),
            "scouts": _scouts(js),
            "lessons": lessons(js), "reader_models": reader_models(js), "focus": focus(js),
            "lint": lint_status(os_root, live)}


def scouts_line(sc: dict[str, Any] | None) -> str:
    """One line from scout_coverage.summary (the newest saved JSON); missing is "no data yet", never zero."""
    if not sc or sc.get("date") is None:
        return f"Scouts: {NO_DATA}"
    cov = "no scout run" if sc.get("coverage") is None else f"{round(sc['coverage'] * 100)}%"
    def n(v: Any) -> str:
        return NO_DATA if v is None else str(v)
    return f"Scouts: coverage {cov} ({sc['date']}), {n(sc.get('new_postings'))} new postings, " \
           f"{n(sc.get('new_companies'))} new companies"


def render(s: dict[str, Any]) -> list[str]:
    L = [f"Agency status {s['date']} · " + (s.get("interviews") or f"Interviews booked this week: {NO_DATA}"), s["clock"]]
    f = s["funnel"]
    L.append(f"Funnel: {NO_DATA}" if f is None else
             f"Funnel: {f['applications']} applications · applied {f['applied']} · screen {f['screen']} · interviewing "
             f"{f['interviewing']} · final {f['final']} · offer {f['offer']} · ended {f['ended']}")
    t = s["tiers"]
    L.append("By tier (applied of batched, + inferred): "
             + " · ".join(f"{k} {t[k]['applied']} of {t[k]['batched']}"
                          + (f" (+{t[k]['inferred']} inferred)" if t[k].get("inferred") else "") for k in TIERS)
             + f" · untiered applied {t['untiered']['applied']}")
    b = s["last_batch"]
    L.append(f"Last batch: {NO_DATA}" if b is None else
             f"Last batch: {b['path']} ({b.get('tier')}; ready {b.get('counts', {}).get('ready', '?')} of "
             f"{b.get('counts', {}).get('roles', '?')})")
    q = s["queue"]
    L.append(f"Queue (hub/NEXT-UP.md): {NO_DATA}" if q is None else
             "Queue (hub/NEXT-UP.md): " + (" · ".join(f"{k} {v}" for k, v in q.items()) or "no rows"))
    L.append(f"Next actions due: {NO_DATA if f is None else f['due_today']}")
    fu = s["follow_ups"]
    L.append(f"Follow-ups due: {fu['due']} (no nudges left {fu['exhausted']}; referral windows {fu['windows']})")
    L.append(f"Approvals waiting on the owner: {NO_DATA if s['approvals'] is None else s['approvals']}")
    r = s["routines"]
    if r is None:
        L.append(f"Routines: {NO_DATA}")
    else:
        stale = ", ".join(f"{n} ({d})" for n, d in r["stale"][:6]) + (f" and {len(r['stale']) - 6} more" if len(r["stale"]) > 6 else "")
        L.append(f"Routines: {r['today']} of {r['enabled']} enabled ran today; stale: {stale or 'none'}")
    L.append(scouts_line(s.get("scouts")))
    ls = s["lessons"]
    L.append(f"Scout lessons: {NO_DATA}" if ls is None else f"Scout lessons: {ls['open']} open"
             + (f" (oldest {ls['oldest'][0]} {ls['oldest'][1] or 'undated'})" if ls["oldest"] else ""))
    fo, rmod = s["focus"], s["reader_models"]
    L.append((f"Focus profiles: {NO_DATA}" if fo is None else
              f"Focus profiles: {fo['profiles']} (vision lines approved {fo['approved']}/{fo['profiles']})")
             + "; reader models clean " + (NO_DATA if rmod is None else f"{rmod['clean']}/{rmod['packages']}"))
    ln = s["lint"]
    L.append(f"Lint and sync: {NO_DATA}" if ln is None else f"Lint and sync ({ln['source']}): lint {ln['lint']} · sync {ln['sync']}")
    return L[:15]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--os-root", type=Path, default=None)
    p.add_argument("--today", type=cli_date, default=None)
    p.add_argument("--live", action="store_true", help="run lint.py and sync.py --check now (slower)")
    args = p.parse_args(argv)
    os_root = args.os_root or os_root_for(Path(args.root))
    today = args.today or dept_day(tr.wall_clock())
    print("\n".join(render(collect(Path(args.root), Path(os_root), today, args.live))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
