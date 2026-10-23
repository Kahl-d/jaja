---
title: Target role definition (read by fit-scorer)
type: targets
owner: role:fit-scorer
updated: null
---

# Target roles

The definition fit-scorer applies to every posting before the rubric. Filled in for the demo candidate; replace it with your own search.
Order of operations: **hard gates (section 4) -> seniority band (section 3) -> title match (sections 1-2) -> signals (sections 5-7)**.
A gate failure is `fit_verdict: skip` with the evidence line quoted, never a low score.

## 1. Included titles

Match on the normalized title (lowercase, punctuation stripped). Lens = the resume base to start from.

| Family | Title patterns to accept | Lens |
|---|---|---|
| AI and ML engineering | `ai engineer`, `machine learning engineer`, `ml engineer`, `ai product engineer` | ai-engineer |

Plain product engineer is deliberately not listed: in a global filter it would let every product-engineering role through for
unlisted companies too. List only the qualified form.

## 2. Excluded titles (skip without scoring)

- `sales engineer`, `solutions engineer`

## 3. Seniority band

- (levels you accept, the years window you can truthfully meet)

## 4. Hard gates

- (the facts that make a role impossible: location, work authorization, clearance, and so on)

## 5. Signals that raise a score

- (what makes a posting better)

## 6. Signals that lower a score

- (what makes a posting worse)

## 7. Company types

- (what you want and avoid in an employer)
