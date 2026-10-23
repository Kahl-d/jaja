"""routine_drift.py with --tasks-state: the enabled flag and the schedule live in the app, so drift there
is found only from the scheduled-tasks tool's list. A task could be on while its spec said
enabled: false, and routine_drift reported nothing. Fixtures are synthetic."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import routine_drift as rd  # noqa: E402


def _world(tmp_path):
    js, osr, tasks = tmp_path / "js", tmp_path / "os", tmp_path / "tasks"
    (js / "routines").mkdir(parents=True)
    (osr / "routines").mkdir(parents=True)
    (js / "routines" / "js-apply.yaml").write_text("name: js-apply\nenabled: false\nschedule: \"15 8 * * 1-5\"\n")
    (js / "routines" / "js-brief.yaml").write_text("name: js-brief\nenabled: true\nschedule: \"45 6 * * *\"\n")
    (osr / "routines" / "watchdog.yaml").write_text("name: watchdog\nenabled: true\nschedule: \"0 7,18 * * *\"\n")
    for name in ("js-apply", "js-brief", "os-watchdog"):
        (tasks / name).mkdir(parents=True)
        (tasks / name / "SKILL.md").write_text("do the routine\n")
    return js, osr, tasks


STATE = [{"taskId": "js-apply", "enabled": True, "cronExpression": "15 8 * * 1-5"},
         {"taskId": "js-brief", "enabled": True, "cronExpression": "45  6 * * *"},
         {"taskId": "os-watchdog", "enabled": True, "cronExpression": "0 7,18 * * *"}]


def _found(tmp_path, state):
    js, osr, tasks = _world(tmp_path)
    return {(f["task"], f["kind"]) for f in rd.check(js, osr, tasks, state)["findings"]}


def test_a_task_switched_on_while_its_spec_says_off_is_found(tmp_path):
    state = {r["taskId"]: r for r in STATE}
    assert _found(tmp_path, state) == {("js-apply", "enabled differs")}


def test_a_spec_on_and_its_task_off_is_found_too(tmp_path):
    state = {r["taskId"]: dict(r) for r in STATE}
    state["js-apply"]["enabled"] = False
    state["js-brief"]["enabled"] = False
    assert _found(tmp_path, state) == {("js-brief", "enabled differs")}


def test_a_changed_schedule_is_found_and_whitespace_is_not_a_change(tmp_path):
    state = {r["taskId"]: dict(r) for r in STATE}
    state["js-apply"]["enabled"] = False
    state["os-watchdog"]["cronExpression"] = "0 8,18 * * *"
    assert _found(tmp_path, state) == {("os-watchdog", "schedule differs")}


def test_a_task_folder_the_app_does_not_list_is_found(tmp_path):
    state = {r["taskId"]: dict(r) for r in STATE if r["taskId"] != "js-brief"}
    state["js-apply"]["enabled"] = False
    assert _found(tmp_path, state) == {("js-brief", "not in the app")}


def test_an_unlisted_folder_is_fine_when_its_spec_is_off_and_stale_when_another_task_runs_it(tmp_path):
    js, osr, tasks = _world(tmp_path)
    (tasks / "watchdog").mkdir()                     # an old folder name for the spec os-watchdog runs
    (tasks / "watchdog" / "SKILL.md").write_text("old prompt\n")
    state = {r["taskId"]: dict(r) for r in STATE if r["taskId"] != "js-apply"}   # js-apply: spec off, not in the app
    found = {(f["task"], f["kind"]) for f in rd.check(js, osr, tasks, state)["findings"]}
    assert found == {("watchdog", "stale folder")}


def test_without_a_state_file_the_report_says_the_flag_was_not_checked(tmp_path, capsys):
    js, osr, tasks = _world(tmp_path)
    res = rd.check(js, osr, tasks)
    assert res["state_checked"] is False and res["findings"] == []
    rd.main(["--root", str(js), "--os-root", str(osr), "--tasks", str(tasks)])
    assert "enabled and schedule NOT checked (pass --tasks-state)" in capsys.readouterr().out


def test_the_cli_reads_the_tools_json_list_or_its_wrapped_form(tmp_path, capsys):
    js, osr, tasks = _world(tmp_path)
    for i, payload in enumerate((STATE, {"tasks": STATE})):
        f = tmp_path / f"state{i}.json"
        f.write_text(json.dumps(payload))
        assert rd.main(["--root", str(js), "--os-root", str(osr), "--tasks", str(tasks), "--tasks-state", str(f)]) == 1
        out = capsys.readouterr().out
        assert "js-apply: enabled differs" in out and "NOT checked" not in out


def test_guard_matching_state_is_clean(tmp_path):
    state = {r["taskId"]: dict(r) for r in STATE}
    state["js-apply"]["enabled"] = False
    assert _found(tmp_path, state) == set()
