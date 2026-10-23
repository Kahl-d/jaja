# Lane orchestration: implementation notes

How the `lane-orchestration` skill maps onto the scripts, hooks and templates in this repo. The doctrine itself is in
`SKILL.md` and `references/`.

## 1 · What implements what

| doctrine | implementation |
|---|---|
| hub scaffold (`/orchestrate <name>`) | `scripts/kernel/new-programme.sh`, filling `templates/orchestration/` |
| lane and orchestrator registration | `hooks/lane_register.py` (SessionStart), writing `hub/registry.json` |
| role resolution (orchestrator, lane, sub-lane) | `hooks/lane_common.py`, from `hub/programme.json` `"lanes"` (`{"scope","branch","parent"}`) |
| session context printed at start | `hooks/lane_context.py` |
| one writer per file | `hooks/lane_write_guard.py` (PreToolUse on `Write|Edit|MultiEdit`) |
| hub events (`events.jsonl`) | `scripts/kernel/hub_events.py` |

## 2 · The scaffold script

`scripts/kernel/new-programme.sh` is plain bash (3.2 compatible) and refuses to overwrite a live hub (use `--force` to
re-scaffold). Flags: `--programme`, `--root`, `--prefix`, `--ticket`, `--branch`, `--base` (default `origin/main`),
`--author` (default derived from `git config` of `--root`, then global), `--force`. It writes `<root>/hub/` plus
`TRACKER.md`, `DIVISION.md` and `classify.py` beside it, where `lane_write_guard` expects them, and a
`hub/programme.json` carrying the prefix. Register each lane in `hub/programme.json` `"lanes"` before it starts.

Template tokens: `{{ORCH_ADDRESS}}` is filled with `<PFX>-ORCH` (launch the orchestrator with
`claude --name <PFX>-ORCH`); `{{ACCOUNTS}}` stays `<UNMEASURED>` until the five-minute probe in
`references/messaging.md` section 5 is run and recorded; `{{LAUNCH_CMD}}` is the programme's command to launch the
shared resource on a lane's own worktree.

## 3 · Hook behaviour

- `find_hubs()` walks the workspace for `**/hub/programme.json`, pruning dot-directories and heavy folders
  (`node_modules`, `.pnpm`, `dist`, `build`, `__pycache__`, `.venv`, `venv`, `DerivedData`, `Pods`). A bare recursive
  glob would descend into `node_modules` and `.git` and be far slower on every SessionStart.
- `hub_for_path` (walk-up from the written file) works for any hub location without `find_hubs`.
- Wiring: `lane_context.py` and `lane_register.py` on SessionStart; `lane_write_guard.py` on PreToolUse for
  `Write|Edit|MultiEdit`.
- Messaging needs `crossSessionInbound` set deliberately (`references/messaging.md` section 0c).

## 4 · Known simplifications

- Post kinds keep the original `LIVE-APP REQUEST / GRANTED / RELEASED` vocabulary although the ledger
  (`hub/RESOURCES.md`) is generic: read "live app" as "the shared resource".
- Phase D of the lane lifecycle refers to an `implementation-mode` skill; where the user has none, use the per-item loop
  in `SKILL.md` section 4b.
- Commit attribution on orchestrated work follows the owner's own rules; set them in the user's config.
