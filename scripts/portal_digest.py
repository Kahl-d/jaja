#!/usr/bin/env python3
"""portal_digest.py - the morning brief's one line per portal. Read only.

Every portal is tracked like email: tags, trackers, follow-ups, jobs seen across portals, dates. portal_report.py is
the full table; this is the short daily view the morning-brief routine prints:

  new jobs      first seen in the last 24 hours, counted for every portal the job was seen on (dedup.portal_of plus
                each `sightings` portal); the header counts distinct jobs
  2+ portals    open jobs whose sightings include this portal and at least one other
  waiting       answers owed: follow_ups.py's portal-message rows for the portal
  follow-ups    follow_ups.py's other due rows (application, prospect, portal-thread) for the portal
  setup         the account: state/accounts.yaml (a platform-wide entry, tenant null) else the registry's presence
                account; alerts: the registry's presence alerts, else alerts_on. Setup is pending for a P0 or P1 portal
                whose account or alerts are not done.
A portal gets its own line when it has any count; portals whose only item is a pending setup step share one
"Setup pending:" line (account, alerts, a connector to add, or "register" for a recruiter firm or talent network);
the rest are named on one "Quiet:" line. An account marked not-wanted is settled. Until a parsed LinkedIn export
exists (state/harvest/linkedin-<date>.jsonl, network_map.latest_export) a "LinkedIn export pending" line shows every
day. Rows follow_ups.py files under "email" or no portal are summed on their own lines. Per-company ATS tenants in
state/accounts.yaml are counted per platform.

CLI:  python3 scripts/portal_digest.py [--root R] [--today YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import portal_registry  # noqa: E402

ACCOUNT_DONE = ("exists", "exists-inferred", "not-wanted")      # not-wanted: settled by the account owner
ALERTS_DONE = ("on", "arriving")
SETUP_PRIORITY = ("P0", "P1")
NONE_GROUP = "no portal"
EXPORT_URL = "https://www.linkedin.com/mypreferences/d/download-my-data"


def _accounts(root: Path) -> tuple[dict[str, str], dict[str, int]]:
    path = root / "state" / "accounts.yaml"
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
    wide, tenants = {}, {}
    for a in data.get("accounts") or []:
        p = str(a.get("platform") or "other")
        if a.get("tenant"):
            tenants[p] = tenants.get(p, 0) + int(bool(a.get("account_exists")))
        else:
            wide[p] = "exists" if a.get("account_exists") else "not-wanted"
    return wide, tenants


def digest(root: Path, now: datetime, follow: Callable[[Path, Any], dict[str, Any]] | None = None) -> dict[str, Any]:
    import dedup
    import fetch_ats as fa
    root = Path(root)
    if follow is None:
        import follow_ups
        follow = follow_ups.collect
    cutoff = now - timedelta(hours=24)
    new: dict[str, int] = {}
    multi: dict[str, int] = {}
    fresh_ids, multi_ids = set(), set()
    for rec in fa.load_jobs(root).values():
        sights = [s for s in rec.get("sightings") or [] if isinstance(s, dict)]
        portals = {dedup.portal_of(rec)} | {str(s.get("portal")) for s in sights}
        first = fa.parse_ts(rec.get("first_seen"))
        if first and first >= cutoff:
            fresh_ids.add(rec.get("id"))
            for p in portals:
                new[p] = new.get(p, 0) + 1
        if len({str(s.get("portal")) for s in sights}) >= 2 and rec.get("status") not in ("closed", "skipped"):
            multi_ids.add(rec.get("id"))
            for p in {str(s.get("portal")) for s in sights}:
                multi[p] = multi.get(p, 0) + 1
    fu = follow(root, now.date())
    msgs: dict[str, int] = {}
    nudges: dict[str, int] = {}
    for r in fu.get("due") or []:
        g = r.get("portal") or NONE_GROUP
        bucket = msgs if r.get("kind") == "portal-message" else nudges
        bucket[g] = bucket.get(g, 0) + 1
    wide, tenants = _accounts(root)
    rows, quiet = [], []
    known = set()
    for e in portal_registry.load(root).get("platforms") or []:
        pid = str(e.get("id"))
        known.add(pid)
        pres = e.get("presence") or {}
        account = wide.get(pid) or (pres.get("account") or {}).get("state") or "unknown"
        alerts = (pres.get("alerts") or {}).get("state") or ("on" if e.get("alerts_on") else "unknown")
        if account == "not-wanted":
            pending = False
        else:
            pending = str(e.get("setup_priority") or "") in SETUP_PRIORITY and (
                account not in ACCOUNT_DONE or (e.get("control") == "alert-mail" and alerts not in ALERTS_DONE))
        row = {"id": pid, "control": e.get("control"), "kind": pres.get("kind"), "new_24h": new.get(pid, 0), "multi": multi.get(pid, 0),
               "messages": msgs.get(pid, 0), "follow_ups": nudges.get(pid, 0), "account": account, "alerts": alerts,
               "setup_pending": bool(pending)}
        if row["new_24h"] or row["multi"] or row["messages"] or row["follow_ups"] or pending:
            rows.append(row)
        else:
            quiet.append(pid)
    other = {g: {"messages": msgs.get(g, 0), "follow_ups": nudges.get(g, 0)}
             for g in sorted(set(msgs) | set(nudges)) if g not in known}
    import network_map
    export = network_map.latest_export(root)          #: the network map waits for this parse
    return {"date": now.date().isoformat(), "new_jobs": len(fresh_ids), "multi_jobs": len(multi_ids),
            "linkedin_export": str(export.relative_to(root)) if export else None,
            "messages": sum(msgs.values()), "follow_ups": sum(nudges.values()), "portals": rows, "quiet": quiet,
            "other": other, "tenants": tenants}


def _setup_hint(r: dict[str, Any]) -> str:
    if r.get("control") == "connector":
        return "add the connector in Claude"
    if r.get("kind") in portal_registry.FIRM_KINDS:     #: a recruiter firm or talent network: you register once
        return "register"
    if r["account"] not in ACCOUNT_DONE:
        return "account"
    return "alerts"


def _has_news(r: dict[str, Any]) -> bool:
    return bool(r["new_24h"] or r["multi"] or r["messages"] or r["follow_ups"])


def render(d: dict[str, Any]) -> str:
    plural = lambda n, word: f"{n} {word}" + ("" if n == 1 else "s")  # noqa: E731
    lines = [f"Portals {d['date']}: {d['new_jobs']} new jobs in 24 h, {d['multi_jobs']} on 2+ portals, "
             f"{d['messages']} messages waiting, {d['follow_ups']} follow-ups due"]
    for r in (r for r in d["portals"] if _has_news(r)):
        bits = [f"{r['new_24h']} new", f"{r['multi']} on 2+ portals", f"{r['messages']} waiting",
                plural(r["follow_ups"], "follow-up"), f"account {r['account']}, alerts {r['alerts']}"]
        if r["setup_pending"]:
            bits.append(f"setup: {_setup_hint(r)}")
        lines.append(f"- {r['id']}: " + " · ".join(bits))
    for g, v in d["other"].items():
        lines.append(f"- {g}: {v['messages']} waiting · {plural(v['follow_ups'], 'follow-up')}")
    setup_only = [r for r in d["portals"] if r["setup_pending"] and not _has_news(r)]
    if setup_only:
        lines.append("Setup pending: " + ", ".join(f"{r['id']} ({_setup_hint(r)})" for r in setup_only))
    if d.get("linkedin_export") is None and "linkedin_export" in d:
        lines.append(f"LinkedIn export pending: request it at {EXPORT_URL} (Connections, Messages, Invitations, "
                     "Profile), then python3 scripts/linkedin_export.py parse <zip>")
    if d["tenants"]:
        lines.append("Company portal accounts: " + ", ".join(f"{p} {n}" for p, n in sorted(d["tenants"].items())))
    lines.append("Quiet: " + (", ".join(d["quiet"]) if d["quiet"] else "none"))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    now = (datetime.combine(jc.parse_date(a.today), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=15)
           if a.today else datetime.now(timezone.utc))
    d = digest(a.root, now)
    print(json.dumps(d, indent=2) if a.json else render(d), end="\n" if a.json else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
