---
title: MERGE TRAIN — the state of integration for this programme
programme: {{PROGRAMME}}
doc_type: hub-runbook
writer: ORCHESTRATOR ONLY
---

# 🚂 THE MERGE TRAIN — {{PROGRAMME}}

⛔ **The protocol lives in `lane-orchestration/references/merge-train.md`** — the nine steps, the
gate's checks, the rebase proof, the abort path. **This file is the STATE.**

⭐ **The model:** `{{BRANCH}}` is the product and keeps moving forward. Each `{{PREFIX}}-<scope>` is
a feature branch. **Lanes merge in ONE AT A TIME, and after each merge every remaining lane rebases
onto the new tip and re-proves its own work still stands.**

⭐ **A lane never has to stop because someone else merged.** Rebasing is owed **at your own merge**.
⚠ **But rebase early anyway** — conflicts found while small are cheap.

---

## 0 · CURRENT

```
SHIPPING   {{BRANCH}}   <tip>   <n> ahead / <n> behind {{BASE}}   <linear|not linear>
AUTHOR     ⛔ {{AUTHOR}} — author AND committer, every commit, zero AI attribution
NEXT       <which scope declares next, and why it is next>
HELD       <lanes told not to move their branch ref, and until when>
```

---

## 1 · PRECONDITIONS — a lane is not "done" until all four are true

| # | precondition | who checks |
|--:|---|---|
| 1 | **Work list at zero** — every item `FIXED` / `NO-FIX` (with reason) / `REFUTED` | the lane |
| 2 | **Vision list at zero** — every item aligned or explicitly deferred with a reason | the lane |
| 3 | **The 8 self-review checks pass and the FINAL VERDICT is written** | the lane |
| 4 | ⭐ **The orchestrator's independent gate passes, on the INTEGRATED tree** | the orchestrator |

⛔ **"The suite is green" is not a precondition, it is an input.**

---

## 2 · RUNS

| # | scope | cut | tip after | files / lines | red-first | gate | verdict |
|--:|---|---|---|---|---|---|---|
| | | | | | | | |

---

## 3 · WHAT THE GATE CAUGHT THAT NO SUITE DID

**Record every one. This table is why the gate exists, and it is what the next lane reads.**

| # | what | why it was invisible to the lane |
|---|---|---|
| | | |

---

## 4 · SAFETY TAGS TAKEN

```
pre-merge/<scope>-<date>      the shipping tip before the merge
lane/<scope>-handover         the exact tree the lane handed over
pre-rebase/<scope>-<date>     each lane, before it rebases
```

⭐ **Five durability layers, and work has to fall through all five to be lost:**
lane WIP → pushed lane branch → handover tag → pre-merge tag → the shipping commit.
