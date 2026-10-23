"""lane_handover.py, the Stop hook: a lane turn that changed files in its scope ends with a handoff block in its lane
file, written by the hook itself when the lane wrote none. Driven like the harness drives it: JSON on stdin, decision
on stdout. The lane's scope globs come from the real lanes/resume/LANE.yaml; the data folder is a throwaway git repo
(JAJA_DATA). Fail-open everywhere else."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[3] / "hooks"
SID = "session-04-5ad0"
SCOPE_TYPES = "policy:\n  lane_scope_types:\n    CRAFT: resume\n"      # a programme lane whose scope runs the resume bundle


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "data"
    js = root                                   # the data folder holds hub/ and the records
    (js / "config").mkdir(parents=True)
    (js / "config" / "lanes.yaml").write_text(SCOPE_TYPES)
    (js / "hub" / "lanes").mkdir(parents=True)
    (js / "hub" / "lanes" / "RESUME.md").write_text("# RESUME lane\n\n## Identity\n")
    (js / "resumes" / "variants").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    return {"root": root, "js": js, "tmp": tmpdir, "transcript": transcript, "hooks": HOOKS}


def start_session(ws):
    ws["transcript"].write_text("{}\n")          # the session starts: its transcript is born now
    time.sleep(0.02)


def stop(ws, agent="lane-resume", **extra):
    payload = {"session_id": SID, "agent_type": agent, "transcript_path": str(ws["transcript"]),
               "hook_event_name": "Stop", "stop_hook_active": False, **extra}
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_PROJECT_DIR", "OS_LANE"}}
    env.update(TMPDIR=str(ws["tmp"]), JAJA_DATA=str(ws["root"]), JAJA_RUNTIME=str(ws["tmp"] / "runtime"),
               PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(ws["hooks"] / "lane_handover.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, cwd=str(ws["root"]), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def change(ws, rel, text="x\n"):
    p = ws["js"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    time.sleep(0.02)


def handoff(ws, heading="## Handoff"):
    p = ws["js"] / "hub" / "lanes" / "RESUME.md"
    p.write_text(p.read_text() + f"\n{heading}\n- files: ...\n")
    time.sleep(0.02)


def test_a_non_lane_session_is_never_blocked(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, agent="claude") is None
    assert stop(ws, agent=None) is None


def lane_text(ws):
    return (ws["js"] / "hub" / "lanes" / "RESUME.md").read_text()


def test_a_scoped_change_without_a_handoff_gets_an_automatic_one(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws) is None                                    # never blocks
    text = lane_text(ws)
    assert "HANDOFF (auto, lane_handover.py)" in text and "`resumes/variants/acme/resume.yaml` sha256 " in text


@pytest.mark.parametrize("heading", ["## Handoff", "### 2026-10-01T20:00Z · HANDOFF · to: Recruiter"])
def test_a_hand_written_handoff_suppresses_the_automatic_one(ws, heading):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    handoff(ws, heading)
    assert stop(ws) is None
    assert "HANDOFF (auto" not in lane_text(ws)


def test_each_turn_needs_its_own_handoff(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    handoff(ws)
    assert stop(ws) is None                                   # turn 1 handed off by hand
    change(ws, "resumes/variants/acme/resume.yaml", "y\n")    # turn 2 changes the page again
    assert stop(ws) is None
    assert lane_text(ws).count("HANDOFF (auto") == 1           # turn 2 handed off by the hook
    assert stop(ws) is None and lane_text(ws).count("HANDOFF (auto") == 1   # nothing new: no block


def test_changes_from_before_the_session_do_not_count(ws):
    change(ws, "resumes/variants/old/resume.yaml")            # dirty before this session started
    start_session(ws)
    assert stop(ws) is None


def test_changes_outside_the_scope_do_not_count(ws):
    start_session(ws)
    change(ws, "profile/notes.md")
    assert stop(ws) is None


def test_stop_hook_active_never_blocks_twice(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, stop_hook_active=True) is None


def test_fail_open_on_a_bad_payload_or_a_missing_lane_bundle(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, agent="lane-nosuchlane") is None
    env = dict(os.environ, TMPDIR=str(ws["tmp"]), JAJA_DATA=str(ws["root"]))
    r = subprocess.run([sys.executable, str(ws["hooks"] / "lane_handover.py")], input="not json",
                       capture_output=True, text=True, env=env, cwd=str(ws["root"]), timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_the_hook_is_registered_under_stop():
    data = json.loads((HOOKS / "hooks.json").read_text())
    cmds = [h["command"] for group in data["hooks"].get("Stop", []) for h in group["hooks"]]
    assert any("lane_handover.py" in c for c in cmds)


def bind(ws, scope, lane_file_name):
    """A Desktop-app lane: no --agent, bound by session id in hub/programme.json."""
    (ws["js"] / "hub" / "programme.json").write_text(json.dumps(
        {"lanes": [{"scope": scope, "session_ids": ["local_x", SID]}]}))
    (ws["js"] / "hub" / "lanes" / lane_file_name).write_text(f"# {scope} lane\n\n## Identity\n")


def test_a_desktop_lane_bound_by_session_id_gets_its_handoff_in_its_own_file(ws):
    bind(ws, "CRAFT", "CRAFT.md")
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, agent=None) is None
    assert "HANDOFF (auto, lane_handover.py)" in (ws["js"] / "hub" / "lanes" / "CRAFT.md").read_text()
    assert "HANDOFF" not in lane_text(ws)                    # never the lane type's own file


def test_an_unbound_session_without_an_agent_is_left_alone(ws):
    bind(ws, "CRAFT", "CRAFT.md")
    payload_sid_other = "0ther000-0000-4000-8000-000000000000"
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, agent=None, session_id=payload_sid_other) is None
    assert "HANDOFF" not in (ws["js"] / "hub" / "lanes" / "CRAFT.md").read_text()


def test_a_plugin_scoped_lane_agent_name_is_a_lane(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    assert stop(ws, agent="jaja:lane-resume") is None
    assert "HANDOFF (auto, lane_handover.py)" in lane_text(ws)


def test_a_session_outside_a_lane_and_a_programme_writes_nothing(ws):
    start_session(ws)
    change(ws, "resumes/variants/acme/resume.yaml")
    before = lane_text(ws)
    assert stop(ws, agent="fit-scorer") is None and lane_text(ws) == before
