"""Lane orchestration hooks, driven the way the harness drives them: JSON on stdin,
exit code and files out.

Each test points the real hooks at a throwaway data folder (JOB_AGENCY_DATA) with one
programme hub at <data>/hub, on a git repo whose branch is the programme's shipping
branch, `main`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[3] / "hooks"

ORCH_SID = "session-01-1b0f"
TASK_SID = "session-02-64d1"   # a scheduled task
RESUME_SID = "session-03-7964"  # a Desktop-launched lane, no OS_LANE


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


@pytest.fixture()
def ws(tmp_path: Path):
    root = tmp_path / "data"
    hub = root / "hub"
    (hub / "lanes").mkdir(parents=True)
    prog = {
        "programme": "Test Programme",
        "shipping_branch": "main",
        "lanes": [
            {"scope": "RESEARCH", "branch": None, "parent": None},
            {"scope": "RESUME", "branch": None, "parent": None, "session_ids": [RESUME_SID]},
        ],
    }
    (hub / "programme.json").write_text(json.dumps(prog), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "init")
    live_sock = tmp_path / "orch.sock"
    live_sock.write_text("", encoding="utf-8")  # stands in for the orchestrator's live socket
    return {"root": root, "hooks": HOOKS, "hub": hub, "sock": live_sock, "tmp": tmp_path}


def run(ws, script: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items()
             if k not in {"OS_LANE", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_PROJECT_DIR"}}
    scratch = ws["tmp"] / "scratch"                   # the once-per-session markers live in the temp dir: keep them per test
    scratch.mkdir(exist_ok=True)
    clean.update({"JOB_AGENCY_DATA": str(ws["root"]), "JOB_AGENCY_RUNTIME": str(ws["tmp"] / "runtime"),
                  "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(scratch)})
    clean.update(env or {})
    return subprocess.run([sys.executable, str(ws["hooks"] / script)], input=json.dumps(payload),
                          capture_output=True, text=True, env=clean, cwd=str(ws["root"]), timeout=30)


def registry(ws) -> dict:
    return json.loads((ws["hub"] / "registry.json").read_text(encoding="utf-8"))


def seed_orch(ws, address: str | None = None) -> dict:
    row = {"key": "ORCH", "role": "orchestrator", "scope": None,
           "address": address if address is not None else f"uds:{ws['sock']}",
           "session_id": ORCH_SID, "branch": "main", "messaging": "up"}
    (ws["hub"] / "registry.json").write_text(
        json.dumps({"programme": "Test Programme", "lanes": {"ORCH": row}}), encoding="utf-8")
    return row


def start(ws, sid: str) -> dict:
    return {"session_id": sid, "cwd": str(ws["root"]), "hook_event_name": "SessionStart"}


# --- lane_register: the ORCH row --------------------------------------------------------

def test_scheduled_task_without_socket_does_not_overwrite_orch(ws):
    """A scheduled task (no OS_LANE, no socket) once replaced the live ORCH row with address "" and messaging DOWN."""
    before = seed_orch(ws)
    r = run(ws, "lane_register.py", start(ws, TASK_SID))
    assert r.returncode == 0
    assert registry(ws)["lanes"]["ORCH"] == before


def test_socketless_session_never_registers(ws):
    run(ws, "lane_register.py", start(ws, TASK_SID))
    assert not (ws["hub"] / "registry.json").exists()


def test_other_session_with_socket_does_not_replace_live_orch(ws):
    """A Desktop-launched lane once overwrote ORCH with its own socket."""
    before = seed_orch(ws)
    r = run(ws, "lane_register.py", start(ws, TASK_SID),
            {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/7964.sock"})
    assert r.returncode == 0
    assert registry(ws)["lanes"]["ORCH"] == before
    assert "not replaced" in r.stdout


def test_same_session_refreshes_its_own_orch_row(ws):
    seed_orch(ws)
    run(ws, "lane_register.py", start(ws, ORCH_SID),
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/2000.sock"})
    row = registry(ws)["lanes"]["ORCH"]
    assert row["address"] == "uds:/tmp/cc-socks/2000.sock"
    assert row["session_id"] == ORCH_SID


def test_payload_without_session_id_does_not_replace_live_orch(ws):
    seed_orch(ws)
    reg = registry(ws)
    reg["lanes"]["ORCH"]["session_id"] = ""
    (ws["hub"] / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
    run(ws, "lane_register.py", {"cwd": str(ws["root"])},
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/6000.sock"})
    assert registry(ws)["lanes"]["ORCH"]["address"] == f"uds:{ws['sock']}"


def test_os_lane_orchestrator_takes_over_a_live_row(ws):
    seed_orch(ws)
    run(ws, "lane_register.py", start(ws, TASK_SID),
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/3000.sock", "OS_LANE": "orchestrator"})
    row = registry(ws)["lanes"]["ORCH"]
    assert row["session_id"] == TASK_SID
    assert row["address"] == "uds:/tmp/cc-socks/3000.sock"


def test_os_lane_orchestrator_without_socket_still_does_not_register(ws):
    before = seed_orch(ws)
    run(ws, "lane_register.py", start(ws, TASK_SID), {"OS_LANE": "orchestrator"})
    assert registry(ws)["lanes"]["ORCH"] == before


def test_dead_orch_row_is_replaced_by_a_new_session_with_a_socket(ws):
    seed_orch(ws, address=f"uds:{ws['tmp'] / 'gone.sock'}")  # socket file no longer exists
    run(ws, "lane_register.py", start(ws, TASK_SID),
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/4000.sock"})
    assert registry(ws)["lanes"]["ORCH"]["session_id"] == TASK_SID


def test_empty_orch_row_is_filled_by_a_session_with_a_socket(ws):
    run(ws, "lane_register.py", start(ws, ORCH_SID),
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/5000.sock"})
    assert registry(ws)["lanes"]["ORCH"]["address"] == "uds:/tmp/cc-socks/5000.sock"


# --- session_ids binding ------------------------------------------------------------------

def test_desktop_lane_bound_by_session_id_registers_as_its_lane(ws):
    before = seed_orch(ws)
    run(ws, "lane_register.py", start(ws, RESUME_SID),
        {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/7964.sock"})
    reg = registry(ws)["lanes"]
    assert reg["ORCH"] == before
    assert reg["RESUME"]["role"] == "lane"
    assert reg["RESUME"]["address"] == "uds:/tmp/cc-socks/7964.sock"


def guard(ws, sid: str, rel: str, env: dict | None = None) -> subprocess.CompletedProcess:
    payload = {"session_id": sid, "cwd": str(ws["root"]), "hook_event_name": "PreToolUse",
               "tool_name": "Write", "tool_input": {"file_path": str(ws["hub"] / rel)}}
    return run(ws, "lane_write_guard.py", payload, env)


def test_write_guard_lets_a_desktop_lane_write_its_own_file(ws):
    assert guard(ws, RESUME_SID, "lanes/RESUME.md").returncode == 0


def test_write_guard_blocks_a_desktop_lane_from_another_lanes_file(ws):
    assert guard(ws, RESUME_SID, "lanes/RESEARCH.md").returncode == 2


def test_write_guard_blocks_a_desktop_lane_from_orchestrator_files(ws):
    assert guard(ws, RESUME_SID, "SIGNAL.md").returncode == 2


def test_write_guard_still_blocks_the_orchestrator_from_lane_files(ws):
    assert guard(ws, ORCH_SID, "lanes/RESUME.md").returncode == 2


def test_write_guard_os_lane_still_wins_over_session_binding(ws):
    assert guard(ws, RESUME_SID, "lanes/RESUME.md", {"OS_LANE": "RESEARCH"}).returncode == 2


def test_write_guard_lets_the_recruiter_write_lane_orders(ws):
    """The recruiter (the head) writes the lanes' ORDERS files; any other lane still may not."""
    assert guard(ws, RESUME_SID, "lanes/HUNT-ORDERS.md", {"OS_LANE": "RECRUITER"}).returncode == 0


def test_write_guard_still_blocks_other_lanes_from_orders_files(ws):
    assert guard(ws, RESUME_SID, "lanes/HUNT-ORDERS.md", {"OS_LANE": "HUNT"}).returncode == 2


def test_lane_context_names_the_session_bound_lane(ws):
    r = run(ws, "lane_context.py", start(ws, RESUME_SID), {"CLAUDE_PROJECT_DIR": str(ws["root"])})
    assert r.returncode == 0
    assert "LANE 'RESUME'" in r.stdout
    assert "ORCHESTRATOR" not in r.stdout.split("\n")[0]


# --- outside the agency, and the plugin-scoped agent name ----------------------------------------------------

def test_lane_context_is_silent_for_a_session_outside_every_agency_folder(ws):
    elsewhere = ws["tmp"] / "somewhere-else"
    elsewhere.mkdir()
    payload = {"session_id": RESUME_SID, "cwd": str(elsewhere), "hook_event_name": "SessionStart"}
    r = run(ws, "lane_context.py", payload, {"CLAUDE_PROJECT_DIR": str(elsewhere)})
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    r = run(ws, "lane_register.py", payload, {"CLAUDE_CODE_MESSAGING_SOCKET": "uds:/tmp/cc-socks/1.sock"})
    assert (r.returncode, r.stdout) == (0, "") and not (ws["hub"] / "registry.json").exists()


def test_lane_context_with_no_programme_prints_nothing(ws):
    (ws["hub"] / "programme.json").unlink()
    r = run(ws, "lane_context.py", start(ws, RESUME_SID), {"CLAUDE_PROJECT_DIR": str(ws["root"])})
    assert (r.returncode, r.stdout) == (0, "")


def test_a_plugin_scoped_lane_agent_is_a_lane(ws):
    payload = {"session_id": "e", "cwd": str(ws["root"]), "hook_event_name": "SessionStart",
               "agent_type": "job-agency:lane-resume"}
    (ws["hub"] / "rulings.yaml").write_text(
        "rulings:\n- {id: R-1, date: '%s', status: standing, reading: resume order, scope: {lanes: [resume]}}\n"
        % __import__("datetime").date.today().isoformat(), encoding="utf-8")
    r = run(ws, "lane_rules.py", payload, {"CLAUDE_PROJECT_DIR": str(ws["root"])})
    assert r.returncode == 0 and "R-1" in r.stdout and "lane resume" in r.stdout
