#!/usr/bin/env python3
"""portal_report.py - one line per portal, and every job seen on more than one portal.

Every portal is tracked like email: tags, trackers, follow-ups, jobs seen across portals, dates. Read-only; the
Recruiter's brief calls it.

Per portal (targets/platforms.yaml entries, plus presence_surfaces that are portals, through portal_registry.py):
  account and alerts   presence.account.state, presence.alerts.state (else alerts_on), control and status
  last alert           the newest row for the portal in state/job-alerts.jsonl
  new jobs             jobs first seen today and in the last 7 days, counted for every portal the job was seen on
                       (dedup.portal_of of the record, plus each `sightings` portal)
  applications         applications/<slug>/application.md with applied_at set, by applied_via (ats -> company-ats)
  messages waiting     inbound mail in state/harvest/*.classified.jsonl tagged with the portal (mail_classify's
                       `portal`, else portal_registry.portal_for_sender), labelled as a person writing to the candidate,
                       in the last WAIT_DAYS days, whose thread has no outbound touch after it
  follow-ups due       prospects.py due() rows on a LinkedIn channel count for linkedin
Then each job with 2 or more sightings: the portals and the date it was first seen on each.

    python3 scripts/portal_report.py [--root R] [--today YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402
import portal_registry  # noqa: E402

WAIT_DAYS = 14
PERSON_LABELS = frozenset({"recruiter_outreach", "hiring_manager_outreach", "founder_outreach", "referral_intro",
                           "networking", "interview_invite", "interview_scheduling"})
NOT_PORTAL_KINDS = frozenset({"website", "code", "research"})
VIA = {"ats": "company-ats"}


def _day(value: Any) -> date | None:
    d = jc.parse_date(str(value or "")[:10]) if value else None
    return d


def _portals(root: Path) -> list[dict[str, Any]]:
    reg = portal_registry.load(root)
    rows = []
    for e in reg.get("platforms") or []:
        pres = e.get("presence") or {}
        rows.append({"id": e.get("id"), "name": e.get("name"), "control": e.get("control"), "status": e.get("status"),
                     "account": (pres.get("account") or {}).get("state") or "unknown",
                     "alerts": (pres.get("alerts") or {}).get("state") or ("on" if e.get("alerts_on") else "unknown")})
    for s in reg.get("presence_surfaces") or []:
        if s.get("kind") in NOT_PORTAL_KINDS:
            continue
        rows.append({"id": s.get("id"), "name": s.get("name"), "control": "manual", "status": "not vetted",
                     "account": (s.get("account") or {}).get("state") or "unknown",
                     "alerts": (s.get("alerts") or {}).get("state") or "unknown"})
    return rows


def _last_alerts(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in jc.read_jsonl(root / "state" / "job-alerts.jsonl"):
        p, at = row.get("platform"), str(row.get("at") or "")
        if p and at > out.get(p, ""):
            out[p] = at
    return out


def _jobs(root: Path, today: date) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]]]:
    import dedup
    import fetch_ats as fa
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"today": 0, "week": 0})
    multi = []
    for rec in fa.load_jobs(root).values():
        sightings = rec.get("sightings") or []
        portals = {dedup.portal_of(rec)} | {s.get("portal") for s in sightings if isinstance(s, dict)}
        first = _day(rec.get("first_seen"))
        for p in portals - {None}:
            if first == today:
                counts[p]["today"] += 1
            if first and today - timedelta(days=6) <= first <= today:
                counts[p]["week"] += 1
        if len(sightings) >= 2:
            multi.append({"id": rec.get("id"), "company": rec.get("company"), "title": rec.get("title"),
                          "status": rec.get("status"),
                          "seen": [(s.get("portal"), str(s.get("first_seen") or "")[:10]) for s in sightings]})
    return counts, sorted(multi, key=lambda m: str(m["id"]))


def _applications(root: Path, today: date) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "week": 0})
    for path in sorted((root / "applications").glob("*/application.md")):
        rec, _ = jc.read_record(path)
        applied = _day(rec.get("applied_at"))
        if not applied:
            continue
        via = str(rec.get("applied_via") or "unknown")
        p = VIA.get(via, via)
        out[p]["total"] += 1
        out[p]["week"] += int(today - timedelta(days=6) <= applied <= today)
    return out


def _waiting(root: Path, today: date) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    last_out: dict[str, str] = {}
    for t in jc.iter_touches(root):
        if t.get("direction") == "out" and t.get("thread_id"):
            last_out[t["thread_id"]] = max(last_out.get(t["thread_id"], ""), str(t.get("at") or ""))
    seen = set()
    for path in sorted((root / "state" / "harvest").glob("*.classified.jsonl")):
        for row in jc.read_jsonl(path):
            label = (row.get("classification") or {}).get("label")
            if row.get("direction", "in") != "in" or label not in PERSON_LABELS or row.get("id") in seen:
                continue
            seen.add(row.get("id"))
            at = str(row.get("internal_date") or "")
            if not at or (_day(at) or date.min) < today - timedelta(days=WAIT_DAYS):
                continue
            portal = row.get("portal") or portal_registry.portal_for_sender(root, row.get("from_email") or "")
            if portal and last_out.get(row.get("thread_id") or "", "") < at:
                out[portal] += 1
    return out


def _follow_ups(root: Path, today: date) -> dict[str, int]:
    import prospects
    roots = cu.Roots(js=root, os=root.parent)
    out: dict[str, int] = defaultdict(int)
    for row in prospects.due(roots, today)["due"]:
        if str(row.get("channel") or "").startswith("linkedin"):
            out["linkedin"] += 1
    return out


def report(root: Path, today: date) -> dict[str, Any]:
    root = Path(root)
    alerts, (jobs, multi) = _last_alerts(root), _jobs(root, today)
    apps, waiting, follow = _applications(root, today), _waiting(root, today), _follow_ups(root, today)
    rows = []
    for p in _portals(root):
        pid = p["id"]
        rows.append({**p, "last_alert": alerts.get(pid), "new_today": jobs[pid]["today"], "new_week": jobs[pid]["week"],
                     "applied_total": apps[pid]["total"], "applied_week": apps[pid]["week"],
                     "messages_waiting": waiting.get(pid, 0), "follow_ups_due": follow.get(pid, 0)})
    return {"today": today.isoformat(), "portals": rows, "multi_portal_jobs": multi}


def render(rep: dict[str, Any]) -> str:
    lines = [f"# Portals {rep['today']} (portal_report.py, read only)", "",
             "| portal | channel | account | alerts | last alert | new today | new 7 d | applied (7 d) | messages waiting | follow-ups due |",
             "|---|---|---|---|---|--:|--:|--:|--:|--:|"]
    for r in rep["portals"]:
        lines.append(f"| {r['id']} | {r['control'] or '?'} | {r['account']} | {r['alerts']} | {str(r['last_alert'] or 'none')[:10]} "
                     f"| {r['new_today']} | {r['new_week']} | {r['applied_total']} ({r['applied_week']}) "
                     f"| {r['messages_waiting']} | {r['follow_ups_due']} |")
    lines += ["", f"## Jobs seen on 2 or more portals ({len(rep['multi_portal_jobs'])})", ""]
    for m in rep["multi_portal_jobs"]:
        seen = ", ".join(f"{p} {d}" for p, d in m["seen"])
        lines.append(f"- {m['id']} · {m['company']} · {m['title']} ({m['status']}): {seen}")
    if not rep["multi_portal_jobs"]:
        lines.append("- none yet")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(jc.JS_ROOT))
    ap.add_argument("--today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    today = jc.parse_date(a.today) if a.today else jc.today()
    rep = report(Path(a.root), today)
    print(json.dumps(rep, indent=2, default=str) if a.json else render(rep), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
