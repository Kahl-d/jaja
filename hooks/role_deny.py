#!/usr/bin/env python3
"""
role_deny.py - PreToolUse hook on Bash. A role never runs the shell commands its card forbids.

A role card (agents/<name>.md) lists `deny_commands:` (shell patterns such as `gmail_send.py` or `x_api.py execute`);
sync renders them as `denyCommands:` in the generated agent file (.claude/agents/<name>.md). When a subagent of that
role calls Bash, this hook reads the generated agent file (else the source card) named by the hook payload's
`agent_type` (a plugin-scoped `job-agency:<name>` works too), and refuses (exit 2, reason on stderr) any command
containing one of the patterns. The main session (no agent_type, or a lane agent) is not restricted here: lanes carry
their own tools.

Why: a `Bash(pattern)` entry in disallowedTools removes the whole Bash tool, so a role carrying one could run no
script at all. This hook is the per-command deny that disallowedTools cannot express.

Fail-open on any error except a matched deny: exit 0 silently.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import CODE_ROOT, WORKSPACE, force_utf8_stdout, strip_namespace  # noqa: E402


def agent_file(agent_type: str) -> Path | None:
    name = strip_namespace(agent_type)
    if not name or "/" in name or name.startswith("."):
        return None
    roots = [Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()), WORKSPACE, CODE_ROOT]
    for root in roots:
        for p in (root / ".claude" / "agents" / f"{name}.md", root / "agents" / f"{name}.md"):
            if p.is_file():
                return p
    return None


def deny_patterns(path: Path) -> list[str]:
    """The patterns in a generated agent file (`denyCommands:` block list) or a source card (`deny_commands:` as a
    block list or an inline JSON/YAML list), read from the frontmatter only."""
    out: list[str] = []
    in_block = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    fence = 0
    for line in lines:
        if line.strip() == "---":
            fence += 1
            if fence >= 2:
                break
            continue
        key = line.split(":", 1)[0] if ":" in line and not line.startswith((" ", "-")) else ""
        if key in ("denyCommands", "deny_commands"):
            inline = line.split(":", 1)[1].strip()
            if inline.startswith("["):
                try:
                    out += [str(x) for x in json.loads(inline)]
                except ValueError:
                    out += [x.strip().strip('"').strip("'") for x in inline.strip("[]").split(",")]
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.append(stripped[2:].strip().strip('"').strip("'"))
                continue
            if not line.startswith(" ") and stripped:
                in_block = False
    return [p for p in out if p]


def main() -> int:
    force_utf8_stdout()
    try:
        payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    agent = strip_namespace(payload.get("agent_type"))
    if not agent or agent.startswith("lane-"):
        return 0
    path = agent_file(agent)
    if path is None:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    for pat in deny_patterns(path):
        if pat.lower() in command.lower():
            sys.stderr.write(f"[role_deny] the {agent} role may not run '{pat}' (card deny_commands). "
                             "Report the need in your output; the lead recruiter or the owner runs it.\n")
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
