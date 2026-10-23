---
title: {{SCOPE}} — standing orders from the orchestrator
programme: {{PROGRAMME}}
scope: {{SCOPE}}
doc_type: lane-orders
writer: ORCHESTRATOR ONLY — ⛔ this lane does not write this file; it writes lanes/{{SCOPE}}.md
---

# ⭐⭐⭐ §0 · STANDING — ANNOUNCE YOURSELF. Every resume, before anything else.

**This is permanent and it is NOT a work order.**

⛔ **The orchestrator CANNOT reach you first.** Your session **TITLE is not an address** — only the
auto-generated **registry name** resolves, nothing on disk records it, and it changes without notice
(*four different ids observed for one lane inside forty minutes*). ⇒ **The channel only exists if
YOU open it.**

```
1. Run `ListAgents`.  The FIRST line names you:  "This session is <name> [<ref>]".
   ⭐ That string is your ADDRESS.

2. Write it into `hub/lanes/{{SCOPE}}.md` under `## Identity`, in place, with your
   worktree, branch, tip and dirty count.

3. Send ONE line to {{ORCH_ADDRESS}} — nothing more:

      lane={{SCOPE}}  address=<your registry name>  tip=<sha measured from YOUR OWN worktree>  dirty=<n>  alive=yes

4. Then continue exactly what you were doing.
```

⚠ **Re-send it whenever you are re-opened, compacted, or restarted.** It costs one call and it is
the only thing that keeps the orchestrator able to reach you.
⭐ **If you are PAUSED, still do 1–3, then stop.** Announcing your address is not resuming work.
⛔ **Do not put status, rows or findings in that line** — disk is the contract; this line is an
address, nothing more.

---

# ORDERS — newest at the TOP. ⛔ Read this file FIRST on every resume.

## <date> · <headline>

<the tip moved / a ruling / a rebase instruction / an answer you were owed>

---

## 0 · WHERE YOU ARE (verified by the orchestrator — ⛔ do not re-derive, DO re-measure if it matters)

```
worktree <path>   HEAD <sha>   branch <branch>, <pushed|not pushed>
<clean|n uncommitted>
<n> commits, <n> gated
<phase>, <n> consecutive dry rounds
```

## 1 · WHAT IS BLOCKING YOU

⛔ **This list is exhaustive. Anything not on it, you decide yourself and record the reason.**

| # | blocker | owner | status |
|---|---|---|---|
| | | | |

## 2 · RULINGS THAT REACHED YOU

| id | ruling | source | date |
|---|---|---|---|
| | | | |

⛔ **A ruling that reaches one recipient is not a ruling, it is a reply** — every one also lands in
`hub/ORCHESTRATOR.md` in the same round.
