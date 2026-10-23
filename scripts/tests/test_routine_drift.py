"""routine_drift.py (SETUP-FINAL A7): an installed task that stops running what its spec says is found, not missed.
On 2026-10-03 js-hunt-review's task dropped the spec's --fresh10 and nothing noticed."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import routine_drift as rd  # noqa: E402


def _world(tmp_path):
    js, osr, tasks = tmp_path / "js", tmp_path / "os", tmp_path / "tasks"
    (js / "routines").mkdir(parents=True)
    (osr / "routines").mkdir(parents=True)
    (js / "routines" / "js-a.yaml").write_text("name: js-a\nenabled: true\naction: \"script:x/scripts/a.py --capture --fresh10\"\n")
    (js / "routines" / "js-b.yaml").write_text("name: js-b\nenabled: true\naction: \"script:x/scripts/b.py\"\n")
    (js / "routines" / "js-off.yaml").write_text("name: js-off\nenabled: false\naction: \"script:x/scripts/o.py\"\n")
    (osr / "routines" / "sweep.yaml").write_text("name: sweep\nenabled: true\naction: \"script:scripts/kernel/sweep.py\"\n")
    for name, text in (("js-a", "1. Run: python3 x/scripts/a.py --capture\n"), ("os-sweep", "python3 scripts/kernel/sweep.py\n"),
                       ("js-stray", "do things\n")):
        (tasks / name).mkdir(parents=True)
        (tasks / name / "SKILL.md").write_text(text)
    return js, osr, tasks


def test_a_dropped_flag_a_missing_task_and_a_task_with_no_spec_are_each_found(tmp_path):
    js, osr, tasks = _world(tmp_path)
    found = {(f["task"], f["kind"]) for f in rd.check(js, osr, tasks)["findings"]}
    assert found == {("js-a", "flag dropped"), ("js-b", "missing task"), ("js-stray", "no spec")}


def test_a_task_that_runs_its_spec_is_clean_and_the_exit_code_says_so(tmp_path):
    js, osr, tasks = _world(tmp_path)
    (tasks / "js-a" / "SKILL.md").write_text("python3 x/scripts/a.py --capture --fresh10\n")
    (tasks / "js-b").mkdir()
    (tasks / "js-b" / "SKILL.md").write_text("python3 x/scripts/b.py\n")
    (tasks / "js-stray" / "SKILL.md").unlink()
    (tasks / "js-stray").rmdir()
    assert rd.check(js, osr, tasks)["findings"] == []
    assert rd.main(["--root", str(js), "--os-root", str(osr), "--tasks", str(tasks)]) == 0


def test_a_spec_run_inside_another_task_is_matched_and_a_changed_value_is_found(tmp_path):
    js, osr, tasks = _world(tmp_path)
    (osr / "routines" / "nightly.yaml").write_text("name: nightly\nenabled: true\naction: \"script:scripts/nightly_sweep.py\"\n")
    (tasks / "os-sweep" / "SKILL.md").write_text("1. python3 scripts/nightly_sweep.py\n2. python3 scripts/kernel/sweep.py\n")
    (js / "routines" / "js-s.yaml").write_text("name: js-s\nenabled: true\naction: \"script:x/scripts/p.py --stages funding,news,feeds\"\n")
    (tasks / "js-s").mkdir()
    (tasks / "js-s" / "SKILL.md").write_text("python3 x/scripts/p.py --stages funding,news\n")
    found = {(f["task"], f["kind"]) for f in rd.check(js, osr, tasks)["findings"]}
    assert ("nightly", "missing task") not in found
    assert ("js-s", "value differs") in found


def test_a_script_named_only_in_other_lines_is_not_a_run(tmp_path):
    """Review CS10 R2: watchdog.py appears in every receipt line ("watchdog.py receipt ..."); a missing os-watchdog task
    must still be reported."""
    js, osr, tasks = _world(tmp_path)
    (osr / "routines" / "watchdog.yaml").write_text("name: watchdog\nenabled: true\naction: \"script:scripts/kernel/watchdog.py --receipt\"\n")
    (tasks / "js-a" / "SKILL.md").write_text("python3 x/scripts/a.py --capture --fresh10\npython3 scripts/kernel/watchdog.py receipt js-a --status ok\n")
    found = {(f["task"], f["kind"]) for f in rd.check(js, osr, tasks)["findings"]}
    assert ("watchdog", "missing task") in found
