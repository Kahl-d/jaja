#!/usr/bin/env python3
"""routine_drift.py: does each installed scheduled task still run what its routine spec says (SETUP-FINAL A7)?

On 2026-10-03 the installed js-hunt-review task ran `hunt_review.py --capture` while its spec's action named
`--capture --fresh10`, and nothing noticed. The specs (routines/*.yaml here, routines/*.yaml for the OS) are the
source; the Desktop app's tasks live in ~/.claude/scheduled-tasks/<name>/SKILL.md. Read only. For each spec:
  missing task      the spec has no installed task (an enabled spec that never runs)
  script not run    the spec's `action: script:<path>` is not called in the task's prompt
  flag dropped      a `--flag` of the spec's action is absent from the task's call
and each installed task with no spec. Whether a task is switched on, and its schedule, live in the app, not on disk:
pass `--tasks-state FILE`, the JSON list the scheduled-tasks tool returns (list_scheduled_tasks: taskId, enabled,
cronExpression), and each installed task is also checked for
  enabled differs   the spec says enabled: true and the task is off, or the other way (: js-apply-batch's task ran
                    while its spec said off, and nothing noticed)
  schedule differs  the spec's schedule is not the task's cronExpression
  not in the app    an enabled spec whose task folder is on disk but not in the app's list (it never runs)
  stale folder      a leftover folder for a spec the app runs under another task name
  (a folder the app does not list is fine when its spec says enabled: false)
Without --tasks-state these are NOT checked, and the report says so on its first line. Exit 1 when anything drifts.

Usage: python3 scripts/routine_drift.py [--root R] [--os-root OS] [--tasks DIR] [--tasks-state FILE] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, os_root_for  # noqa: E402

TASKS = Path.home() / ".claude" / "scheduled-tasks"
ALIASES = {"os-": ""}            # the OS tasks are installed as os-<name>; their specs are routines/<name>.yaml


def specs(root: Path, os_root: Path) -> dict[str, dict[str, Any]]:
    import yaml
    out = {}
    for folder in (Path(root) / "routines", Path(os_root) / "routines"):
        for f in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if d.get("name"):
                out[str(d["name"])] = {**d, "_file": str(f)}
    return out


def spec_for(task: str, all_specs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if task in all_specs:
        return all_specs[task]
    for prefix, repl in ALIASES.items():
        if task.startswith(prefix) and task[len(prefix):] in all_specs:
            return all_specs[task[len(prefix):]]
    return None


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    """The scheduled-tasks tool's list (a JSON list, or {"tasks": [...]}) keyed by taskId."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("tasks", []) if isinstance(data, dict) else data
    return {str(r["taskId"]): r for r in rows if isinstance(r, dict) and r.get("taskId")}


def _cron(expr: Any) -> str:
    return " ".join(str(expr or "").split())


def check(root: Path, os_root: Path, tasks: Path, state: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    all_specs = specs(root, os_root)
    unlisted: list[tuple[str, dict[str, Any]]] = []
    installed = {p.parent.name: p.read_text(encoding="utf-8") for p in sorted(tasks.glob("*/SKILL.md"))} if tasks.is_dir() else {}
    findings, matched = [], set()
    for task, prompt in installed.items():
        spec = spec_for(task, all_specs)
        if spec is None:
            findings.append({"task": task, "kind": "no spec", "detail": "an installed task with no routine spec"})
            continue
        matched.add(spec["name"])
        if state is not None:
            row = state.get(task)
            if row is None:
                unlisted.append((task, spec))
            else:
                want, have = bool(spec.get("enabled")), bool(row.get("enabled"))
                if want != have:
                    findings.append({"task": task, "kind": "enabled differs",
                                     "detail": f"spec {spec['_file']} enabled: {str(want).lower()}; the installed task is {'on' if have else 'off'}"})
                if spec.get("schedule") and row.get("cronExpression") and _cron(spec["schedule"]) != _cron(row["cronExpression"]):
                    findings.append({"task": task, "kind": "schedule differs",
                                     "detail": f"spec {_cron(spec['schedule'])!r}; task {_cron(row['cronExpression'])!r}"})
        m = re.match(r"script:(\S+)(.*)", str(spec.get("action") or ""))
        if not m:
            continue
        script, args = m.group(1), m.group(2)
        calls = [ln for ln in prompt.splitlines() if Path(script).name in ln]
        if not calls:
            findings.append({"task": task, "kind": "script not run", "detail": f"the prompt never calls {script}"})
            continue
        for flag, value in re.findall(r"(--[\w-]+)(?:[ =]+([^\s-][^\s]*))?", args):
            hit = [c for c in calls if re.search(rf"{re.escape(flag)}\b", c)]
            if not hit:
                findings.append({"task": task, "kind": "flag dropped", "detail": f"{flag} of the spec's action is not in the call"})
            elif value and not any(re.search(rf"{re.escape(flag)}[ =]+{re.escape(value)}(?:\s|`|$)", c) for c in hit):
                findings.append({"task": task, "kind": "value differs",
                                 "detail": f"the spec runs {flag} {value}; the call differs"})
    for task, spec in unlisted:                    # a folder the app does not list: drift only when the spec says on
        if not spec.get("enabled"):
            continue
        live = [t for t in installed if t != task and t in (state or {}) and spec_for(t, all_specs) is spec]
        if live:
            findings.append({"task": task, "kind": "stale folder", "detail": f"the app runs this spec as {live[0]}; this folder is a leftover"})
        else:
            findings.append({"task": task, "kind": "not in the app", "detail": f"enabled in {spec['_file']}, but the app does not list the task, so it never runs"})
    every_line = [ln for text in installed.values() for ln in text.splitlines()]
    for name, spec in all_specs.items():
        m = re.match(r"script:(\S+)(.*)", str(spec.get("action") or ""))
        if name not in matched and m:
            path, args = m.group(1), m.group(2).split()
            # run as a step of another task (nightly-sweep in os-morning-brief): a line with the full script path and
            # every argument of the spec; "watchdog.py receipt ..." in receipt lines is not "watchdog.py --receipt"
            if any(path in ln and all(re.search(rf"(?<!\S){re.escape(t)}(?!\S)", ln) for t in args) for ln in every_line):
                matched.add(name)
        if name not in matched and spec.get("enabled"):
            findings.append({"task": name, "kind": "missing task", "detail": f"enabled in {spec['_file']} but not installed"})
    return {"specs": len(all_specs), "installed": len(installed), "findings": findings, "state_checked": state is not None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--os-root", type=Path, default=None)
    ap.add_argument("--tasks", type=Path, default=TASKS)
    ap.add_argument("--tasks-state", type=Path, default=None,
                    help="JSON from the scheduled-tasks tool (list_scheduled_tasks): checks enabled and schedule too")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    state = load_state(a.tasks_state) if a.tasks_state else None
    res = check(a.root, a.os_root or os_root_for(a.root), a.tasks, state)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"routine drift: {len(res['findings'])} finding(s) over {res['specs']} specs and {res['installed']} installed tasks"
              + ("" if res["state_checked"] else "; enabled and schedule NOT checked (pass --tasks-state)"))
        for f in res["findings"]:
            print(f"- {f['task']}: {f['kind']}: {f['detail']}")
    return 1 if res["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
