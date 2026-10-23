#!/usr/bin/env python3
"""context_budget.py - measure the context every session is forced to load before it does any work.

Every token the forced load costs is a token not spent on applications. This meter is the before/after number for every
efficiency change. Deterministic, read-only, no network, no model.

For the root session (started in the data folder), for each lane (lanes/<type>/ in this repo) and for one subagent
spawn, it sums:
  chain   the CLAUDE.md / AGENTS.md instruction chain the engine loads from the data folder (with @imports) and the
          lane's agent card body
  hooks   what the SessionStart (or SubagentStart) hooks print, measured by running them with a throwaway session id
          (lane_register.py is never run: it writes hub/registry.json; its one line is counted as 300 bytes)
  reads   the files the lane's initial_prompt tells it to read at start (paths found in the prompt that exist in the
          data folder); its own hub log and ORDERS count only while the prompt still rewrites the Identity block in place
Claude Code caps one SessionStart hook's output at 10,000 characters; longer output is saved to a file and only a
2,000-character preview reaches the model (code.claude.com/docs/en/hooks). `hooks` counts what reaches the model under
that cap; `hooks_raw` (JSON) is what the hooks printed. Tokens are estimated as bytes / 4. Budgets are flags, never
failures, unless --strict.

Usage:
  python3 scripts/kernel/context_budget.py                 # table
  python3 scripts/kernel/context_budget.py --json          # machine output
  python3 scripts/kernel/context_budget.py --strict        # exit 1 when a session is over its start budget
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_paths as _ap  # noqa: E402

ROOT = _ap.CODE_ROOT
DATA = _ap.DATA_ROOT
HOOKS = ROOT / "hooks"
# Start budgets (tokens): what a session may load before its first useful action.
BUDGET = {"root": 6_000, "lane": 12_000, "subagent": 4_000}
HOOK_CAP, HOOK_PREVIEW = 10_000, 2_000 + 200  # preview plus the "saved to <file>" note
PATH_RE = re.compile(r"(?<![\w/.-])((?:hub|state|playbooks|profile|research|resumes|targets|lanes)/[\w./<>*-]+\.(?:md|ya?ml|json)"
                     r"|AGENCY-CHARTER\.md|AGENTS\.md)")


def size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def expand_imports(p: Path, seen: set[Path] | None = None, depth: int = 0) -> int:
    """Bytes of a CLAUDE.md-style file with its @imports expanded (the engine expands up to 4 hops)."""
    seen = seen if seen is not None else set()
    p = p.resolve()
    if p in seen or not p.is_file() or depth > 4:
        return 0
    seen.add(p)
    text = p.read_text(errors="replace")
    total = len(text.encode())
    for m in re.finditer(r"(?m)^\s*@(\S+)", text):
        total += expand_imports((p.parent / m.group(1)), seen, depth + 1)
    return total


def root_chain() -> int:
    """The instruction chain a session in the data folder loads: its CLAUDE.md (else AGENTS.md) with imports."""
    return expand_imports(DATA / "CLAUDE.md") or expand_imports(DATA / "AGENTS.md")


RAW: list[int] = []  # raw bytes of every hook run, for hooks_raw


def run_hook(cmd: list[str], payload: dict, env_extra: dict, cwd: Path) -> int:
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), CLAUDE_PROJECT_DIR=str(cwd), **env_extra)
    try:
        out = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, cwd=cwd, env=env,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    RAW.append(len(out.encode()))
    return RAW[-1] if len(out) <= HOOK_CAP else HOOK_PREVIEW


def frontmatter_prompt(card: Path) -> tuple[str, str]:
    """(initial_prompt text, body) of a lane AGENT.md."""
    text = card.read_text(errors="replace") if card.is_file() else ""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return "", text
    fm, body = m.group(1), m.group(2)
    pm = re.search(r"initial_prompt:\s*\|\n((?:[ \t]+.*\n?)*)", fm)
    return (pm.group(1) if pm else ""), body


def lane_rows() -> list[dict]:
    rows = []
    sid = f"budget-{uuid.uuid4().hex[:12]}"
    chain0 = root_chain()
    lanes_dir = ROOT / "lanes"
    for d in sorted(p for p in lanes_dir.iterdir() if p.is_dir() and (p / "AGENT.md").is_file()) if lanes_dir.is_dir() else []:
        lane = d.name
        prompt, body = frontmatter_prompt(d / "AGENT.md")
        chain = chain0 + len(body.encode())
        payload = {"session_id": f"{sid}-{lane}", "cwd": str(DATA), "hook_event_name": "SessionStart", "source": "startup",
                   "agent_type": f"lane-{lane}"}
        env = {"OS_LANE": lane}
        RAW.clear()
        hooks = (run_hook([sys.executable, str(HOOKS / "lane_context.py")], payload, env, DATA)
                 + run_hook([sys.executable, str(HOOKS / "lane_rules.py")], payload, env, DATA)
                 + 300)  # lane_register.py's line, not run
        # a lane's own log and its ORDERS are append-only and grepped (their live end is in <LANE>-STATE.md, which
        # the prompt names); count them only when the prompt still says to read them whole
        names = {m.group(1) for m in PATH_RE.finditer(prompt)}
        whole_log = re.search(r"Rewrite the\s+`## Identity` block", prompt) is not None
        names = sorted(n for n in names if whole_log or not re.fullmatch(r"hub/lanes/[\w-]+?(-ORDERS)?\.md", n)
                       or n.endswith("-STATE.md"))
        if whole_log:
            names = sorted(set(names) | {f"hub/lanes/{lane.upper()}.md"})
        found = [(n, size(DATA / n)) for n in names if "<" not in n and "*" not in n and size(DATA / n)]
        for m in re.finditer(r"python3 scripts/charter\.py ([\d. ]+)`", prompt):   # charter sections, printed
            charter = ROOT / "scripts" / "charter.py"
            if charter.is_file():
                out = subprocess.run([sys.executable, str(charter), *m.group(1).split()],
                                     capture_output=True, text=True, cwd=DATA).stdout
                found.append((f"charter.py {m.group(1).strip()}", len(out.encode())))
        reads = sum(b for _, b in found)
        total = chain + hooks + reads
        rows.append({"session": f"lane:{lane}", "chain": chain, "hooks": hooks, "hooks_raw": sum(RAW) + 300, "reads": reads, "total": total,
                     "tokens": total // 4, "budget": BUDGET["lane"], "over": total // 4 > BUDGET["lane"],
                     "read_files": [{"path": n, "bytes": b} for n, b in sorted(found, key=lambda x: -x[1])]})
    return rows


def root_row() -> dict:
    payload = {"session_id": f"budget-root-{uuid.uuid4().hex[:8]}", "cwd": str(DATA), "hook_event_name": "SessionStart",
               "source": "startup"}
    chain = root_chain()
    RAW.clear()
    hooks = run_hook([sys.executable, str(HOOKS / "lane_context.py")], payload, {}, DATA)
    total = chain + hooks
    return {"session": "root", "chain": chain, "hooks": hooks, "hooks_raw": sum(RAW), "reads": 0, "total": total, "tokens": total // 4,
            "budget": BUDGET["root"], "over": total // 4 > BUDGET["root"], "read_files": []}


def subagent_row(agent: str = "resume-tailor") -> dict:
    payload = {"session_id": f"budget-sub-{uuid.uuid4().hex[:8]}", "agent_id": uuid.uuid4().hex[:8],
               "agent_type": agent, "cwd": str(DATA), "hook_event_name": "SubagentStart"}
    card = ROOT / ".claude" / "agents" / f"{agent}.md"
    chain = root_chain() + size(card)
    RAW.clear()
    hooks = run_hook([sys.executable, str(HOOKS / "role_rules.py")], payload, {}, DATA)
    total = chain + hooks
    return {"session": f"subagent:{agent}", "chain": chain, "hooks": hooks, "hooks_raw": sum(RAW), "reads": 0, "total": total,
            "tokens": total // 4, "budget": BUDGET["subagent"], "over": total // 4 > BUDGET["subagent"],
            "read_files": []}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any session is over its start budget")
    ap.add_argument("--subagent", default="resume-tailor")
    a = ap.parse_args(argv)
    rows = [root_row(), *lane_rows(), subagent_row(a.subagent)]
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        print(f"{'session':22} {'chain':>8} {'hooks':>8} {'reads':>9} {'~tokens':>8} {'budget':>7}")
        for r in rows:
            flag = "  OVER" if r["over"] else ""
            print(f"{r['session']:22} {r['chain']:>8} {r['hooks']:>8} {r['reads']:>9} {r['tokens']:>8} {r['budget']:>7}{flag}")
            for f in r["read_files"][:4]:
                print(f"{'':24}{f['bytes']:>9}  {f['path']}")
        print("bytes; ~tokens = bytes/4; budgets are the BUDGET constant in this file")
    return 1 if a.strict and any(r["over"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
