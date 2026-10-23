---
name: orchestrate
description: "Use when work is split across more than one session: starting/resuming an orchestrator, a lane or a sub-lane, handing work between sessions, or integrating a lane's change set"
disable-model-invocation: true
argument-hint: "[programme name | resume | status]"
---

# /orchestrate

**You are the manager. You do not do the work. You make N lanes add up to a shipped thing.**

⛔ **FIRST: load the `lane-orchestration` skill.** It is the doctrine; this command is the runbook.
Read its `references/messaging.md` before addressing any other session.

---

## `/orchestrate status` — the state, in one screen

1. Read `hub/SIGNAL.md`, `hub/LANES-LIVE.md`, `TRACKER.md`.
2. **Re-measure, do not quote:** for each lane, `git -C <its worktree> rev-parse HEAD` + `status
   --porcelain`, and the shipping tip. ⛔ **Never quote a lane's count back from memory or rebuild
   one by arithmetic** — re-measure, or write *"your declared value"* and let the lane supply it.
3. Report: the board · who holds the shared resource · answers owed · what merges next and why.

---

## `/orchestrate resume` — pick up an existing programme

1. `hub/README.md` → `SIGNAL.md` → `ORCHESTRATOR.md` → `LANES-LIVE.md` → `TRACKER.md`.
2. ⭐ **Re-establish the channel before anything else:** run `ListAgents`. Its first line is **your**
   address — write it into `LANES-LIVE.md`. Every lane row older than this session is a **memory,
   not an address**; re-handshake before relying on one.
3. ⭐ **Re-derive the board from the tree, do not patch it.** Everyone reads the first table.
4. Then: answer what is owed, before starting anything new.

---

## `/orchestrate <name>` — open a new programme

### ① Scaffold — do not hand-build a hub

```bash
scripts/kernel/new-programme.sh --programme "<name>" --root <project dir, e.g. projects/example-programme> \
    --prefix <PFX> --ticket <T> --branch <shipping-branch> --base origin/main
```

The hub lands at `<root>/hub/` (with `TRACKER.md`, `DIVISION.md`, `classify.py` beside it).

⛔ Refuses to overwrite a live hub. **Never scaffold a second hub beside one.**

### ② The channel — measure it, then ask the owner

- `ListAgents` → your own address (line 1) → into `LANES-LIVE.md` §B with a UTC timestamp.
- ⭐ **Ask the owner the account question and record the answer:** *same account* ⇒ two-way
  `SendMessage`, shared rate limit. *Separate accounts* ⇒ files only, separate limits.
  ⛔ **Do not decide it for the owner, and do not assert either answer without measuring.**

### ③ Divide by mechanism

- Fill `classify.py`'s `GROUPS` (ordered → the primary writer) and `ALSO` (a second **reader**).
- Run it. ⛔ **The division is not accepted until `UNASSIGNED: 0`.** Place every gap deliberately.
- ⭐ Add the **second axis** — the end-to-end flow tracers that cross scope boundaries. **A file
  partition cannot hold a seam, and that is where the defects are.**
- ⭐ Add the **third** — *what UNTOUCHED file does this change obsolete?* Routed by hand.
- Run `DIVISION.md` §6's adversarial pass on the division itself, before any lane opens.

### ④ Index the research

Fill `hub/RESEARCH-MAP.md`, one row per scope. ⛔ **A lane that re-derives what is on disk has
spent its budget on the one thing that was never missing.** Mark older revisions *orientation only*.

### ⑤ Brief, then hand over — the owner starts the lane

- Write `hub/briefs/<scope>.md` and `hub/lanes/<scope>-ORDERS.md` (its `§0` is standing).
- Fill `hub/starters/_LANE-STARTER.md` completely — ⛔ **no placeholders; the lane must not need a
  second message.** Keep *"tell me anything in this prompt you believe is wrong."*
- ⛔ **You never start a lane. Give the prompt to the owner.**
- Register it in `LANES-LIVE.md` when **it** writes its own address into the hub.

---

## Running it

| ⭐ do | ⛔ do not |
|---|---|
| answer fast and specifically; keep the answers-owed table live | hover, or review work-in-progress |
| ⭐ **re-run every load-bearing claim yourself** before accepting it | accept a verdict you cannot reproduce |
| accept correction plainly and **record the general rule** where the next lane reads it | defend a ruling that has been falsified |
| ⭐ **propagate** measured results between lanes — yields, traps, lifted constraints | let each lane rediscover the same lesson |
| keep the collision list explicit and current | assume disjoint-by-file covers everything |
| ⭐ arm a watch on lane branches, commits and hub files | poll `ListAgents`, or ask "are you done?" |

⭐ **A ruling that reaches one recipient is not a ruling, it is a reply** — every ruling lands in
`ORCHESTRATOR.md` **and** the lane's brief in the same round.
⭐ **If a lane asks whether to keep going, the answer is yes.**

---

## Integrating a handover — `references/merge-train.md` is the protocol

**Nine steps, nothing skips:** the lane DECLARES → change set applied **uncommitted** → your
complete review → the full zero-error pass → every review-skill learning → **you
live-drive every fix** → ⛔ **findings go BACK TO THE LANE** → ⭐ **the app stays running and the owner
live-tests and gives FEEDBACK, which also goes back to the lane** → **commit + push on the owner's word.**

⛔ **Before you merge:** red-first the lane's arms yourself · full suites on the **integrated** tree
· **every CI lane, not just the default one** · no assertion deleted · `git var GIT_AUTHOR_IDENT`
· safety tags. ⛔ **`--ff-only`; if it refuses, the lane did not rebase — send it back.**
⛔ **Release the shared resource before you merge** — a merge moves the tree under a live app.

⛔ **The owner approves the merge AND the push. Neither is implied by the other.**
⛔ **One commit per scope, in the owner's authorship, per the owner's commit policy.**
