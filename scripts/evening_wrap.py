#!/usr/bin/env python3
"""evening_wrap.py - the evening wrap: one file a day,
state/recruiter/wrap-<date>.md, that tomorrow's morning cards read (cards.py header) and the Recruiter reads first
(lanes/recruiter/AGENT.md step 3).

Sections, every number from the records with its n:
  Numbers against the goals   today and this week against targets/goals.yaml's floors (lessons.progress), the clock
  What went out               applications today by effort (mass, focused) and path; first touches today
  What came back              today's replies, screens, rounds and rejections, each with its application's path
  What worked                 interviews per application by path (interviews.rates, n shown) and today's returns
                              by channel; a rate under MIN_TRIES a side is shown, never turned into a weight
  New LESSONS candidates      today's state/learn rows still proposed (learn.py), each rejection today and each
                              debrief written today (a coded LESSONS line is owed: codes YRS FORM COLD AGE LAB PDF)
  Experiments                 n per arm from the records and whether each is ready (lessons.experiments)
  Re-checks due               LESSONS.md lines whose re-check date has come (lessons.due)
  For <owner> to decide       generated: experiments ready to decide, re-checks due, floors not confirmed, weekly
                              floors still below on Thursday or later
  ## Decide                   the Recruiter's own words: kept verbatim on every rewrite (strategy_review.with_decide)
Writes only with --write (the wrap file); without it prints a summary. Nothing outward.
CLI:  python3 scripts/evening_wrap.py [--root JS] [--date YYYY-MM-DD] [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import lessons as ls  # noqa: E402
from js_common import JS_ROOT, MIN_TRIES, cli_date, fm  # noqa: E402

WRAP_DIR = Path("state") / "recruiter"
DECIDE_FOR_OWNER = "## For the owner to decide"   # the heading string as already written in wrap files
CAME_BACK = {"screen", "oa", "interviewing", "final", "offer", "rejected"}


def wrap_path(root: Path, day: date) -> Path:
    return Path(root) / WRAP_DIR / f"wrap-{day.isoformat()}.md"


def last_wrap(root: Path, before: date) -> Path | None:
    """The newest wrap-<date>.md dated before `before` (the evening routine runs on weekdays, so Monday reads Friday)."""
    folder = Path(root) / WRAP_DIR
    found = sorted(p for p in folder.glob("wrap-????-??-??.md") if p.stem[5:] < before.isoformat()) \
        if folder.is_dir() else []
    return found[-1] if found else None


def decide_items(path: Path) -> list[str]:
    """The bullets under the "For <owner> to decide" heading in a wrap file (the morning cards show them)."""
    items, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.strip() == DECIDE_FOR_OWNER
            continue
        if inside and line.startswith("- ") and line.strip() != "- none":
            items.append(line[2:].strip())
    return items


def _today_events(root: Path, day: date) -> list[dict[str, Any]]:
    """Events dated `day` (in your time zone) that came back from a company or a person: inbound mail and company stages."""
    import interviews as iv
    import tracker as tr
    out = []
    for slug, meta, body in tr.iter_applications(root) or []:
        for e in tr.parse_events(body):
            at = tr.parse_ts(e.at)
            kind = e.type.strip().lower()
            stage = kind.split(":", 1)[1] if kind.startswith("override:") else tr.EVENT_STAGE.get(kind)
            if not at or tr._day(at) != day or not (stage in CAME_BACK or kind in tr.INBOUND):
                continue
            side = {"warm": "person", "cold": "cold"}.get(iv.channel_of(meta), "unknown")
            out.append({"slug": slug, "company": meta.get("company"), "type": e.type, "stage": stage, "path": side,
                        "channel": ls._person_kind(meta) if side == "person" else side})
    return out


def build(root: Path, day: date) -> dict[str, Any]:
    import interviews as iv
    import learn
    root = Path(root)
    now = ls._end_of(day)
    apps, touches = ls.app_rows(root, now), ls.first_touches(root)
    prog = ls.progress(root, day, apps, touches)
    ex, ex_problems = ls.experiments(root, now, apps, touches)
    lessons_rows, lesson_problems = ls.lessons(root)
    due = ls.due(lessons_rows, day)
    rates = iv.rates(root, now)
    back = _today_events(root, day)
    rows, invalid = learn.read(root)
    candidates = [r for r in learn.current(rows) if r["status"] == "proposed" and r["date"] == day.isoformat()]
    debriefs = sorted(p.relative_to(root).as_posix() for p in (root / "prep" / "debriefs").glob(f"*{day.isoformat()}*.md")) \
        if (root / "prep" / "debriefs").is_dir() else []
    went = [r for r in apps if r["day"] == day.isoformat()]
    clock = ls.clock_line(root, day)
    goals = jc.load_goals(root)
    decide: list[str] = []
    for e in ex:
        if e["ready"]:
            decide.append(f"experiment {e['id']} is {e['state']}: {ls.arms_text(e)}; keep or revert what it tested")
    for d in due:
        decide.append(f"re-check due {d['recheck_on']} (LESSONS.md line {d['line']}): did \"{d['change']}\" help? "
                      "keep it, or revert it")
    if goals["source"] != jc.GOALS_FILE.as_posix():
        decide.append(f"set the goals: {jc.GOALS_FILE.as_posix()} is missing (reading {goals['source'] or 'nothing'})")
    elif goals["status"] not in ("set", "confirmed"):
        decide.append(f"confirm the floors in {goals['source']} (status {goals['status']})")
    if day.weekday() >= 3:
        for r in prog["rows"]:
            if r["weekly"] is not None and r["week"] is not None and r["week"] < r["weekly"]:
                decide.append(f"{r['metric'].replace('_', ' ')} this week {r['week']} of a floor of {r['weekly']} "
                              "with the week nearly out: what changes tomorrow")
    return {"date": day.isoformat(), "clock": clock, "progress": prog, "went_out": went,
            "first_touches_today": [t for t in touches if t["day"] == day.isoformat()], "came_back": back,
            "rates": rates, "min_tries": MIN_TRIES, "candidates": candidates, "learn_invalid": invalid,
            "rejections_today": [b for b in back if b["stage"] == "rejected"], "debriefs_today": debriefs,
            "experiments": ex, "due": due, "decide": decide, "problems": lesson_problems + ex_problems}


def _count(rows: list[dict[str, Any]], key: str) -> str:
    tally: dict[str, int] = {}
    for r in rows:
        tally[str(r[key])] = tally.get(str(r[key]), 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(tally.items())) or "none"


def render(w: dict[str, Any]) -> str:
    r = w["rates"]
    small = r["warm_n"] < w["min_tries"] or r["cold_n"] < w["min_tries"]
    L = [f"# Evening wrap {w['date']}", "",
         "Built by `scripts/evening_wrap.py` (charter section 3 step 7) from the records; tomorrow's morning cards "
         "read it. Every number carries its n. The `## Decide` section at the end is the Recruiter's and survives "
         "every rewrite.", "", f"- {w['clock']}", "", "## Numbers against the goals", ""]
    L += ls.goal_lines(w["progress"])
    L += ["", "## What went out", "",
          f"- applications today: {len(w['went_out'])} (by effort: {_count(w['went_out'], 'effort')}; by path: "
          f"{_count(w['went_out'], 'path')})",
          f"- first touches today: {len(w['first_touches_today'])}"]
    L += ["", "## What came back", ""]
    L += [f"- {b['company'] or b['slug']}: {b['type']} (path {b['path']}, channel {b['channel']}; `{b['slug']}`)"
          for b in w["came_back"]] or ["- nothing came back today"]
    L += ["", "## What worked", "",
          f"- interviews per application by path, all applications: person {r['warm_k']} of {r['warm_n']}, cold "
          f"{r['cold_k']} of {r['cold_n']}, unknown {r.get('unknown_n', 0)} not counted"
          + (f"; under {w['min_tries']} a side, so no weight changes on it (charter section 1)" if small else ""),
          f"- today's returns by channel: {_count(w['came_back'], 'channel')}"]
    L += ["", "## New LESSONS candidates", ""]
    L += [f"- {c['role']}: {c['observation']} (evidence: {c['evidence']})" + (f"; change: {c['change']}" if c["change"]
                                                                                 else "") for c in w["candidates"]]
    L += [f"- rejection today, `{b['slug']}`: write its coded LESSONS line (YRS FORM COLD AGE LAB PDF, \"inferred\" "
          "when no reason was given)" for b in w["rejections_today"]]
    L += [f"- debrief written today: {d}: its lesson goes through `scripts/learn.py add`" for d in w["debriefs_today"]]
    if not (w["candidates"] or w["rejections_today"] or w["debriefs_today"]):
        L += ["- none today"]
    if w["learn_invalid"]:
        L += [f"- {len(w['learn_invalid'])} invalid lines in state/learn (`python3 scripts/learn.py list`)"]
    L += ["", "## Experiments", ""]
    L += [f"- {e['id']}: {e['state']}; {ls.arms_text(e)} [{e['source']}]; read on: {e['read_on']}"
          for e in w["experiments"]] or ["- none open (state/recruiter/experiments.yaml)"]
    L += ["", "## Re-checks due", ""]
    L += [f"- {d['recheck_on']} (line {d['line']}): {d['what']} -> {d['change']}" for d in w["due"]] or ["- none"]
    if w["problems"]:
        L += ["", "## Ledger problems (the Recruiter fixes these)", ""] + [f"- {p}" for p in w["problems"]]
    L += ["", DECIDE_FOR_OWNER, ""] + ([f"- {d}" for d in w["decide"]] or ["- none"])
    return "\n".join(L) + "\n"


def write(root: Path, w: dict[str, Any]) -> Path:
    import strategy_review as sr
    path = wrap_path(root, date.fromisoformat(w["date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"type": "wrap", "date": w["date"], "built_by": "scripts/evening_wrap.py",
            "decide_items": len(w["decide"])}
    text = sr.with_decide(path, fm.dump(meta, render(w)))       # the Recruiter's "## Decide" kept verbatim
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--date", type=cli_date, default=None, help="the day (default: today, in your time zone)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    day = a.date or jc.dept_day(datetime.now(timezone.utc))
    w = build(a.root, day)
    if a.json:
        print(json.dumps(w, indent=1, default=str, ensure_ascii=False))
    else:
        print(f"Wrap {w['date']}: applications {len(w['went_out'])}, first touches {len(w['first_touches_today'])}, "
              f"came back {len(w['came_back'])}, lesson candidates {len(w['candidates'])}, experiments "
              f"{len(w['experiments'])} ({sum(e['ready'] for e in w['experiments'])} ready), re-checks due "
              f"{len(w['due'])}, for the owner to decide {len(w['decide'])}")
    if a.write:
        print(f"wrote {write(a.root, w).relative_to(a.root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
