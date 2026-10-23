#!/usr/bin/env python3
"""
lane_register.py - SessionStart. THE AUTO-REGISTRY.

Every session that starts inside a programme registers ITSELF into the hub:
its peer address, its name, its worktree, its branch, its tip, its role.
Nobody types it. Nobody remembers it. Nobody guesses it.

WHY THIS EXISTS - measured on a large multi-session programme:
    every cross-session send that was refused went to a session TITLE or to a
    remembered name, and the orchestrator sent replies to the WRONG session
    because it INFERRED an address.

WHAT MAKES IT POSSIBLE - measured 2026-08-27, and documented:
    Claude Code exports the session's own inbox address to every hook as
    CLAUDE_CODE_MESSAGING_SOCKET, before SessionStart runs. That string is the
    same `uds:` peer address `/status` prints and the same one an inbound
    message carries in its `from` attribute.
    -> a session can always answer "what is my address" without being told.

    Docs: code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket
    Env:  code.claude.com/docs/en/env-vars#variables

THE RULE THIS ENFORCES:
    An address the OWNER supplies is authoritative.
    An address the orchestrator INFERS is a guess that misroutes.

WHO MAY TAKE THE ORCH ROW - measured twice:
    a Desktop-launched lane (no OS_LANE, on main) replaced ORCH with its own
    socket; a scheduled task then replaced it with address "" and messaging
    DOWN. The branch rule makes EVERY
    session on the shipping branch "the orchestrator", so:
      - a session with no messaging socket never registers (it cannot be reached,
        and a row that says so only erases one that could);
      - a live ORCH row (address set, socket still on disk) held by another
        session is never replaced unless OS_LANE=orchestrator says so.

Fail-open: any exception exits 0 silently. Never blocks a session from starting.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import (WORKSPACE, find_hubs, in_agency_session, load_programme, force_utf8_stdout,  # noqa: E402
                         resolve_role, declared_orchestrator, _git)

REGISTRY = "registry.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_is_live(row: dict) -> bool:
    """A row is live if it carries an address whose socket is still on disk.
    An address we cannot check counts as live: keeping a stale row costs a
    retry, erasing a live one cuts the programme off from its orchestrator."""
    addr = str(row.get("address") or "")
    if not addr:
        return False
    if addr.startswith("uds:"):
        try:
            return Path(addr[4:]).exists()
        except Exception:
            return True
    return True


def main() -> int:
    force_utf8_stdout()
    hubs = find_hubs()
    if not hubs:
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    session_id = payload.get("session_id") or ""
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    if not in_agency_session(cwd, payload):          # a user-scope plugin fires in every folder: stay silent outside
        return 0

    # THE ADDRESS. Exported by Claude Code itself - not remembered, not inferred.
    socket = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
    addr = socket if socket.startswith("uds:") else (f"uds:{socket}" if socket else "")

    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or ""
    tip = _git(cwd, "rev-parse", "--short", "HEAD") or ""
    dirty = _git(cwd, "status", "--porcelain") or ""
    dirty_n = len([l for l in dirty.splitlines() if l.strip()])

    out: list[str] = []
    for hubjson in hubs:
        hub = hubjson.parent
        prog = load_programme(hub)
        role, scope = resolve_role(cwd, prog, session_id)
        if role == "unknown":
            continue

        if not addr:
            out.append(f"[lane-registry] not registered in {hub.parent.name}: this session has "
                       "no messaging socket (CLAUDE_CODE_MESSAGING_SOCKET), so no peer could "
                       "reach it. If it should be reachable, check `claude --version` (native Windows "
                       "needs v2.1.234+) and that no telemetry/feature-flag env var disabled "
                       "messaging. The hub files remain the contract.")
            continue

        key = "ORCH" if role == "orchestrator" else str(scope)
        entry = {
            "key": key,
            "role": role,
            "scope": scope,
            "address": addr,
            "session_id": session_id,
            "cwd": str(cwd),
            "branch": branch,
            "tip": tip,
            "dirty": dirty_n,
            "registered_utc": _now(),
            "messaging": "up" if addr else "DOWN - no CLAUDE_CODE_MESSAGING_SOCKET",
        }

        reg_path = hub / REGISTRY
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            reg = {"programme": prog.get("programme", ""), "lanes": {}}
        reg.setdefault("lanes", {})
        prev = reg["lanes"].get(key) or {}
        if (key == "ORCH" and _row_is_live(prev)
                and not (session_id and prev.get("session_id") == session_id)
                and not declared_orchestrator()):
            out.append(f"[lane-registry] ORCH row in {hub.parent.name} not replaced: it is held "
                       f"by live session {prev.get('session_id') or '?'}. If THIS session is "
                       "the orchestrator, restart it with OS_LANE=orchestrator; if it is a "
                       "lane, set OS_LANE=<scope> or add its session_id to its lane's "
                       "session_ids in hub/programme.json.")
            continue
        reg["lanes"][key] = entry
        reg["updated_utc"] = _now()
        try:
            reg_path.write_text(json.dumps(reg, indent=1), encoding="utf-8")
        except Exception:
            continue

        moved = prev.get("address") and prev["address"] != addr
        out.append(
            f"[lane-registry] registered {key} ({role}) in {hub.parent.name} -> {reg_path.name}")
        if moved:
            out.append("[lane-registry] your address CHANGED since the last registration - "
                       "peers holding the old one cannot reach you. It is now published.")

        peers = [v for k, v in reg["lanes"].items() if k != key and v.get("address")]
        if peers:
            out.append("[lane-registry] peers on file: " + ", ".join(
                f"{p['key']}({p.get('branch') or 'no-branch'})" for p in peers))
            out.append("[lane-registry] ⛔ a registry row is a CACHE. Before you act on one, "
                       "confirm the peer is live: `ListAgents` shows it `interactive`, or it "
                       "answered you. A send to a dead-but-named row returns success and is "
                       "silently discarded.")

    if out:
        sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open, always
