#!/usr/bin/env python3
"""
lane_context.py - SessionStart hook.

Tells a session, before it does anything, whether it is inside a multi-session
programme and what its role is - so nobody has to be told how to connect and
start. It carries the two facts that cost the most when missed:

  * a session's ADDRESS is the registry name on the first line of ListAgents.
    A session TITLE is not an address.
  * there is no push. A ruling reaches a lane when the lane next looks.

A lane's start reads are its small STATE file and the live block of its ORDERS, not the whole append-only log, and
the hub's event feed (hub/events.jsonl) replaces re-reading SIGNAL.md: this hook prints the lane's events since its
STATE was last refreshed. Prints once per session (the plugin and a lane's settings may both register it). The session id line is
last so the stable lines above it keep the prompt cache warm.

Fail-open: any exception exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import (CODE_ROOT, KERNEL_DIR, WORKSPACE, find_hubs, in_agency_session, load_programme,  # noqa: E402
                         force_utf8_stdout, resolve_role)
try:
    from rulings_common import first_time  # noqa: E402
except Exception:  # fail-open: without the dedupe helper the hook still prints
    def first_time(kind: str, key: str | None) -> bool:  # type: ignore[misc]
        return True

EVENTS_MAX = 8


def recent_events(hub: Path, scope: str | None, since: float | None) -> list[str]:
    """Event lines for ALL or this scope newer than `since` (epoch), else the last 72 hours; newest last."""
    import datetime as dt
    p = hub / "events.jsonl"
    if not p.is_file():
        return []
    cut = since if since else (dt.datetime.now().timestamp() - 72 * 3600)
    rows = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(raw)
            ts = e["ts"]
            t = None
            for fmt in ("%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M"):
                try:
                    t = dt.datetime.strptime(ts, fmt)
                    break
                except ValueError:
                    continue
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.astimezone()
        except Exception:
            continue
        sc = [str(x).upper() for x in e.get("scope") or ["ALL"]]
        if t.timestamp() > cut and ("ALL" in sc or (scope and scope.upper() in sc)):
            where = f" ({e['where']})" if e.get("where") else ""
            rows.append(f"[events]  {ts[:16].replace('T', ' ')} [{','.join(sc)}] {e.get('text', '')}{where}")
    return rows[-EVENTS_MAX:]


def main() -> int:
    force_utf8_stdout()
    hubs = find_hubs()
    if not hubs:
        return 0

    try:
        payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not in_agency_session(payload=payload):       # a user-scope plugin fires in every folder: stay silent outside
        return 0
    session_id = payload.get("session_id")
    src = str(payload.get("source") or "startup")
    if not first_time(f"context-{src}", session_id):
        return 0

    try:
        cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    except Exception:
        cwd = WORKSPACE

    mine: list[tuple[Path, dict, str, str | None]] = []
    others: list[tuple[Path, dict]] = []
    for hubjson in hubs:
        hub = hubjson.parent
        prog = load_programme(hub)
        role, scope = resolve_role(cwd, prog, session_id)
        if role == "unknown":
            others.append((hub, prog))
        else:
            mine.append((hub, prog, role, scope))

    out: list[str] = []

    if not mine:
        names = ", ".join(sorted({p.get("programme", h.parent.name) for h, p in others}))
        out.append(f"[lanes] {len(others)} orchestrated programme(s) on disk: {names}.")
        out.append("[lanes] This session matches none of them by branch. If you ARE a lane, set "
                   "OS_LANE=<scope>, check out your lane branch, or add this session's id to your "
                   "lane's session_ids in hub/programme.json, then re-read your hub.")
        sys.stdout.write("\n".join(out) + "\n")
        return 0

    for hub, prog, role, scope in mine:
        rel = hub.relative_to(WORKSPACE).as_posix() if hub.is_relative_to(WORKSPACE) else str(hub)
        name = prog.get("programme", hub.parent.name)
        who = "ORCHESTRATOR" if role == "orchestrator" else f"{role.upper()} '{scope}'"

        out.append(f"[lanes] {name} - you are the {who}. Hub: {rel}")
        if role == "orchestrator":
            out.append(f"[lanes] Read: {rel}/ORCHESTRATOR.md (the live section at the top) -> "
                       f"`python3 {KERNEL_DIR}/hub_events.py since --hours 24`.")
        else:
            out.append(f"[lanes] Read: {rel}/lanes/{scope}-STATE.md (your live state, ~3 KB) -> the live block of "
                       f"lanes/{scope}-ORDERS.md. Your log lanes/{scope}.md is history: grep it, never read it whole; "
                       f"append to it (`cat >>`), never Read-then-Edit it.")

        out.append("[lanes] YOUR ADDRESS: run ListAgents - the FIRST line names this session. "
                   "That registry name is the only thing SendMessage resolves; a session TITLE "
                   "is NOT an address. Routine hand-offs go through the day queue and files, not messages.")

        if role == "orchestrator":
            out.append("[lanes] You may write: hub/README.md, ORCHESTRATOR.md, events.jsonl (via hub_events.py; "
                       "SIGNAL.md is generated from it), LANES-LIVE.md, RESOURCES.md, MERGE-TRAIN.md, RESEARCH-MAP.md, "
                       "briefs/*, lanes/*-ORDERS.md, TRACKER.md, DIVISION.md, classify.py. "
                       "NEVER hub/lanes/<id>.md - that is the lane's alone.")
            out.append("[lanes] You own ALL git on the shipping branch. A commit is the "
                       "certificate that the gate passed - re-run every load-bearing claim "
                       "yourself; a verdict you cannot reproduce goes back to the lane.")
        else:
            out.append(f"[lanes] You may write: hub/lanes/{scope}.md (yours alone) and your own "
                       "dossier/findings. NEVER another lane's file, a brief, ORDERS, "
                       "ORCHESTRATOR.md, TRACKER.md or the classifier - hand over ROWS.")
            out.append("[lanes] You do NOT commit or push on the shipping branch. You produce a "
                       "reviewed, evidenced CHANGE SET; the orchestrator converts it to a commit. "
                       "The Stop hook writes your handoff block and refreshes your STATE file.")

        out.append("[lanes] THERE IS NO PUSH - a ruling reaches you when you look: when you claim the next item, run "
                   f"`python3 {KERNEL_DIR}/hub_events.py since --lane {scope or 'ORCH'} --hours 24` (one line per event; "
                   "no SIGNAL re-read). A session is not a daemon: DO MORE PER TURN.")
        out.append(f"[lanes] Doctrine on demand: {CODE_ROOT}/skills/lane-orchestration/ (read references/messaging.md "
                   "only before addressing another session).")
        state = hub / "lanes" / f"{scope}-STATE.md" if scope else None
        since = state.stat().st_mtime if state is not None and state.is_file() else None
        ev = recent_events(hub, scope, since)
        if ev:
            out.append(f"[events] {len(ev)} event(s) for you since your last STATE refresh (hub/events.jsonl):")
            out.extend(ev)
        if session_id:
            out.append(f"[lanes] session_id {session_id}: a lane launched without OS_LANE "
                       "(the Desktop app) is bound by adding this to its lane's session_ids "
                       "in hub/programme.json.")

    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open, always
