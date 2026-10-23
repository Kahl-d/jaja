#!/usr/bin/env python3
"""
lane_write_guard.py - PreToolUse on Write|Edit|MultiEdit.

Enforces the ONE MECHANICAL RULE of lane orchestration: one author per file.

    Several sessions write the hub at once. Two sessions appending to one file
    LOSE EACH OTHER'S WRITES. This is a real failure mode: if the orchestrator
    appends to a lane's file while that lane is mid-write on it, one of the two
    posts is lost.

Claim nothing you do not enforce: the hub contract states this rule, and this
hook is what makes it true.

  BLOCKS  a lane writing another lane's file, a brief, an ORDERS file,
          ORCHESTRATOR.md, SIGNAL.md, LANES-LIVE.md, the tracker or the classifier
  BLOCKS  the orchestrator writing hub/lanes/<id>.md   (the collision described above)
  ALLOWS  everything outside a hub, and everything when identity is UNKNOWN
          (fail-open: an unknown role must not stop legitimate work)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import hub_for_path, load_programme, resolve_role  # noqa: E402

# Files only the orchestrator may write, relative to hub/
ORCH_ONLY = {
    "README.md", "ORCHESTRATOR.md", "SIGNAL.md", "LANES-LIVE.md",
    "RESOURCES.md", "MERGE-TRAIN.md", "RESEARCH-MAP.md", "programme.json",
}
# Files only the orchestrator may write, relative to the hub's PARENT
ORCH_ONLY_PARENT = {"TRACKER.md", "DIVISION.md", "classify.py"}


def _deny(msg: str) -> int:
    sys.stderr.write(msg)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    ti = payload.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path") or ""
    if not raw:
        return 0

    try:
        target = Path(raw).resolve()
    except Exception:
        return 0

    hub = hub_for_path(target)
    if hub is None:
        return 0  # not hub work - the common case, and it costs nothing

    prog = load_programme(hub)
    try:
        cwd = Path(payload.get("cwd") or ".").resolve()
    except Exception:
        cwd = Path(".").resolve()
    role, scope = resolve_role(cwd, prog, payload.get("session_id"))

    if role == "unknown":
        sys.stderr.write(
            "lane_write_guard: NOTE - writing inside a programme hub, but this session's role "
            "could not be derived from its branch.\n"
            "  One author per file is still binding: hub/lanes/<id>.md belongs to that lane "
            "ALONE; everything else in hub/ belongs to the orchestrator.\n"
            "  Set OS_LANE=<scope> (or OS_LANE=orchestrator), or add this session's id to its "
            "lane's session_ids in hub/programme.json, so this can be enforced rather than "
            "asserted.\n")
        return 0  # fail-open

    prefix = "lane_write_guard: BLOCKED - one author per file (lane-orchestration SKILL.md).\n"
    try:
        rel = target.relative_to(hub)
    except ValueError:
        rel = None

    # --- inside hub/ ---------------------------------------------------------
    if rel is not None:
        parts = rel.parts
        name = parts[-1]

        # hub/lanes/<id>.md  and  hub/lanes/<L>/sub/<S>.md
        if len(parts) >= 2 and parts[0] == "lanes" and not name.endswith("-ORDERS.md"):
            owner = Path(name).stem
            if len(parts) >= 4 and parts[2] == "sub":
                owner = Path(parts[3]).stem
            if owner.startswith("_"):
                return 0  # a template

            if role == "orchestrator":
                return _deny(
                    prefix +
                    f"  {rel.as_posix()} is lane '{owner}'s file. THE ORCHESTRATOR NEVER WRITES IT.\n"
                    f"  This is the collision to avoid: orchestrator content "
                    f"appended while the lane is mid-write, and the lane loses the post.\n"
                    f"  -> put it in hub/briefs/{owner}.md or hub/lanes/{owner}-ORDERS.md instead.\n")

            if scope and owner.lower() != str(scope).lower():
                return _deny(
                    prefix +
                    f"  You are lane '{scope}'. {rel.as_posix()} belongs to lane '{owner}'.\n"
                    f"  A lane never writes another lane's file, and never FIXES a defect it "
                    f"finds in another scope.\n"
                    f"  -> post CROSS-LANE in hub/lanes/{scope}.md with the evidence; the "
                    f"orchestrator routes it by mechanism.\n")
            return 0

        # orchestrator-only surfaces
        if role != "orchestrator" and (name in ORCH_ONLY or parts[0] == "briefs"
                                       or (name.endswith("-ORDERS.md") and str(scope).upper() != "RECRUITER")):
            return _deny(
                prefix +
                f"  {rel.as_posix()} is the ORCHESTRATOR's file. You are lane '{scope}'.\n"
                f"  -> hand over ROWS: write them in hub/lanes/{scope}.md and let the "
                f"orchestrator merge them.\n")
        return 0

    # --- the hub's parent: shared ledgers ------------------------------------
    try:
        prel = target.relative_to(hub.parent)
    except ValueError:
        return 0
    if role != "orchestrator" and prel.name in ORCH_ONLY_PARENT and len(prel.parts) == 1:
        return _deny(
            prefix +
            f"  {prel.as_posix()} is a shared ledger - the orchestrator only. You are lane "
            f"'{scope}'.\n"
            f"  -> hand over ROWS in hub/lanes/{scope}.md. A shared ledger with two authors "
            f"loses one of them silently.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open, always
