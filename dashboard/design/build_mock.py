"""Build dashboard/design/mock-home-pipeline.html from a read-only dash_api snapshot (snap.json) and extra.json."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

S = Path(sys.argv[1])
OUT = Path(sys.argv[2])
snap = json.load(open(S / "snap.json"))
extra = json.load(open(S / "extra.json"))
today = date.fromisoformat(snap["today"])
o = snap["overview"]
f = {x["step"]: x for x in o["funnel"]}
apps = snap["applications"]
applied = [a for a in apps if a["applied"]]
STAGES = ["fit_checked", "tailored", "applied", "confirmation", "screen", "interviewing", "final", "offer"]
LABEL = {"fit_checked": "Scored", "tailored": "Page ready", "applied": "Applied", "confirmation": "Confirmed",
         "screen": "Screen", "interviewing": "Interviewing", "final": "Final", "offer": "Offer"}

since = (today - timedelta(days=1)).isoformat()
TYPE = {"applied": "Applied", "submitted": "Applied", "confirmation_received": "Confirmation", "screen": "Screen booked",
        "email_in": "Reply", "reply": "Reply", "recruiter_reply": "Reply", "rejected": "Rejected", "resume_ready": "Page ready",
        "tailored": "Page ready"}
updates = [e for e in extra["events"] if e["day"] >= since and e["type"] in TYPE]
upd_counts = {}
for e in updates:
    upd_counts[TYPE[e["type"]]] = upd_counts.get(TYPE[e["type"]], 0) + 1

# 90-day tracker: events per day from the clock start (pre-clock days fold into "before day 1")
start = date.fromisoformat(snap["clock"]["clock_start"])
plot = {}
for e in extra["events"]:
    kind = {"applied": "applied", "submitted": "applied", "email_in": "reply", "reply": "reply",
            "recruiter_reply": "reply", "screen": "round", "interviewing": "round"}.get(e["type"])
    if not kind:
        continue
    d = (date.fromisoformat(e["day"]) - start).days + 1
    key = str(max(d, 0))
    plot.setdefault(key, {"applied": 0, "reply": 0, "round": 0})[kind] += 1

rounds = [a for a in apps if a["stage"] in ("screen", "interviewing", "final")]
cal = []
for a in rounds:
    when = a.get("next_action_at")
    cal.append({"company": a["company"], "title": a["title"], "stage": a["stage"], "when": when,
                "pack": a["slug"] in extra["prep_packs"]})

via = o["response_by_via"] or {}
warm = via.get("recruiter", {}) or {}
cold = via.get("ats", {}) or {}
lat = o["apply_latency_hours"] or {}
dream_n = sum(1 for a in applied if a.get("tier") == "dream" or str(a.get("company_tier")) == "dream")
week = {r["metric"]: r for r in o["weeks"]["rows"]}

due_today = [x for x in snap["follow_ups"] if x.get("due") and x["due"] <= today.isoformat()]
tasks = [{"what": f"Approve or reject {extra['approvals_pending']} waiting approvals", "n": extra["approvals_pending"],
          "where": "work/approvals"},
         {"what": f"{len(due_today)} follow-ups due (oldest {max((x.get('days_overdue') or 0) for x in due_today)} days overdue)",
          "n": len(due_today), "where": "follow-ups"}]
for a in rounds:
    tasks.insert(0, {"what": f"Prep: {a['company']} {a['stage']}" + ("" if a["slug"] in extra["prep_packs"] else " (no prep pack)"),
                     "n": None, "where": a["slug"]})

pipeline = []
for a in apps:
    if a["stage"] not in STAGES:
        continue
    ss = extra["stage_since"].get(a["slug"])
    days = (today - date.fromisoformat(ss)).days if ss else None
    pipeline.append({"company": a["company"], "title": a["title"], "stage": a["stage"], "days": days,
                     "next": a.get("next_action"), "next_at": a.get("next_action_at"), "warm": len(a.get("people") or []),
                     "fit": a.get("fit"), "tier": a.get("tier")})

data = {
    "today": snap["today"], "generated_at": snap["generated_at"], "clock": snap["clock"],
    "kpis": [
        {"label": "Applied", "value": f["applied"]["count"], "n": None, "trend": week.get("applied", {}).get("delta"),
         "note": f"{week.get('applied', {}).get('this', 0)} this week"},
        {"label": "Response, warm vs cold", "value": f"{warm.get('responded', 0)}/{warm.get('applied', 0)} vs {cold.get('responded', 0)}/{cold.get('applied', 0)}",
         "n": warm.get("applied", 0) + cold.get("applied", 0), "trend": None,
         "note": "recruiter path vs cold ATS; the live KPI is interviews per application (crm.py, interviews.rates)"},
        {"label": "Screens", "value": f["screen"]["count"], "n": f["applied"]["count"], "trend": week.get("interviews", {}).get("delta"),
         "note": "of applied"},
        {"label": "Days to apply (median)", "value": round(lat.get("median", 0) / 24, 1) if lat.get("median") else None,
         "n": lat.get("measured"), "trend": None, "note": f"{lat.get('within_72h', 0)} within 72 h"},
        {"label": "Days left", "value": snap["clock"]["days_left"], "n": None, "trend": None,
         "note": f"clock {snap['clock']['clock_start']} to {snap['clock']['clock_end']}"},
    ],
    "band48": {"rounds": cal, "replies_waiting": sum(1 for x in snap["follow_ups"] if "answer owed" in str(x.get("next"))),
               "approvals": extra["approvals_pending"], "follow_ups": len(due_today), "calendar": None},
    "findings": [
        {"text": f"Warm paths reply; cold applications have not yet: recruiter path {warm.get('responded', 0)} of {warm.get('applied', 0)}, cold ATS {cold.get('responded', 0)} of {cold.get('applied', 0)}.",
         "n": warm.get("applied", 0), "enough": warm.get("applied", 0) >= 10 and cold.get("applied", 0) >= 10},
        {"text": f"Applying speed: median {round(lat.get('median', 0) / 24, 1)} days from posting to applying; {lat.get('within_72h', 0)} of {lat.get('measured', 0)} within 72 h.",
         "n": lat.get("measured", 0), "enough": (lat.get("measured") or 0) >= 10},
        {"text": f"Dream share: {dream_n} of {len(applied)} applications are at dream-tier companies.",
         "n": len(applied), "enough": len(applied) >= 10},
    ],
    "updates": updates[-12:][::-1], "update_counts": upd_counts, "plot": plot, "tasks": tasks,
    "pipeline": pipeline, "stages": STAGES, "labels": LABEL,
    "queue_note": f"NEXT-UP: {len(snap['queue']['rows'])} rows; each is checked against its application's stage at build (D2)",
}

html = (Path(__file__).with_name("mock_template.html")).read_text(encoding="utf-8")
OUT.write_text(html.replace("/*DATA*/null", json.dumps(data, default=str)), encoding="utf-8")
print("wrote", OUT, len(pipeline), "pipeline cards,", len(updates), "updates")
