---
title: The tracker — the canonical progress table for {{PROGRAMME}}
programme: {{PROGRAMME}}
ticket: {{TICKET}}
shipping_branch: {{BRANCH}}
doc_type: programme-tracker
writer: ORCHESTRATOR ONLY — ⛔ lanes hand over ROWS, they do not write this file
status: <one line a stranger could act on>
---

# TRACKER — {{PROGRAMME}}

> ⛔⛔ **THE BOARD IS RE-DERIVED FROM THE TREE, NEVER PATCHED.** Correct it in place — everyone reads
> the first table. ⭐ **Lane state comes from each lane's own last report; commit counts are measured
> against the product tip.**

## THE BOARD — re-derived <UTC>

| scope | tip | vs product | open C / H / M | dry rounds | remote | state |
|---|---|--:|---|:--:|:--:|---|
| | | | | | | |

```
PRODUCT   {{BRANCH}}  <tip>   <n> dirty   <n> ahead / <n> behind {{BASE}}
          <linear | NOT linear, and why>
          author AND committer = {{AUTHOR}}          ⛔ verified with `git var`
COVERAGE  <n> closed (<n>%) · <n> active · ⛔ <n> NEVER OPENED (<n>%)
```

⭐ **Report STATES, not fractions.** `SPECIFIED · IN PROGRESS · IMPLEMENTED · PROVEN · NO-CHANGE`.
⛔ **"Done" here means LANDED on `{{BRANCH}}`** — never "the lane edited it". Work on a lane branch
is *in-lane*. **That distinction is the whole reason this rollup exists.**

---

## A · THE LANE LEDGER — scope ↔ lane ↔ worktree ↔ branch ↔ resource

| scope | owns | lane session | worktree | branch | resource | opened |
|---|---|---|---|---|---|---|
| | | | | | | |

⭐ **Addresses live in `hub/LANES-LIVE.md` §B, with expiry.** ⛔ Not here — this table must not rot.

---

## B · ⛔ RULINGS — binding on every lane

**A ruling that reaches one recipient is not a ruling, it is a reply.** Every ruling lands here **in
the same round it is sent**, and in `hub/ORCHESTRATOR.md`.

| id | ruling | source | date |
|---|---|---|---|
| | | | |

---

## C · SEAMS AND COLLISIONS — which pairs of scopes can reach the same file

⛔ **Disjoint-by-file does not cover everything.** Every defect a seam-shaped project produces lives
*between* files. **Keep this list explicit and current.**

| scope A | scope B | shared surface | ruling |
|---|---|---|---|
| | | | |

---

## D · CROSS-LANE — filed by one scope, owned by another

⛔ **Routed by the classifier's rule order, never by hand.**

| id | filed by | cites | routed to | state |
|---|---|---|---|---|
| | | | | |

---

## E · CARRIED — real, documented, deliberately not done now

⭐ **A carry is a real artefact, not a shrug:** id · evidence · owner · the reason it was carried.
**"Documented for later" must mean a queue somebody can work.**

| id | what | severity | owner | why carried | date |
|---|---|---|---|---|---|
| | | | | | |
