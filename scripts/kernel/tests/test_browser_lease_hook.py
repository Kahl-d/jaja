"""browser_lease.py (R-332, R-333): each lane drives its own Chrome window; text never lands in another
lane's tab. Driven the way the harness drives it: JSON on stdin, a deny decision on stdout, the lease file out."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[3] / "hooks"
A = "session-05-aaaa"   # the recruiter lane
B = "session-06-bbbb"   # the focused lane
P = "mcp__claude-in-chrome__"


@pytest.fixture()
def ws(tmp_path: Path):
    hub = tmp_path / "data" / "hub"
    hub.mkdir(parents=True)
    (hub / "programme.json").write_text(json.dumps({"lanes": [
        {"scope": "RECRUITER", "session_ids": ["local_" + A]},
        {"scope": "FOCUSED", "session_ids": [B]}]}), encoding="utf-8")
    return tmp_path / "data"


def run(ws, payload, ttl="90", cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "OS_LANE"}
    env.update(OS_BROWSER_LEASE_TTL=ttl, OS_BROWSER_LEASE_DIR=str(ws / "lease"), JOB_AGENCY_DATA=str(ws),
               JOB_AGENCY_RUNTIME=str(ws / "runtime"), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(HOOKS / "browser_lease.py")], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=30, cwd=str(cwd or ws))
    assert r.returncode == 0
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout.strip() else None


def call(ws, sid, tool=None, ttl="90", **ti):
    payload = {"session_id": sid, "hook_event_name": "Stop" if tool is None else "PreToolUse"}
    if tool is not None:
        payload.update(tool_name=P + tool, tool_input=ti)
    return run(ws, payload, ttl)


def db(ws):
    return json.loads((ws / "lease/browser-lease.json").read_text())


def set_mode(ws, m):
    (ws / "lease").mkdir(parents=True, exist_ok=True)
    (ws / "lease/browser-mode").write_text(m)


def test_reads_never_need_the_lease(ws):
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None
    for tool in ("get_page_text", "read_page", "find", "tabs_context_mcp"):
        assert call(ws, B, tool, tabId=2) is None


def test_first_input_must_bring_own_tab_forward(ws):
    reason = call(ws, A, "computer", action="type", text="LangGraph", tabId=1)
    assert reason and "screenshot" in reason
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None
    assert call(ws, A, "computer", action="type", text="LangGraph", tabId=1) is None
    assert db(ws)["focus"][A]["tab"] == 1


def test_shared_mode_blocks_another_session_while_held(ws):
    call(ws, A, "computer", action="screenshot", tabId=1)
    reason = call(ws, B, "computer", action="left_click", tabId=2)
    assert reason and "RECRUITER" in reason and "Wait" in reason
    assert call(ws, B, "form_input", ref="r1", value="x", tabId=2)


def test_shared_mode_idle_lease_passes_and_new_holder_must_focus(ws):
    call(ws, A, "computer", action="screenshot", tabId=1, ttl="0")
    reason = call(ws, B, "computer", action="type", text="x", tabId=2, ttl="0")
    assert reason and "screenshot" in reason
    assert call(ws, B, "computer", action="screenshot", tabId=2, ttl="0") is None
    assert call(ws, B, "computer", action="type", text="x", tabId=2, ttl="0") is None
    reason = call(ws, A, "computer", action="type", text="y", tabId=1, ttl="0")
    assert reason and "screenshot" in reason          # B touched Chrome since A focused


def test_windows_mode_lets_lanes_work_in_parallel(ws):
    set_mode(ws, "windows")
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None
    assert call(ws, B, "computer", action="screenshot", tabId=2) is None   # not blocked by A
    assert call(ws, B, "computer", action="left_click", tabId=2) is None
    assert call(ws, A, "computer", action="left_click", tabId=1) is None   # own window: clicks need no refocus
    reason = call(ws, A, "computer", action="type", text="x", tabId=1)
    assert reason and "screenshot" in reason          # keystrokes: B touched Chrome since A focused
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None
    assert call(ws, A, "computer", action="type", text="x", tabId=1) is None
    call(ws, A)                                       # A's turn ends
    reason = call(ws, A, "computer", action="type", text="x", tabId=1)
    assert reason and "screenshot" in reason          # a new turn focuses again


def test_stop_releases_the_holder(ws):
    call(ws, A, "computer", action="screenshot", tabId=1)
    call(ws, B)
    assert db(ws)["browsers"]["default"]["holder"] == A
    call(ws, A)
    assert db(ws)["browsers"]["default"]["holder"] is None
    assert call(ws, B, "computer", action="screenshot", tabId=2) is None


def test_bound_lane_must_select_its_own_browser(ws):
    hub = ws / "hub/programme.json"
    prog = json.loads(hub.read_text())
    prog["lanes"][1]["browser"] = "dev-focused"
    hub.write_text(json.dumps(prog))
    reason = call(ws, B, "computer", action="screenshot", tabId=2)
    assert reason and "select_browser" in reason and "dev-focused" in reason
    assert call(ws, B, "select_browser", deviceId="dev-recruiter")      # another lane's Chrome: blocked
    assert call(ws, B, "select_browser", deviceId="dev-focused") is None
    assert call(ws, B, "computer", action="screenshot", tabId=2) is None
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None  # A on default: no wait


def test_non_chrome_tools_are_ignored(ws):
    assert run(ws, {"session_id": A, "hook_event_name": "PreToolUse",
                    "tool_name": "Bash", "tool_input": {"command": "ls"}}) is None
    assert not (ws / "lease/browser-lease.json").exists()


def test_windows_mode_keystrokes_to_another_own_tab_need_its_focus(ws):
    set_mode(ws, "windows")
    assert call(ws, A, "computer", action="screenshot", tabId=1) is None
    assert call(ws, A, "computer", action="type", text="x", tabId=1) is None
    reason = call(ws, A, "computer", action="type", text="y", tabId=3)   # a second tab of A's own window
    assert reason and "screenshot" in reason
    assert call(ws, A, "computer", action="screenshot", tabId=3) is None
    assert call(ws, A, "computer", action="type", text="y", tabId=3) is None


def test_a_session_outside_every_agency_folder_is_never_leased(ws, tmp_path):
    elsewhere = tmp_path / "other-project"
    elsewhere.mkdir()
    payload = {"session_id": "session-07-cccc", "hook_event_name": "PreToolUse",
               "tool_name": P + "computer", "tool_input": {"action": "type", "text": "x", "tabId": 1}}
    assert run(ws, payload, cwd=elsewhere) is None                         # no focus demanded, nothing recorded
    assert not (ws / "lease" / "browser-lease.json").exists()
