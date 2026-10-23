---
name: new-automation
description: Use when something should happen in reaction to an event (session start or end, a tool call, a file change, a new inbox item, a routine finishing, a webhook) rather than on a schedule. Interview, automation spec from template, hook or sweep wiring, lint, dry run.
disable-model-invocation: true
---

# New automation

Today is !`date +%F`. Request: `$ARGUMENTS`

## Procedure
1. **Interview**: which event (`SessionEnd | SessionStart | PreToolUse | FileChanged(path) |
   inbox:new(type) | routine:done(name) | webhook`)? Under what condition? What runs, and is it
   deterministic (script) or judgment (skill)? Tier? Can it block, or only observe?
2. **Classify**: hook-backed events are compiled into `hooks/hooks.json` by sync;
   inbox- and routine-backed ones are evaluated by the sweep. Check the automations folder and the
   hooks file for an existing rule first.
3. **Scaffold** an automation spec from `templates/automation.yaml`, `enabled: false`. A blocking
   guard must be a script under `hooks/` that exits 2 with a reason and fails open on parse errors.
4. **Wire**: `python3 scripts/kernel/sync.py` (hooks merged into the engine adapters); receipts go to
   the runtime's `logs/runs/<name>/` if the action is more than a guard.
5. **Lint**: `python3 scripts/kernel/lint.py`.
6. **Dry run**: fire the event once by hand (e.g. echo a JSON payload into the script) and confirm
   the expected exit code and output; then set `enabled: true`.
7. Report the spec path, the event, and the dry-run evidence.
