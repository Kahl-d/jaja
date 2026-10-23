#!/usr/bin/env python3
"""
lane_rules.py - SessionStart hook. Prints the rulings digest for this lane type: pinned principles, the lane's
standing rulings from the last 14 days (one line each), and a count of the rest with the command that shows them
(rulings_common.digest).

The lane type comes from the hook payload's `agent_type` (`claude --agent lane-resume`, or the
`agent` key of .claude/lane-settings/<type>.json), or from OS_LANE, or from the programme binding
(lane_common.resolve_role). A session that is none of these gets nothing: a ruling binds what it
names.

Output is plain text on stdout (Claude Code adds SessionStart stdout to the context).
Fail-open: any exception exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import WORKSPACE, find_hubs, force_utf8_stdout, in_agency_session, load_programme, resolve_role  # noqa: E402
from rulings_common import digest, first_time, index_rows, lane_type_from_agent, registries  # noqa: E402


def main() -> int:
    force_utf8_stdout()
    try:
        payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    lane = lane_type_from_agent(payload.get("agent_type")) or (os.environ.get("OS_LANE") or "").strip().lower() or None
    if lane == "orchestrator":
        lane = "orch"

    if lane is None:
        # a Desktop lane bound by session id in programme.json (only inside the agency's own folders)
        try:
            cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        except Exception:
            cwd = WORKSPACE
        if not in_agency_session(cwd, payload):
            return 0
        sid = payload.get("session_id")
        for hubjson in find_hubs():
            role, scope = resolve_role(cwd, load_programme(hubjson.parent), sid)
            if role in ("lane", "sublane") and scope:
                lane = str(scope).lower()
                break
            if role == "orchestrator":
                lane = "orch"
                break
    if lane is None:
        return 0
    src = str(payload.get("source") or "startup")
    if not first_time(f"lane-{src}", payload.get("session_id")):
        return 0

    blocks: list[str] = []
    for reg in registries():
        rel = reg.relative_to(WORKSPACE).as_posix() if reg.is_relative_to(WORKSPACE) else str(reg)
        rows = index_rows(reg)
        if rows:
            blocks.append(digest(rows, lane=lane, registry=rel))
    if blocks:
        sys.stdout.write("\n".join(blocks) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
