#!/usr/bin/env python3
"""
role_rules.py - SubagentStart hook. Prints the rulings digest for this role (rulings_common.digest).

The role is the hook payload's `agent_type` (the subagent's name: resume-editor, fit-scorer,
base-comparer ...). Built-in agents (general-purpose, Explore, Plan) and lane agents get nothing
here; lanes get theirs from lane_rules.py at SessionStart.

Output: JSON with hookSpecificOutput.additionalContext (the SubagentStart contract), so the
rulings land in the subagent's context before its first turn.
Fail-open: any exception exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import WORKSPACE, force_utf8_stdout, in_agency_session  # noqa: E402
from rulings_common import digest, first_time, index_rows, lane_type_from_agent, registries  # noqa: E402

# Built-in and helper agents search, read or score for their caller; they never act on a ruling, so they get none
# (each print was ~9.4 KB in every Explore and general-purpose spawn).
SKIP = {"general-purpose", "explore", "plan", "claude-code-guide", "statusline-setup", "claude"}


def main() -> int:
    force_utf8_stdout()
    try:
        payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    agent = str(payload.get("agent_type") or "").strip()
    if not agent or lane_type_from_agent(agent):
        return 0
    try:
        cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    except Exception:
        cwd = None
    if not in_agency_session(cwd, payload):          # a user-scope plugin fires in every folder: stay silent outside
        return 0
    role = agent.split(":")[-1].lower()  # plugin-scoped names: keep the bare role name
    if role in SKIP:
        return 0
    if not first_time("role", payload.get("agent_id")):
        return 0

    blocks: list[str] = []
    for reg in registries():
        rel = reg.relative_to(WORKSPACE).as_posix() if reg.is_relative_to(WORKSPACE) else str(reg)
        rows = index_rows(reg)
        if rows:
            blocks.append(digest(rows, role=role, registry=rel))
    if blocks:
        out = {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "\n".join(blocks)}}
        sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
