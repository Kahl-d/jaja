#!/usr/bin/env python3
"""interviews.py: the one number the agency is aimed at.

The whole system's goal is interviews first, as many and as fast as possible, against the search clock. This module
is the shared source for every surface that shows it:
  - booked(root, today): interviews booked this week (Monday to Sunday, in your time zone) and last week, from the tracker's
    events: screen, recruiter screen, phone screen, OA, interview, onsite, final (an event dated in the week);
  - rates(root, now): the measured interview rate per channel, an interview being a screen, OA, interview or final
    (a rejection is a reply, not an interview), shrunk toward the overall rate with the weight of 10 applications.
    The channel is the application's `path`: person = warm, cold = cold, unknown = not counted; only
    a record without `path` falls back to `applied_via` (ats = cold, any other value = warm). Every rate carries its
    n (warm_n, cold_n, and unknown_n for the applications left out);
  - held(root, today): rounds held this week and last week, apart from booked (charter section 11): a round is held
    only on a held event or a later round, never because its time passed; totals(root, today) for all rounds;
  - upcoming(root, today): the rounds still to happen with their structured time (round_at), and a flag when no time
    is recorded or the records disagree (never a guess); the cards header reads it;
  - target(root): the weekly target the owner set in state/recruiter/target.yaml (`weekly_interviews`), or None
    ("target not set"; the Recruiter proposes one, the owner sets it);
  - propose(rates, warm_per_week, cold_per_week): the interviews a week the measured rates predict, for the Recruiter's
    proposal.
Usage: python3 scripts/interviews.py [--today YYYY-MM-DD] [--json]   (prints the line agency_status and the plans use)
No network; read only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agency_config as cfg  # noqa: E402
from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
LA = ZoneInfo(str(cfg.get("identity.timezone", "UTC")))     # your zone: weeks run Monday to Sunday in it
TZ_LABEL = str(cfg.get("identity.timezone_label", "UTC"))
INTERVIEW_STAGES = {"screen", "oa", "interviewing", "final"}
ROUND_ENDS = {"rejected", "withdrawn", "ghosted", "on_hold"}
ROUND_GAP_DAYS = 21                      # a same-stage event further from its round's booking starts a new round


def _day(at: str) -> date | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(at).strip()):     # a date-only stamp is its own day (no UTC midnight)
        return date.fromisoformat(str(at).strip())
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(LA).date()


INTERVIEW_ORDER = ("screen", "oa", "interviewing", "final")


def rounds(root: Path) -> list[dict[str, Any]]:
    """Every interview round in the tracker's events, one per round, with whether it was held.

    In event time order per application: an interview-stage event opens a round (booked that day). A later event of the
    same stage within ROUND_GAP_DAYS is the round's held event (e.g. a screen logged when invited and again when held),
    unless it carries a `round_at=` later than its own time: then it only records when the round is, and a second,
    different time is kept as a conflict (never resolved by guessing). A `[reschedule]` event moves the round (its
    `round_at` replaces the times). A later interview stage means the round before it was held. A rejection,
    withdrawal, ghosting or hold ends a round that was not held. A round counts as held only on that evidence; a round
    whose time has passed with no held record is `unconfirmed`, never held."""
    import tracker
    out: list[dict[str, Any]] = []
    for slug, meta, body in tracker.iter_applications(root) or []:
        cur: dict[str, Any] | None = None
        timed = sorted(((tracker.parse_ts(ev.at), ev) for ev in tracker.parse_events(body) if tracker.parse_ts(ev.at)),
                       key=lambda t: t[0])
        for when, ev in timed:
            kind = ev.type.lower()
            stage = kind.split(":", 1)[1] if kind.startswith("override:") else tracker.EVENT_STAGE.get(kind)
            d = _day(ev.at)
            if stage in ROUND_ENDS:
                if cur and not cur["held"]:
                    cur["ended"] = stage
                cur = None
                continue
            if stage not in INTERVIEW_STAGES or d is None:
                continue
            info = tracker.round_info(ev.evidence)
            at = tracker.parse_ts(info["round_at"]) if info["round_at"] else None
            if info["reschedule"]:                    # a moved round is the same round
                if cur and at:
                    cur["times"] = [at]
                continue
            if cur and cur["stage"] == stage and (d - cur["booked"]).days <= ROUND_GAP_DAYS:
                if at and at > when:                  # it says when the round is: a time, not the round held
                    if at not in cur["times"]:
                        cur["times"].append(at)
                    continue
                cur.update(held=True, held_on=at.astimezone(LA).date() if at else d, held_by="held event")
                cur = None
                continue
            if cur and not cur["held"] and INTERVIEW_ORDER.index(stage) > INTERVIEW_ORDER.index(cur["stage"]):
                held_at = cur["times"][-1] if cur["times"] and cur["times"][-1] <= when else None
                cur.update(held=True, held_on=held_at.astimezone(LA).date() if held_at else d, held_by="next round")
            cur = {"slug": slug, "company": str(meta.get("company") or ""), "stage": stage, "type": ev.type,
                   "booked": d, "times": [at] if at else [], "held": False, "held_on": None, "held_by": None,
                   "ended": None}
            out.append(cur)
    return out


def _pt(at: datetime) -> str:
    return at.astimezone(LA).strftime("%Y-%m-%d %H:%M")


def round_view(r: dict[str, Any], today: date) -> dict[str, Any]:
    """One round as plain JSON: status held | ended | unconfirmed | upcoming, its structured time and any flag."""
    times = r["times"]
    at = times[-1] if len(times) == 1 else None
    if r["held"]:
        status = "held"
    elif r["ended"]:
        status = "ended"
    elif times and max(t.astimezone(LA).date() for t in times) < today:
        status = "unconfirmed"
    else:
        status = "upcoming"
    flag = None
    if len(times) > 1:
        flag = "records disagree: " + " vs ".join(f"{_pt(t)} {TZ_LABEL}" for t in times) + "; confirm the time, never guess"
    elif not times and status == "upcoming":
        flag = "no round time recorded: tracker.py event <slug> --type <stage> --round-at <ISO date and time>"
    elif status == "unconfirmed":
        flag = "the round's time has passed and no held event is recorded"
    return {"slug": r["slug"], "company": r["company"], "stage": r["stage"], "booked": r["booked"].isoformat(),
            "status": status, "round_at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if at else None,
            "date_pt": at.astimezone(LA).date().isoformat() if at else None,
            "time_pt": at.astimezone(LA).strftime("%H:%M") if at else None,
            "times_pt": [_pt(t) for t in times], "held_on": r["held_on"].isoformat() if r["held_on"] else None,
            "held_by": r["held_by"], "ended": r["ended"], "flag": flag}


def upcoming(root: Path, today: date) -> list[dict[str, Any]]:
    """The rounds still to happen as of `today` (your time zone), soonest first; a round with no recorded time or with
    conflicting times is listed with its flag. Keys (stable, for the cards header): slug, company, stage, booked,
    status ("upcoming"), round_at (UTC ISO or None), date_pt, time_pt (HH:MM or None), times_pt (every recorded time,
    "YYYY-MM-DD HH:MM"), held_on, held_by, ended, flag (None or the reason to check)."""
    views = [round_view(r, today) for r in rounds(root)]
    up = [v for v in views if v["status"] == "upcoming"]
    return sorted(up, key=lambda v: (v["times_pt"][0] if v["times_pt"] else "9999", v["booked"], v["slug"]))


def _weeks(today: date) -> tuple[date, dict[str, tuple[date, date]]]:
    monday = today - timedelta(days=today.weekday())
    return monday, {"this_week": (monday, monday + timedelta(days=6)),
                    "last_week": (monday - timedelta(days=7), monday - timedelta(days=1))}


def booked(root: Path, today: date) -> dict[str, Any]:
    """Interview rounds booked this week and last week (Monday start, your time zone), one count per round (rounds()): a
    round counts in the week of its first event, the day it was booked."""
    monday, weeks = _weeks(today)
    out: dict[str, Any] = {k: 0 for k in weeks} | {"items": []}
    for r in rounds(root):
        for k, (a, b) in weeks.items():
            if a <= r["booked"] <= b:
                out[k] += 1
                out["items"].append({"slug": r["slug"], "type": r["type"], "stage": r["stage"],
                                     "date": r["booked"].isoformat(), "week": k})
    out["week_of"] = monday.isoformat()
    return out


def held(root: Path, today: date) -> dict[str, Any]:
    """Interview rounds held this week and last week, by the day held (a held event, or the next round; rounds())."""
    monday, weeks = _weeks(today)
    out: dict[str, Any] = {k: 0 for k in weeks} | {"items": []}
    for r in rounds(root):
        for k, (a, b) in weeks.items():
            if r["held_on"] and a <= r["held_on"] <= b:
                out[k] += 1
                out["items"].append({"slug": r["slug"], "stage": r["stage"], "date": r["held_on"].isoformat(),
                                     "by": r["held_by"], "week": k})
    out["week_of"] = monday.isoformat()
    return out


def totals(root: Path, today: date) -> dict[str, int]:
    """All rounds to date: booked, held, upcoming, unconfirmed (time passed, no held record), ended unheld."""
    views = [round_view(r, today) for r in rounds(root)]
    return {"booked": len(views), **{s: sum(v["status"] == s for v in views)
                                     for s in ("held", "upcoming", "unconfirmed", "ended")}}


PATH_SIDE = {"person": "warm", "cold": "cold", "unknown": "unknown"}     # the recorded path


def channel_of(meta: dict[str, Any]) -> str:
    """warm, cold or unknown for one application. The recorded `path` decides (person = warm, cold = cold, unknown =
    unknown); only when `path` is absent does `applied_via` decide, as before the path field existed: ats = cold, any other value
    (referral, recruiter, email, ...) = warm, none or unknown = unknown."""
    path = str(meta.get("path") or "").strip().lower()
    if path:
        return PATH_SIDE.get(path, "unknown")
    via = str(meta.get("applied_via") or "")
    if not via or via == "unknown":
        return "unknown"
    return "cold" if via == "ats" else "warm"


def channel_counts(root: Path, now: datetime) -> dict[str, dict[str, int]]:
    """Applied applications per channel and how many reached an interview stage (screen, OA, interview, final). A
    rejection is a reply, not an interview, so it never counts. The channel is
    channel_of(meta): the recorded `path` first (person = warm, cold = cold), `applied_via` only when `path` is absent.
    An application whose channel is unknown is not in warm or cold; "unknown" counts it so the n left out is shown."""
    import tracker
    out = {"cold": {"applied": 0, "interviewed": 0}, "warm": {"applied": 0, "interviewed": 0},
           "unknown": {"applied": 0, "interviewed": 0}}
    for _slug, meta, body in tracker.iter_applications(root) or []:
        events = tracker.parse_events(body)
        if tracker.project(events, now.astimezone(timezone.utc))["applied_at"] is None:
            continue
        side = out[channel_of(meta)]
        side["applied"] += 1
        side["interviewed"] += int(any(tracker.EVENT_STAGE.get(e.type.lower()) in INTERVIEW_STAGES or
                                       (e.type.lower().startswith("override:") and
                                        e.type.split(":", 1)[1] in INTERVIEW_STAGES) for e in events))
    return out


def rates(root: Path, now: datetime) -> dict[str, Any]:
    """Interviews per application per channel (channel_counts: `path` first), shrunk toward the overall rate of the
    counted applications with the weight of 10 applications. unknown_n is the applied applications left out."""
    c = channel_counts(root, now)
    cold, warm = c["cold"], c["warm"]
    n = cold["applied"] + warm["applied"]
    p0 = (cold["interviewed"] + warm["interviewed"] + 1) / (n + 2)

    def shrink(d: dict[str, int]) -> float:
        return (d["interviewed"] + 10 * p0) / (d["applied"] + 10)
    return {"cold": shrink(cold), "warm": shrink(warm), "cold_n": cold["applied"], "cold_k": cold["interviewed"],
            "warm_n": warm["applied"], "warm_k": warm["interviewed"], "unknown_n": c["unknown"]["applied"]}


def target(root: Path) -> int | None:
    p = root / "state" / "recruiter" / "target.yaml"
    if not p.is_file():
        return None
    import yaml
    v = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("weekly_interviews")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def propose(r: dict[str, Any], warm_per_week: int, cold_per_week: int) -> float:
    return round(r["warm"] * warm_per_week + r["cold"] * cold_per_week, 1)


def line(root: Path, today: date, now: datetime | None = None) -> str:
    b = booked(root, today)
    t = target(root)
    r = rates(root, now or datetime.now(LA))
    goal = f"target {t}" if t else "target not set (the Recruiter proposes, you set)"
    h = held(root, today)
    return (f"Interviews booked this week (from {b['week_of']}): {b['this_week']}, {goal}; last week {b['last_week']}. "
            f"Held this week: {h['this_week']}; last week {h['last_week']}. "
            f"Interview rates: warm {r['warm_k']} of {r['warm_n']}, cold ATS {r['cold_k']} of {r['cold_n']} "
            f"(expected {r['warm']:.0%} and {r['cold']:.0%} per application).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else datetime.now(LA).date()
    if a.json:
        print(json.dumps({"booked": booked(a.root, today), "held": held(a.root, today),
                          "upcoming": upcoming(a.root, today), "totals": totals(a.root, today),
                          "target": target(a.root), "rates": rates(a.root, datetime.now(LA))}, indent=2, default=str))
    else:
        print(line(a.root, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
