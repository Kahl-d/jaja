#!/usr/bin/env python3
"""
lane_common.py - shared identity resolution for the orchestration hooks.

THE DESIGN RULE THIS FILE EXISTS FOR:
    Derive facts about the world; declare only intent.

A session's ROLE in a programme is not declared, it is DERIVED from the branch
its worktree is on, matched against the programme's own record. A declared role
rots the moment a session is resumed on a different tree; a derived one cannot.

Fail-open everywhere. An orchestration hook that blocks legitimate work is worse
than one that misses a violation - the file-ownership rule is also written in
the hub contract, which every session reads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agency_paths as _ap  # noqa: E402

# The folder the hooks look in for the programme hub is the data folder (it holds hub/programme.json);
# the code (lanes/, skills/, scripts/) is the repo this file lives in.
WORKSPACE = _ap.DATA_ROOT
CODE_ROOT = _ap.CODE_ROOT
RUNTIME_ROOT = _ap.RUNTIME_ROOT
KERNEL_DIR = _ap.KERNEL_DIR

# Directories never walked when looking for hubs. A data folder may sit beside whole code
# repos (node_modules alone is hundreds of thousands of files); an unpruned
# `**/hub/programme.json` walk would cost every SessionStart seconds.
PRUNE_DIRS = {"node_modules", ".pnpm", "vault", "dist", "build", "__pycache__",
              ".venv", "venv", "DerivedData", "Pods"}
HUB_WALK_DEPTH = 5            # `<data>/hub/programme.json` is depth 1; a project hub is at most a few folders down


def strip_namespace(agent_type: object) -> str:
    """`job-agency:lane-hunt` -> `lane-hunt`: a plugin's agents carry the plugin name before the colon."""
    return str(agent_type or "").strip().split(":")[-1].strip()


def in_agency_session(cwd: Path | None = None, payload: dict | None = None) -> bool:
    """True when this session belongs to the agency: it declared a lane (OS_LANE), runs a `lane-*` agent, or its
    working directory is inside the data folder, the code repo or the runtime folder. A user-scope plugin fires its
    hooks in every folder; outside these the hooks must stay silent."""
    if declared_lane() or strip_namespace((payload or {}).get("agent_type")).lower().startswith("lane-"):
        return True
    try:
        here = (cwd or Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())).resolve()
    except Exception:
        return False
    for base in (WORKSPACE, CODE_ROOT, RUNTIME_ROOT):
        try:
            if base.exists() and (here == base.resolve() or base.resolve() in here.parents):
                return True
        except Exception:
            continue
    return False


def force_utf8_stdout() -> None:
    """MEASURED 2026-08-27: the harness hands hooks a cp1252 stdout on this machine.

    A single non-ASCII character then raises UnicodeEncodeError mid-print, the hook's
    fail-open except swallows it, and the hook emits ZERO BYTES and exits 0. Not a
    truncated message - a silent, total one, indistinguishable from "nothing to say".

    Red proof: lane_register.py without this -> 0 bytes. With PYTHONIOENCODING=utf-8
    -> all three lines. The doctrine markers we write are exactly the trigger.

    Call this FIRST in any hook that prints anything but plain ASCII.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass  # older/odd streams: fall through, callers still degrade gracefully


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def find_hubs() -> list[Path]:
    """Every programme hub in the data folder: any `**/hub/programme.json` (normally `<data>/hub/`). A pruned,
    depth-capped walk, not a bare glob - dot-directories and PRUNE_DIRS are skipped."""
    found: list[Path] = []
    try:
        base_depth = len(Path(WORKSPACE).parts)
        for root, dirs, files in os.walk(WORKSPACE):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in PRUNE_DIRS
                       and len(Path(root).parts) - base_depth < HUB_WALK_DEPTH]
            if os.path.basename(root) == "hub" and "programme.json" in files:
                found.append(Path(root) / "programme.json")
        return sorted(found)
    except Exception:
        return []


def hub_for_path(target: Path) -> Path | None:
    """The hub directory governing `target`, by walking up. None if not hub work."""
    try:
        p = target if target.is_dir() else target.parent
        for anc in [p, *p.parents]:
            if anc.name == "hub" and (anc / "programme.json").exists():
                return anc
            cand = anc / "hub" / "programme.json"
            if cand.exists():
                return cand.parent
    except Exception:
        pass
    return None


def load_programme(hub: Path) -> dict:
    try:
        return json.loads((hub / "programme.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def declared_lane() -> str:
    """OS_LANE (the lane's scope; `orchestrator` for the orchestrator), stripped. Empty when the session declared nothing."""
    return os.environ.get("OS_LANE", "").strip()


def declared_orchestrator() -> bool:
    return declared_lane().lower() in {"orch", "orchestrator", "main"}


def resolve_role(cwd: Path, prog: dict, session_id: str | None = None) -> tuple[str, str | None]:
    """(role, scope) for this session within `prog`.

    role is one of: 'orchestrator' | 'lane' | 'sublane' | 'unknown'

    DERIVED, in this order:
      1. OS_LANE in the environment - an explicit override for a session that
         is not on its own branch yet (a lane in its first turn, say).
      2. `session_id` (the hook payload's transcript UUID) matched against a
         lane's `session_ids` in programme.json. A lane launched from the Desktop
         app cannot set OS_LANE and shares the shipping branch, so without this
         it would resolve as the orchestrator.
      3. the branch of `cwd`, matched against the programme's own lane records.
    An unmatched branch is 'unknown', NEVER guessed: a check that cannot answer
    NO is worse than no check.
    """
    declared = declared_lane()
    if declared:
        if declared_orchestrator():
            return "orchestrator", None
        return "lane", declared

    lanes = prog.get("lanes", []) or []

    sid = (session_id or "").strip().lower()
    if sid:
        for lane in lanes:
            bound = {str(s).strip().lower() for s in (lane.get("session_ids") or [])}
            if sid in bound:
                return ("sublane" if lane.get("parent") else "lane"), lane.get("scope")

    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return "unknown", None

    if branch == prog.get("shipping_branch"):
        return "orchestrator", None

    for lane in lanes:
        if lane.get("branch") and lane["branch"] == branch:
            return ("sublane" if lane.get("parent") else "lane"), lane.get("scope")

    return "unknown", None
