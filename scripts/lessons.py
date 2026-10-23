#!/usr/bin/env python3
"""lessons.py - what the agency learned, read in code: the Recruiter's ledger
state/recruiter/LESSONS.md, the open experiments in state/recruiter/experiments.yaml and the shared learning record
state/learn/<role>.jsonl (scripts/learn.py), measured against the records. Read only.

What it gives (each a function the cards header, the evening wrap and strategy_review call):
  lessons(root)          LESSONS.md lines `date | what happened | evidence | change | where | re-check on`, parsed;
                         a line with a missing field is a problem, shown, never dropped; rejection codes (YRS, FORM,
                         COLD, AGE, LAB, PDF) found in the line mark a clear cause
  due(lessons, today)    the re-checks whose date has come (a re-check written as words, "on merge", is never due)
  experiments(root, now) each experiment with n per arm COMPUTED from the records, not copied from n_so_far:
                           path             person against cold (interviews.channel_of: the recorded `path`)
                           apply_freshness  cold applications applied <= 72 h after posting (within_72h) or later
                                            (posted_at on the job record; tracker.metrics' apply-latency rule)
                           person_kind      person-path applications by who carried them: recruiter, referral, other
                                            (the application's `channel`, else `applied_via`)
                           first_touches    first outbound touches to a person (touches/*.jsonl), replied in 7 days
                           tagged           applications and touches carrying `experiment: <id>` and `arm: <name>`
                         The counter is the experiment's `count:` key, else the one whose arm names match n_so_far's
                         keys, else tagged; when the records hold no try, n_so_far is shown as a hand count, marked.
                         An experiment is READY to decide only when every arm has MIN_TRIES (about 15, charter §1,
                         the same constant as strategy_review's MIN_N) or it names a `clear_cause:`; a written
                         `result:` closes it
  learned(root)          state/learn rows (learn.read/current): candidates (proposed), tried, kept, invalid lines
  progress(root, day)    today's and this week's numbers against targets/goals.yaml's floors (js_common.load_goals):
                         interviews booked (interviews.booked) and held (interviews.py's held count when it has one,
                         else "not measured"), applications mass and focused (the recorded `tier`: dream or focused =
                         focused, mass = mass; no tier: a person path is focused, else mass, charter §7), first touches
                         and posts read per channel, LinkedIn, X and YC (state/posts/*-<date>.jsonl, unique urls, the
                         row's `platform` else the file prefix); no ceiling
  rounds(root, day)      interview rounds in the next 7 days (interviews.upcoming when interviews.py has it, with its
                         flag; else the round_at the tracker recorded); a round with no time on record is undated
CLI:  python3 scripts/lessons.py [--root JS] [--today YYYY-MM-DD] [--json]
No network; writes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402
from js_common import JS_ROOT, MIN_TRIES, cli_date, fm  # noqa: E402

LESSONS_FILE = Path("state") / "recruiter" / "LESSONS.md"
EXPERIMENTS_FILE = Path("state") / "recruiter" / "experiments.yaml"
LESSON_FIELDS = ("date", "what", "evidence", "change", "where", "recheck")
REJECTION_CODES = ("YRS", "FORM", "COLD", "AGE", "LAB", "PDF")
FRESH_HOURS = 72
REPLY_DAYS = 7
ROUND_DAYS = 7
COUNTER_ARMS = {"path": ("person", "cold"), "apply_freshness": ("within_72h", "later"),
                "person_kind": ("recruiter", "referral", "other"), "first_touches": ("sent",)}
LA = jc.DEPT_TZ
TZ_LABEL = str(cfg.get("identity.timezone_label", "UTC"))


# --------------------------------------------------------------------------- LESSONS.md
def parse_lessons(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """(lessons, problems). A lesson line starts with a date and a pipe; anything else is prose and skipped."""
    out, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not re.match(r"^\d{4}-\d{2}-\d{2} \|", line):
            continue
        parts = [p.strip() for p in line.split(" | ")]
        row = dict(zip(LESSON_FIELDS, parts + [""] * (len(LESSON_FIELDS) - len(parts))))
        row["line"] = n
        if len(parts) < len(LESSON_FIELDS):
            problems.append(f"LESSONS.md line {n}: {len(parts)} fields, the format has {len(LESSON_FIELDS)} "
                            f"(missing: {', '.join(LESSON_FIELDS[len(parts):])})")
        elif len(parts) > len(LESSON_FIELDS):
            row["recheck"] = " | ".join(parts[len(LESSON_FIELDS) - 1:])
        m = re.search(r"\d{4}-\d{2}-\d{2}", row["recheck"])
        try:
            row["recheck_on"] = date.fromisoformat(m.group(0)).isoformat() if m else None
        except ValueError:
            row["recheck_on"] = None
        codes = re.search(r"\bcodes? ([^|]*)", row["what"] + " " + row["evidence"])
        row["codes"] = [c for c in REJECTION_CODES if codes and re.search(rf"\b{c}\b", codes.group(1))]
        row["clear_cause"] = bool(row["codes"])
        out.append(row)
    for m in re.finditer(r"(\d+)\+ sends per arm", text):
        if int(m.group(1)) != MIN_TRIES:
            problems.append(f"LESSONS.md 'Decision thresholds' says {m.group(1)}+ sends per arm; the charter (§1) says "
                            f"about {MIN_TRIES} tries (js_common.MIN_TRIES)")
    return out, problems


def lessons(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = Path(root) / LESSONS_FILE
    if not path.is_file():
        return [], [f"{LESSONS_FILE.as_posix()} missing"]
    return parse_lessons(path.read_text(encoding="utf-8"))


def due(rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """The lessons whose re-check date has come (on or before today), oldest first."""
    return sorted((r for r in rows if r.get("recheck_on") and r["recheck_on"] <= today.isoformat()),
                  key=lambda r: (r["recheck_on"], r["line"]))


# --------------------------------------------------------------------------- the records, one row per application
def _end_of(day: date) -> datetime:
    return datetime.combine(day, time(23, 59), LA).astimezone(timezone.utc)


def app_rows(root: Path, now: datetime) -> list[dict[str, Any]]:
    """One row per applied application: day, path side, tier side, latency, responded, interviewed, experiment tags."""
    import interviews as iv
    import tracker as tr
    from fetch_ats import load_jobs
    jobs = load_jobs(root)
    out = []
    for slug, meta, body in tr.iter_applications(root) or []:
        events = tr.parse_events(body)
        proj = tr.project(events, now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        applied = proj.get("applied_at")
        if applied is None:
            continue

        def stage(e: Any) -> str | None:
            kind = e.type.strip().lower()
            return kind.split(":", 1)[1] if kind.startswith("override:") else tr.EVENT_STAGE.get(kind)
        after = [e for e in events if (tr.parse_ts(e.at) or applied) >= applied]
        job = jobs.get(tr._link_target(meta.get("job")) or "", {})
        posted = tr.parse_ts(job.get("posted_at"))
        side = {"warm": "person", "cold": "cold"}.get(iv.channel_of(meta), "unknown")
        tier = str(meta.get("tier") or "").strip().lower()
        out.append({
            "slug": slug, "day": tr._day(applied).isoformat(), "path": side,
            "kind": _person_kind(meta) if side == "person" else None,
            "effort": "focused" if tier in ("dream", "focused") or (not tier and side == "person") else "mass",
            "latency_h": (applied - posted).total_seconds() / 3600 if posted and applied >= posted else None,
            "responded": any(stage(e) in tr.RESPONDED or e.type.strip().lower() in tr.INBOUND for e in after),
            "interviewed": any(stage(e) in iv.INTERVIEW_STAGES for e in events),
            "experiment": str(meta.get("experiment") or "") or None, "arm": str(meta.get("arm") or "") or None})
    return out


def _person_kind(meta: dict[str, Any]) -> str:
    for key in ("channel", "applied_via"):
        v = str(meta.get(key) or "").strip().lower()
        if "recruit" in v:
            return "recruiter"
        if "referr" in v:
            return "referral"
    return "other"


def first_touches(root: Path) -> list[dict[str, Any]]:
    """The first touch with each person, when it was ours and went out: {prospect, day, replied (within 7 days),
    experiment, arm}. A person who wrote first has no first touch of ours."""
    rows = []
    for t in jc.iter_touches(root):
        at = jc.parse_date(t.get("at"))
        if t.get("prospect") and at:
            rows.append(t)
    rows.sort(key=lambda t: str(t.get("at")))
    seen: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for t in rows:
        who = str(t["prospect"])
        if who in seen:
            first = seen[who]
            if first.get("_ours") and t.get("direction") == "in" and not first["replied"]:
                gap = _instant(t.get("at")) - _instant(first["at"])
                first["replied"] = timedelta(0) <= gap <= timedelta(days=REPLY_DAYS)
            continue
        ours = t.get("direction") == "out" and str(t.get("status") or "") in ("sent", "delivered")
        first = {"prospect": who, "at": t.get("at"), "day": _local_day(t.get("at")), "replied": False, "_ours": ours,
                 "experiment": t.get("experiment"), "arm": t.get("arm")}
        seen[who] = first
        if ours:
            out.append(first)
    return [{k: v for k, v in r.items() if k != "_ours"} for r in out]


def _instant(value: Any) -> datetime:
    import tracker as tr
    ts = tr.parse_ts(value)
    return ts if ts else datetime.min.replace(tzinfo=timezone.utc)


def _local_day(value: Any) -> str:
    ts = _instant(value)
    return ts.astimezone(LA).date().isoformat() if ts.year > 1 else str(value)[:10]


# --------------------------------------------------------------------------- experiments
def _arm(n: int, responded: int | None = None, screens: int | None = None) -> dict[str, Any]:
    return {"n": n, "responded": responded, "screens": screens}


def count(counter: str, exp_id: str, apps: list[dict[str, Any]], touches: list[dict[str, Any]]) -> dict[str, dict]:
    """n per arm (and its outcomes) for one counter. Empty when the records hold no try."""
    def tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return _arm(len(rows), sum(r["responded"] for r in rows), sum(r["interviewed"] for r in rows))
    if counter == "path":
        return {a: tally([r for r in apps if r["path"] == a]) for a in ("person", "cold")}
    if counter == "apply_freshness":
        cold = [r for r in apps if r["path"] == "cold" and r["latency_h"] is not None]
        return {"within_72h": tally([r for r in cold if r["latency_h"] <= FRESH_HOURS]),
                "later": tally([r for r in cold if r["latency_h"] > FRESH_HOURS])}
    if counter == "person_kind":
        return {a: tally([r for r in apps if r["kind"] == a]) for a in ("recruiter", "referral", "other")}
    if counter == "first_touches":
        return {"sent": _arm(len(touches), sum(t["replied"] for t in touches))} if touches else {}
    arms: dict[str, dict[str, Any]] = {}
    for r in apps:
        if r["experiment"] == exp_id:
            arms.setdefault(r["arm"] or "unnamed", []).append(r)
    out = {a: tally(rows) for a, rows in arms.items()}
    for t in touches:
        if str(t.get("experiment") or "") == exp_id:
            a = out.setdefault(str(t.get("arm") or "unnamed"), _arm(0, 0))
            a["n"] += 1
            a["responded"] = (a["responded"] or 0) + int(t["replied"])
    return out


def pick_counter(exp: dict[str, Any]) -> str:
    given = str(exp.get("count") or "").strip()
    if given:
        return given
    hand = exp.get("n_so_far")
    if isinstance(hand, dict) and hand:
        for name, arms in COUNTER_ARMS.items():
            if set(map(str, hand)) <= set(arms):
                return name
    return "tagged"


def experiments(root: Path, now: datetime, apps: list[dict[str, Any]] | None = None,
                touches: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    path = Path(root) / EXPERIMENTS_FILE
    if not path.is_file():
        return [], [f"{EXPERIMENTS_FILE.as_posix()} missing"]
    try:
        data = fm.parse_yaml(path.read_text(encoding="utf-8")) or {}
    except ValueError as exc:
        return [], [f"{EXPERIMENTS_FILE.as_posix()} unreadable: {exc}"]
    apps = app_rows(root, now) if apps is None else apps
    touches = first_touches(root) if touches is None else touches
    out, problems = [], []
    for exp in data.get("experiments") or []:
        if not isinstance(exp, dict) or not exp.get("id"):
            problems.append("experiments.yaml: an entry without an id")
            continue
        eid = str(exp["id"])
        counter = pick_counter(exp)
        if counter not in COUNTER_ARMS and counter != "tagged":
            problems.append(f"experiments.yaml {eid}: unknown count {counter!r} "
                            f"(known: {', '.join([*COUNTER_ARMS, 'tagged'])})")
            counter = "tagged"
        arms = count(counter, eid, apps, touches)
        source = f"records ({counter})"
        if not any(a["n"] for a in arms.values()):
            hand = exp.get("n_so_far")
            arms = ({str(k): _arm(int(v)) for k, v in hand.items() if isinstance(v, int)} if isinstance(hand, dict)
                    else {"all": _arm(int(hand))} if isinstance(hand, int) else {})
            source = "hand count (experiments.yaml n_so_far; no try in the records yet)"
        smallest = min((a["n"] for a in arms.values()), default=0)
        closed = exp.get("result") not in (None, "")
        cause = str(exp.get("clear_cause") or "").strip()
        if closed:
            state = "closed"
        elif cause:
            state = "ready (clear cause)"
        elif arms and smallest >= MIN_TRIES:
            state = f"ready ({MIN_TRIES}+ a side)"
        else:
            state = f"running (needs {MIN_TRIES} a side; smallest arm {smallest})"
        out.append({"id": eid, "hypothesis": exp.get("hypothesis"), "counter": counter, "source": source,
                    "arms": arms, "smallest": smallest, "min_tries": MIN_TRIES, "state": state,
                    "ready": state.startswith("ready"), "read_on": exp.get("read_on"), "result": exp.get("result")})
    return out, problems


# --------------------------------------------------------------------------- learned (state/learn)
def learned(root: Path) -> dict[str, Any]:
    import learn
    rows, problems = learn.read(root)
    cur = learn.current(rows)
    return {"rows": rows, "current": cur, "invalid": problems,
            "by_status": {s: sum(1 for r in cur if r["status"] == s) for s in learn.STATUSES}}


# --------------------------------------------------------------------------- goals: today and this week
def posts_read(root: Path, day: date) -> dict[str, int] | None:
    """Unique post urls the posts lane read that day, per channel (the owner: the posts lane reads LinkedIn, X and YC):
    state/posts/*-<day>.jsonl, the channel being a row's `platform`, else the file's prefix (linkedin-, x-, yc-).
    None when no file exists for the day (not measured); a channel with no row is 0 that day."""
    files = sorted((Path(root) / "state" / "posts").glob(f"*-{day.isoformat()}.jsonl"))
    if not files:
        return None
    urls: dict[str, set[str]] = {c: set() for c in jc.POST_CHANNELS}
    for f in files:
        prefix = f.name[: -len(f"-{day.isoformat()}.jsonl")].lower()
        for row in jc.read_jsonl(f):
            channel = str(row.get("platform") or prefix).strip().lower()
            channel = "x" if channel in ("twitter", "x.com") else "yc" if channel.startswith("yc") else channel
            urls.setdefault(channel, set()).add(str(row.get("url") or row.get("lead_id")
                                                    or json.dumps(row, sort_keys=True)))
    return {c: len(u) for c, u in urls.items()}


def _held(root: Path, day: date, booked: dict[str, Any]) -> tuple[int | None, int | None, str]:
    """(today, week, why) of interviews held, from whatever interviews.py offers; (None, None, why) otherwise."""
    import interviews as iv
    fn = getattr(iv, "held", None)
    if callable(fn):
        try:
            res = fn(root, day)
        except TypeError:
            res = None
        if isinstance(res, dict):
            items = res.get("items") or []
            today_n = sum(1 for i in items if str(i.get("date") or i.get("held_on") or "")[:10] == day.isoformat())
            return today_n if items else res.get("today"), res.get("this_week"), "interviews.held"
        if isinstance(res, int):
            return None, res, "interviews.held"
    for key in ("held_this_week", "held"):
        if isinstance(booked.get(key), int):
            return None, booked[key], f"interviews.booked {key}"
    return None, None, "not measured: interviews.py has no held count yet"


def progress(root: Path, day: date, apps: list[dict[str, Any]] | None = None,
             touches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Each goal metric: today, this week (Monday to `day`), the floors and a state (met, below, not measured)."""
    import interviews as iv
    goals = jc.load_goals(root)
    monday = day - timedelta(days=day.weekday())
    days = [(monday + timedelta(days=i)).isoformat() for i in range((day - monday).days + 1)]
    apps = app_rows(root, _end_of(day)) if apps is None else apps
    touches = first_touches(root) if touches is None else touches
    b = iv.booked(root, day)
    held_today, held_week, held_why = _held(root, day, b)
    post_days = [posts_read(root, date.fromisoformat(d)) for d in days]

    def posts(channel: str) -> tuple[int | None, int | None, str]:
        seen = [p.get(channel, 0) for p in post_days if p is not None]
        return (None if post_days[-1] is None else post_days[-1].get(channel, 0), sum(seen) if seen else None,
                f"state/posts/*-<date>.jsonl ({channel})")
    actual: dict[str, tuple[int | None, int | None, str]] = {
        "interviews_booked": (sum(1 for i in b["items"] if i["date"] == day.isoformat()), b["this_week"],
                              "interviews.booked"),
        "interviews_held": (held_today, held_week, held_why),
        "applications_mass": (sum(1 for r in apps if r["effort"] == "mass" and r["day"] == day.isoformat()),
                              sum(1 for r in apps if r["effort"] == "mass" and r["day"] in days), "tracker applied"),
        "applications_focused": (sum(1 for r in apps if r["effort"] == "focused" and r["day"] == day.isoformat()),
                                 sum(1 for r in apps if r["effort"] == "focused" and r["day"] in days),
                                 "tracker applied"),
        "first_touches": (sum(1 for t in touches if t["day"] == day.isoformat()),
                          sum(1 for t in touches if t["day"] in days), "touches/ first outbound"),
        **{f"posts_read_{c}": posts(c) for c in jc.POST_CHANNELS},
    }
    rows = []
    for metric in jc.GOAL_METRICS:
        floor = goals["floors"].get(metric, {})
        t, w, src = actual[metric]
        states = []
        for span, have, need in (("day", t, floor.get("daily")), ("week", w, floor.get("weekly"))):
            if need is None:
                continue
            states.append(f"{span} not measured" if have is None else f"{span} met" if have >= need
                          else f"{span} below by {need - have}")
        rows.append({"metric": metric, "today": t, "week": w, "daily": floor.get("daily"),
                     "weekly": floor.get("weekly"), "source": src, "state": "; ".join(states) or "no floor"})
    return {"goals": goals["source"], "status": goals["status"], "week_of": monday.isoformat(), "rows": rows}


# --------------------------------------------------------------------------- interview rounds coming
def rounds(root: Path, day: date, days: int = ROUND_DAYS) -> dict[str, Any]:
    """{"source", "rounds": [{slug, company, stage, when, flag}], "undated": [{slug, company, stage, flag}]}.
    interviews.upcoming(root, today) when interviews.py has it (BUILD-SCREENER: soonest first, untimed last; keys slug,
    company, stage, round_at, date_pt, time_pt, flag): a timed round within `days` is listed with its local time, an
    untimed one as undated, and its `flag` ("no round time recorded: ...", "records disagree: ...") is kept. Else each
    application in an interview stage with the round_at its latest event of that stage carries (tracker.py event
    --round-at)."""
    import interviews as iv
    fn = getattr(iv, "upcoming", None)
    if callable(fn):
        horizon = (day + timedelta(days=days)).isoformat()
        out, undated = [], []
        for r in fn(root, day) or []:
            if not isinstance(r, dict):
                continue
            row = {"slug": r.get("slug"), "company": r.get("company"), "stage": r.get("stage"), "flag": r.get("flag")}
            when_day = str(r.get("date_pt") or "")[:10]
            if not r.get("round_at") or not when_day:
                undated.append(row)
            elif when_day < horizon:
                out.append({**row, "when": f"{when_day} {r.get('time_pt') or ''} {TZ_LABEL}".replace("  ", " ")})
        return {"source": "interviews.upcoming", "rounds": out, "undated": undated}
    import tracker as tr
    now = _end_of(day - timedelta(days=1))
    horizon = day + timedelta(days=days)
    out, undated = [], []
    for slug, meta, body in tr.iter_applications(root) or []:
        events = tr.parse_events(body)
        stage = tr.project(events, now, meta.get("ghost_after_days"), meta.get("last_inbound_at")).get("stage")
        if stage not in iv.INTERVIEW_STAGES:
            continue
        at = None
        for ev in sorted(events, key=lambda e: tr.parse_ts(e.at) or datetime.min.replace(tzinfo=timezone.utc)):
            kind = ev.type.strip().lower()
            if (kind.split(":", 1)[1] if kind.startswith("override:") else tr.EVENT_STAGE.get(kind)) == stage:
                at = tr.round_info(ev.evidence)["round_at"]
        when = tr.parse_ts(at) if at else None
        row = {"slug": slug, "company": meta.get("company"), "stage": stage, "flag": None}
        if when is None:
            undated.append(row)
        elif day <= when.astimezone(LA).date() < horizon:
            out.append({**row, "when": when.astimezone(LA).strftime("%Y-%m-%d %H:%M ") + TZ_LABEL})
    return {"source": "tracker round_at", "rounds": sorted(out, key=lambda r: r["when"]), "undated": undated}


# --------------------------------------------------------------------------- the clock
def clock_line(root: Path, day: date) -> str:
    """clock.py's status line (day N of 90, days left, the end date), or why there is none."""
    import clock as job_clock
    try:
        cfg = job_clock.load(root)
    except job_clock.ClockError as exc:
        return f"Job clock unreadable: {exc}"
    return "Job clock: no clock.yaml (missing is not empty)" if cfg is None else job_clock.status(cfg, day)["line"]


# --------------------------------------------------------------------------- the whole picture
def report(root: Path, day: date) -> dict[str, Any]:
    root = Path(root)
    now = _end_of(day)
    apps, touches = app_rows(root, now), first_touches(root)
    ls, lp = lessons(root)
    ex, ep = experiments(root, now, apps, touches)
    return {"date": day.isoformat(), "min_tries": MIN_TRIES, "lessons": len(ls), "due": due(ls, day),
            "experiments": ex, "learned": learned(root), "progress": progress(root, day, apps, touches),
            "problems": lp + ep}


def _n(v: Any) -> str:
    return "not measured" if v is None else str(v)


def arms_text(e: dict[str, Any]) -> str:
    return ", ".join(f"{a} {v['n']}" + (f" ({v['screens']} screens)" if v.get("screens") else "")
                     for a, v in e["arms"].items()) or "no arm counted"


def goal_lines(p: dict[str, Any]) -> list[str]:
    head = (f"Goals ({p['goals']}, {p['status']}; floors, no ceiling), week of {p['week_of']}:" if p["goals"]
            else "Goals: no targets/goals.yaml (missing is not empty): the numbers, with no floor")
    lines = [head]
    for r in p["rows"]:
        floor_d = f" of {r['daily']}" if r["daily"] is not None else ""
        floor_w = f" of {r['weekly']}" if r["weekly"] is not None else ""
        lines.append(f"- {r['metric'].replace('_', ' ')}: today {_n(r['today'])}{floor_d} · week {_n(r['week'])}"
                     f"{floor_w} ({r['state']})")
    return lines


def render(r: dict[str, Any]) -> str:
    L = [f"Lessons {r['date']}: {r['lessons']} in LESSONS.md, {len(r['due'])} re-checks due; "
         f"{len(r['experiments'])} experiments, {sum(e['ready'] for e in r['experiments'])} ready to decide "
         f"(about {r['min_tries']} tries a side or a clear cause, charter §1)", "", "Re-checks due:"]
    L += [f"- {d['recheck_on']} (line {d['line']}): {d['what']} -> {d['change']}" for d in r["due"]] or ["- none"]
    L += ["", "Experiments:"]
    L += [f"- {e['id']}: {e['state']}; {arms_text(e)} [{e['source']}]; read on: {e['read_on']}"
          for e in r["experiments"]] or ["- none"]
    lr = r["learned"]
    L += ["", f"Learned (state/learn): {lr['by_status']['proposed']} proposed, {lr['by_status']['tried']} tried, "
          f"{lr['by_status']['kept']} kept" + (f"; {len(lr['invalid'])} invalid lines" if lr["invalid"] else "")]
    L += [f"- {x['date']} {x['role']}: {x['observation']}" for x in lr["current"] if x["status"] == "proposed"]
    L += [""] + goal_lines(r["progress"])
    if r["problems"]:
        L += ["", "Problems:"] + [f"- {p}" for p in r["problems"]]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    day = a.today or jc.dept_day(datetime.now(timezone.utc))
    r = report(a.root, day)
    print(json.dumps(r, indent=1, default=str, ensure_ascii=False) if a.json else render(r), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
