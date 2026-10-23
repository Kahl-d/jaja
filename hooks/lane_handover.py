#!/usr/bin/env python3
"""
lane_handover.py - Stop hook (design §3, "files are the contract"). A lane turn that changed files in its scope ends
with a handoff section in its lane file. The hook WRITES that section itself (the files and their
sha256, machine-made) instead of blocking the stop for a model turn that wrote it by hand, and refreshes the lane's
small live state file hub/lanes/<TYPE>-STATE.md (scripts/kernel/lane_state.py) so the next start reads a few KB, not the
whole log. A lane that wants to say more (what moved, what needs whom) writes its own one-line post; a hand-written handoff still counts and suppresses
the automatic one.

Who: sessions whose payload `agent_type` is `lane-<type>` (plugin-scoped `jaja:lane-<type>` too), and desktop-app
lanes, which run without --agent: OS_LANE, or the session id bound to a lane's `session_ids` in <data>/hub/programme.json,
names the scope. Anything else: exit 0, no output.
Scope: `scope:` of lanes/<type>/LANE.yaml in the code repo (globs relative to the data folder or the repo).
Changed this turn: `git status --porcelain` paths under the data folder (and the repo) that match a scope glob and were
modified after the turn's start. The turn's start is this hook's marker for the session (the last stop it allowed), else the
session's start (the transcript file's birth time).
Handoff: the lane file hub/lanes/<TYPE>.md holds more handoff sections than at the last allowed stop: a heading
"## Handoff" or a "### ..." heading containing HANDOFF. The lane file itself is in scope and never counts as a change.
Never blocks. A stop that is the continuation of another hook's stop decision (`stop_hook_active`) is left alone.
Marker: <tempdir>/os-lane-handover/<session_id>.json = {"t": <epoch seconds>, "handoffs": <count>}, written when a
stop is allowed.
Fail-open: any error, a missing bundle, no git: exit 0 with no output.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agency_paths import CODE_ROOT, DATA_ROOT as WORKSPACE, KERNEL_DIR  # noqa: E402
HANDOFF_RE = re.compile(r"^(## Handoff\b.*|### .*\bHANDOFF\b.*)$", re.M)


def lane_type(agent_type: object) -> str | None:
    a = str(agent_type or "").strip().split(":")[-1].strip().lower()
    return a[len("lane-"):] or None if a.startswith("lane-") else None


def find_bundle(lane: str) -> Path | None:
    for p in sorted(CODE_ROOT.glob(f"lanes/{lane}/LANE.yaml")):
        return p
    return None


def scope_globs(lane_yaml: Path) -> list[str]:
    for line in lane_yaml.read_text(encoding="utf-8").splitlines():
        if line.startswith("scope:"):
            value = line.split(":", 1)[1].strip()
            try:
                globs = json.loads(value)
            except ValueError:
                import yaml  # only when the flow list is not plain JSON
                globs = yaml.safe_load(value)
            return [str(g) for g in globs or []]
    return []


def _scope_types() -> dict[str, str]:
    """{SCOPE: lane type} for a programme lane whose scope name differs from the lane type it runs, from the
    `policy.lane_scope_types` config section (empty by default)."""
    try:
        import agency_config as cfg
        raw = cfg.get("policy.lane_scope_types", {}) or {}
        return {str(k).upper(): str(v).lower() for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}



ORCH_NAMES = {"ORCH", "ORCHESTRATOR", "MAIN"}


def bound_scope(session_id: str) -> str | None:
    """The scope a session is bound to: OS_LANE, else its id in a programme's lane `session_ids` (a Desktop-app lane)."""
    declared = (os.environ.get("OS_LANE") or "").strip().upper()
    if declared:
        return None if declared in ORCH_NAMES else declared
    sid = session_id.strip().lower()
    for prog in sorted(WORKSPACE.glob("hub/programme.json")):
        try:
            lanes = json.loads(prog.read_text(encoding="utf-8")).get("lanes") or []
        except Exception:
            continue
        for lane in lanes:
            if sid and sid in {str(x).strip().lower() for x in lane.get("session_ids") or []}:
                return str(lane.get("scope") or "").strip().upper() or None
    return None


def marker_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80] or "none"
    return Path(tempfile.gettempdir()) / "os-lane-handover" / f"{safe}.json"


def turn_start(marker: Path, transcript: object) -> tuple[float | None, int | None]:
    if marker.is_file():
        data = json.loads(marker.read_text(encoding="utf-8"))
        return float(data["t"]), int(data.get("handoffs", 0))
    if transcript and Path(str(transcript)).is_file():
        st = Path(str(transcript)).stat()
        return float(getattr(st, "st_birthtime", st.st_ctime)), None
    return None, None


def changed_in_scope(dept: Path, globs: list[str], since: float, lane_file: Path) -> list[str]:
    top = subprocess.run(["git", "-C", str(dept), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         timeout=10)
    if top.returncode != 0:
        return []
    repo = Path(top.stdout.strip())
    out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--", str(dept)],
                         capture_output=True, text=True, timeout=20).stdout
    hits = []
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        full = repo / path
        try:
            rel = full.resolve().relative_to(dept.resolve()).as_posix()
        except ValueError:
            continue
        if full.resolve() == lane_file.resolve() or not full.is_file():
            continue
        if any(fnmatch.fnmatch(rel, g) for g in globs) and full.stat().st_mtime > since:
            hits.append(rel)
    return sorted(hits)


def write_marker(marker: Path, handoffs: int) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=marker.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"t": time.time(), "handoffs": handoffs}, fh)
    os.replace(tmp, marker)


def sha12(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return "unreadable"


def auto_handoff(lane_file: Path, dept: Path, changed: list[str]) -> None:
    """Append the machine-written handoff section: every changed in-scope file with its sha256."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = [f"\n### {now} · HANDOFF (auto, lane_handover.py)"]
    lines += [f"- `{rel}` sha256 {sha12(dept / rel)}" for rel in changed[:30]]
    if len(changed) > 30:
        lines.append(f"- and {len(changed) - 30} more (git status in the department)")
    lines.append("- what moved and what needs whom: the lane's last post above; reviewers re-run the gates from the files")
    with lane_file.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def refresh_state(lane: str) -> None:  # lane = the scope, upper case
    """Write hub/lanes/<TYPE>-STATE.md when scripts/kernel/lane_state.py is present (fail-open)."""
    script = KERNEL_DIR / "lane_state.py"
    if script.is_file():
        subprocess.run([sys.executable, str(script), lane, "--write", "--quiet"], capture_output=True,
                       timeout=20)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    sid = str(payload.get("session_id") or "")
    if not sid:
        return 0
    # the lane's scope names its file: a scope mapped in policy.lane_scope_types runs another lane type's bundle but
    # keeps its own hub/lanes/<SCOPE>.md
    bound = bound_scope(sid)
    lane = lane_type(payload.get("agent_type")) or (_scope_types().get(bound, bound.lower()) if bound else None)
    if not lane:
        return 0
    bundle = find_bundle(lane)
    if bundle is None:
        return 0
    dept = WORKSPACE                    # the data folder: hub/lanes/<SCOPE>.md and the records live here
    scope = bound or lane.upper()
    lane_file = dept / "hub" / "lanes" / f"{scope}.md"
    marker = marker_path(sid)
    handoffs_now = len(HANDOFF_RE.findall(lane_file.read_text(encoding="utf-8"))) if lane_file.is_file() else 0
    if payload.get("stop_hook_active"):
        write_marker(marker, handoffs_now)
        return 0
    since, handoffs_then = turn_start(marker, payload.get("transcript_path"))
    if since is None:
        write_marker(marker, handoffs_now)
        return 0
    changed = changed_in_scope(dept, scope_globs(bundle), since, lane_file) + changed_in_scope(CODE_ROOT, scope_globs(bundle), since, lane_file)
    if handoffs_then is None:          # first stop of the session: a handoff written this session counts
        handoff_new = handoffs_now > 0 and lane_file.is_file() and lane_file.stat().st_mtime > since
    else:
        handoff_new = handoffs_now > handoffs_then
    if changed and not handoff_new and lane_file.is_file():
        auto_handoff(lane_file, dept, changed)
        handoffs_now += 1
    if changed or handoff_new:
        try:
            refresh_state(scope)
        except Exception:
            pass
    write_marker(marker, handoffs_now)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
