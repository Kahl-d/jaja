# Starters, briefs and orders — the prompts that worked

⛔ **The orchestrator never starts a lane. The owner does.** The orchestrator's job is to hand the owner a
prompt so complete that **the lane never needs a second message to begin.**

---

## 1 · ⭐⭐⭐ THE LANE STARTER PROMPT

The owner pastes this into a fresh session. Fill every `<…>`; ⛔ **leave no placeholder in.**

```markdown
You are the **<PREFIX> <SCOPE> LANE** for <programme> (<ticket>, shipping branch `<branch>` @ `<sha>`).
You own ONE scope: **<scope>** — <one line of what it is, in the USER's terms> (<size: files, lines>).
A separate session, "<PREFIX> MAIN lane", is the **orchestrator** and owns ALL git.

## ⭐ FIRST THREE ACTIONS, IN THIS ORDER, BEFORE ANYTHING ELSE

1. **Run `ListAgents`.** The FIRST line of its output names YOU — e.g.
   `This session is <name> [<ref>]`. ⛔ **That string is your ADDRESS. Your session TITLE is not.**
2. **Write it into `<hub>/lanes/<SCOPE>.md`** under `## Identity`, with your worktree, branch, tip
   and dirty count. Rewrite that block in place on every resume — it is a cache, not a record.
3. **Send ONE line** to the orchestrator (`<orchestrator registry name>`), nothing more:
   `lane=<SCOPE> address=<your name> tip=<sha> dirty=<n> alive=yes`
   Then continue. ⛔ Do not put status or findings in it — disk is the contract.

(Also rename this session to `<PREFIX> <SCOPE> lane` so the owner can find it. That is for the owner's
navigation only; it does not change your address.)

## YOUR DOOR — read in this order

`<hub>/README.md` — the contract (roles · the one-writer rule · the lifecycle · the handover checks)
`<hub>/ORCHESTRATOR.md` — standing directives, newest at the top
`<hub>/briefs/<SCOPE>.md` — YOUR assignment, boundaries and rulings
`<hub>/lanes/<SCOPE>-ORDERS.md` — the live wire; ⛔ read it FIRST on every resume
`<hub>/lanes/<SCOPE>.md` — YOUR file. You are its only writer
`<hub>/RESEARCH-MAP.md` — ⭐ what ALREADY EXISTS for your scope. ⛔ Do not re-derive it

## STARTUP WORK — all of it, then report

  1 the project    2 the vision (the correctness ORACLE, not background)
  3 the process and the roles    4 your scope    5 what is already researched

## WORKTREE

    git worktree add <path OUTSIDE the project folder> -b <lane-branch> <sha>
    cd <path>  &&  <install>  &&  <the provisioning steps in your brief>
    git push -u origin <lane-branch>

⛔ Push it continuously — insurance, never a merge request.
⛔ ALWAYS rebase from the shipping branch; NEVER merge.
⛔ NEVER touch the shared worktree. NEVER commit or push on `<branch>`.
⛔ Before your first commit: `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`
   must both be `<the one permitted identity>`. Environment variables override git config.

## RESOURCE

`<slot / instance / device>` is shared. ⛔ Announce in your file BEFORE you take it, read the other
lanes' files first, and release when done. One holder at a time.

## HARD RULES

- You do NOT commit or push on the shipping branch. You produce a reviewed, evidenced CHANGE SET.
- <the real full stop for this programme — e.g. "hand the complete review back, then STOP">
- Self-review before handover: the eight checks in `<hub>/README.md`.
- Write ONLY your own file in `hub/`. A defect in another scope's files is a `CROSS-LANE` post,
  never an edit.

## HOW WE WORK

⭐ You are **AUTONOMOUS inside your scope.** Do not ask permission to think, probe, read or
disagree. Ask only when your brief names it as blocking. ⭐ **Chain your work — do not end a turn
because one unit finished.** A session halts until a human types, so every turn you end, this lane
is dead until someone notices.
⭐ Message me directly and often; I answer fast. But **disk is the contract** — anything that must
survive goes in your file.

## FIRST MOVE

<the concrete first action>, then post `STATUS` with your plan, what you expect to yield most, and
⭐ **anything in this prompt you believe is WRONG.** Then <stop for the vision | continue>.
```

⭐⭐ **Always include the last line.** A lane's first post routinely finds errors in
its own instructions — sometimes the orchestrator's own bad routing ruling. **That question is why.**

⭐ **And if the scope has no vision yet, the starter's FIRST MOVE is: explain the scope back to
the owner and STOP.** No vision, no work.

---

## 2 · ⭐ THE STARTUP CHECKLIST — hand this with the starter

```markdown
Before you file anything, you can answer all five:
  □ THE PROJECT — what is shipping, and what does the user get from it
  □ THE VISION — your scope's §0, verbatim. It is the ORACLE and a SOURCE OF WORK
  □ THE PROCESS — the roles, the one-writer rule, the lifecycle, the handover checks
  □ YOUR SCOPE — its files, its units, its seams, what it inherits, what it does NOT own
  □ THE RESEARCH — your row in RESEARCH-MAP.md, read. ⛔ Nothing on it gets re-derived
```

---

## 3 · `hub/briefs/<id>.md` — the orchestrator's assignment

```markdown
---
title: <SCOPE> — brief
writer: orchestrator — ⛔ the lane does not write this file; it writes lanes/<SCOPE>.md
---
## 1 · What you own            files, units, sub-parts, and ⛔ what you do NOT own
## 2 · Boundaries              scoped write grants (named), the collision list, the seams
## 3 · Resources               instance/slot, worktree path, branch, base sha, provisioning steps
## 4 · What is already known   inherited rows, prior rounds, the research map row
## 5 · What is BLOCKING        ⛔ the exhaustive list. Anything not here, you decide yourself
## 6 · Rulings                 dated, newest last. Every ruling that reached you lands here too
```

⛔ **A ruling that reaches one recipient is not a ruling, it is a reply.** Every ruling lands in the
brief **in the same round it is sent**.

---

## 4 · `hub/lanes/<id>-ORDERS.md` — the live wire

**`§0` is permanent and is not a work order.** It is the self-healing half of the channel: a lane
that resumes re-registers itself, so **the orchestrator is removed from the critical path.**

```markdown
# §0 · STANDING — ANNOUNCE YOURSELF. Every resume, before anything else.
⛔ The orchestrator CANNOT reach you first. Your session TITLE is not an address; only the
auto-generated registry name resolves, nothing on disk records it, and it changes without notice.
⇒ The channel only exists if YOU open it.
  1. `ListAgents` — the first line names you. That is your address.
  2. Write it into `hub/lanes/<id>.md` under `## Identity`, with tip and dirty count.
  3. Send ONE line: `lane=<id> address=<name> tip=<sha> dirty=<n> alive=yes`
  4. Continue what you were doing. ⭐ If PAUSED, still send it, then stop.

# <dated orders, newest at the top — the tip moved, a ruling, a rebase instruction>
```

---

## 5 · ⭐ `SIGNAL.md` — the cheap poll

**One table. Rewritten in place. Read at every phase boundary and before every blocking question.**

```markdown
# 🔔 SIGNAL — last update <UTC>
| scope | what changed | where |
|---|---|---|
| **<G>** | one line — the thing that would change what you do next | link to the brief |
```

⭐ **It is cheap to read, which is why it gets read.** ⛔ **There is no push** — a ruling reaches a
lane when the lane next looks.

---

## 6 · ⭐ THE SUB-LANE STARTER

Same shape, three substitutions, written by **the LANE**, started by **the owner**:

- *"A separate session, `<PREFIX> <LANE> lane`, is **your orchestrator** and owns the lane branch."*
- Door: `hub/lanes/<L>/briefs/<S>.md` (yours) · `hub/lanes/<L>/sub/<S>.md` (your file) ·
  and the lane's own file for context.
- Handover: ⛔ **to the LANE, never to the orchestrator.** Branch off the LANE's branch; the lane
  integrates and makes the single upward handover.
- Instance: ⛔ **through the lane**, which holds the grant.

---

## 7 · ⛔ WHAT MAKES A STARTER FAIL

| ⛔ | ⭐ instead |
|---|---|
| *"rename this session to X"* offered as the addressing step | `ListAgents` → the first line → write it down |
| discrete units with exit criteria | a **self-clearing exit test** per unit, and *"chain them"* said explicitly |
| a scope described in module names | described **in the user's terms** — a scope explained in module names produces a vision about module names |
| no pointer at existing research | the `RESEARCH-MAP.md` row, named |
| the provisioning steps left out | every environment trap listed, or the lane reports phantom failures |
| no *"tell me what is wrong here"* | it is the highest-yield line in the prompt |
