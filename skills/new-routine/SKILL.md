---
name: new-routine
description: Use when something must happen on a schedule (cloud, GitHub Actions or the desktop app) or the owner asks to automate a recurring job. Interview, routine spec from template, receipt folder, budget line, watchdog wiring, lint, dry run.
disable-model-invocation: true
---

# New routine

Today is !`date +%F`. Request: `$ARGUMENTS`

## Procedure
1. **Interview**: what must be true after each run? Must it run while the Mac sleeps (cloud),
   is it deterministic with no connectors (actions), or does it need local files or Apple apps
   (desktop)? Which skill, role or script does the work? Which connectors, truly? What is the
   "needs you" sentinel? Owner and budget?
2. **Classify** `runs_on` and `engine`; check `routines/` for an existing routine that already
   covers it.
3. **Scaffold** `routines/<name>.yaml` from `templates/routine.yaml` with every key;
   `enabled: false` until the dry run passes; `last_reviewed` = today. Never give
   an unattended routine a send-capable connector.
4. **Wire**: create the runtime's `logs/runs/<name>/` receipt folder; add the budget line to
   the budgets policy; the watchdog reads the spec on the next sweep. Installation of the live
   copy (cloud routine, desktop task or Actions workflow) is manual until an installer exists;
   record the install command in the spec's comment.
5. **Lint**: `python3 scripts/kernel/lint.py`.
6. **Dry run**: execute the action once by hand and write the first receipt
   `logs/runs/<name>/<today>.md`; only then set `enabled: true`.
7. Report the spec path, the runner, and the receipt.
