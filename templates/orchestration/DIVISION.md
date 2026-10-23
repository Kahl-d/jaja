---
title: The division — every file in a scope, nothing unassigned
programme: {{PROGRAMME}}
shipping_branch: {{BRANCH}}
base: {{BASE}}
doc_type: division
writer: ORCHESTRATOR ONLY
status: <division-complete | division-in-progress>
---

# THE DIVISION — {{PROGRAMME}}

## 0 · The one number that matters

| | |
|---|---|
| diff | `{{BASE}}...HEAD` — three-dot, **our changes only** |
| merge base | <sha> |
| tip | <sha> — ⛔ re-measured, never quoted from an older document |
| **surface** | **<n> files · +<n> / −<n>** |
| **assigned** | **<n> / <n>** |
| **unassigned** | ⭐ **0** |

---

## 1 · How the division was made — so it can be RE-RUN, not re-argued

1. `git diff --numstat {{BASE}}...HEAD` — measured live, ⛔ **never a snapshot on disk**.
2. Every file matched against ordered rules in [`classify.py`](classify.py) — **a real script, not a
   hand list**, so a new file cannot silently escape it.
3. ⭐ **The coverage assertion is the point:** the script prints `UNASSIGNED` and the division is not
   accepted until that count is **0**. Every gap it finds is **placed deliberately, never swept.**
4. Per-scope diffs and manifests emitted into `diff/` and `manifests/` — **this is how a big diff is
   managed in parts.**

```bash
python <this dir>/classify.py
```

⛔⛔ **THE TWO WAYS THIS INSTRUMENT LIES, both measured:** it read a **frozen snapshot** while the
live diff had moved, so `UNASSIGNED: 0` was **true by construction**; and an **empty diff** would
report full coverage of nothing. ⭐ **A check that cannot fail reads exactly like a check that keeps
passing.** The script measures from git and **exits** rather than falling back.

---

## 2 · The rules

- **A file may belong to several scopes; it must belong to at least one.** ⭐ Overlap is a feature —
  it gives a **second reader**, never a second writer. The `also` column counts those.
- **Tests ride with their subject**, never as a "tests" scope. Reviewing a component means reviewing
  the arms that claim to cover it.
- ⭐ **Sized by RISK, not by lines.** Findings-per-file correlates **inversely** with safety.
- **Sub-parts, not more scopes.** A scope is what must be *understood* together; sub-parts are how a
  large one is *worked* — and a large enough sub-part gets a **sub-lane**.
- **Primary counts never double-count.**

---

## 3 · The scopes

| ID | scope | files | +lines | code | tests | also-in |
|---|---|--:|--:|--:|--:|--:|
| | | | | | | |
| | **TOTAL (primary, no double-count)** | | | | | |

*(per scope: what it is · why these files are ONE unit · its sub-parts · what is already known)*

---

## 4 · ⭐⭐ THE SECOND AXIS — flow tracers

⛔ **A FILE PARTITION CANNOT HOLD A SEAM, and every defect lives in a seam.** Reviewing every file
and stopping there leaves the end-to-end paths never traced.

| id | the journey | crosses | proven by |
|---|---|---|---|
| F-A | | | ⛔ **a live drive** |

⛔ **A flow tracer that emits no findings is decoration.** Give it a quota.

---

## 5 · ⛔ THE THIRD BLIND SPOT — a file this change BREAKS WITHOUT TOUCHING

A diff-scoped division cannot see it. **The shape is always the same: something enumerates its
members by hand, we added a member, and the hand list did not move.**

⇒ ⭐ **Standing entry condition on every scope: *what UNTOUCHED file does this change obsolete?*** —
switchers, pins, denylists, allowlists, registries, generated catalogues, docs. **Routed by hand.**

| file | what obsoletes it | routed to |
|---|---|---|
| | | |

---

## 6 · ⭐ THE ADVERSARIAL PASS ON THE DIVISION ITSELF

**A partition built to catch seam defects is itself a place a seam defect can hide.** Run before the
first lane opens:

| # | question | verdict | what changed |
|---|---|:--:|---|
| 1 | Does any scope hold the **oracles** for another scope's subject? | | |
| 2 | Does every overlap stated in **prose** exist in the **mechanism**? | | |
| 3 | Is any large test file primary in a different scope from the module it imports? | | |
| 4 | Does any generated / cross-cutting artefact have a one-scope review and a multi-way seam? | | |
| 5 | Do the unit counts in **every** document agree? | | |

## 7 · Coverage proof

```
TOTAL FILES: <n>   ASSIGNED: <n>   UNASSIGNED: 0        measured <UTC>
```
