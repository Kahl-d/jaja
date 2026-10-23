---
name: base-compare
description: "Use after a variant renders and before anyone reads it: judges every changed bullet and order blind against the current base for this JD (shuffled A/B, key sealed) and marks KEEP or REVERT; the owner's own ruling on the role wins; the base ships unless the variant wins."
context: fork
agent: base-comparer
background: false
---

# Base compare (the regression gate, blind)

Today is !`date +%F`. Argument: `$ARGUMENTS` = `<slug>`. V = `resumes/variants/<slug>`.

0. **Same base.** `python3 scripts/base_drift.py V/` exits 0: a variant cut from an older base is rebased by the tailor
   first (compare judges against the base that ships).
1. **The owner first.** (The orchestrator's rulings recorded in NOTES are scope decisions: judge those pairs on merit and say
   whether you agree; only the owner's words decide a pair.) `grep -n -i "<company or title words>" hub/rulings.yaml` and
   read every ruling that names this role, this company's page or this slug. If the owner ruled on this page, that ruling
   decides: write it at the top of compare.md and REVERT anything it covers. You judge only what the owner has not ruled
   on. A ruling about one page covers that page only. For every other variant the standing default applies: the base
   keeps on a tie, and a reorder is judged blind like any other pair (never reverted unread).
   If the owner's rulings cover every pair: still run step 2 (the pairs are the record), skip steps 3 and 4, and write
   compare.md with each pair `REVERT (<ruling id>)` and the last line `KEEP 0 · REVERT n · base unchanged: yes`.
2. **Make the pairs** (Experience bullets and order, Skills rows and order; do this step, then do NOT open the key):
   `python3 scripts/blind_pairs.py make --resume V/resume.yaml --base resumes/bases/general/resume.yaml --out V/compare/pairs.md --key V/compare/key.json --seed $(date +%s)`
3. **Judge blind.** Read ONLY `V/compare/pairs.md`, the JD and the package sections 1, 6 (the Reader model: judge for that reader's wants) and 7, and the tailor's NOTES `The person` row only after reveal (the page must stay true to the owner's core work). Do not read V/NOTES.md,
   the base file or the key. For each pair write `V/compare/verdicts.yaml`: `<pair id>: {winner: A|B|tie, why: "<one sentence quoting both>"}`.
4. **Reveal**: `python3 scripts/blind_pairs.py reveal --key V/compare/key.json --verdicts V/compare/verdicts.yaml`.
5. Write `V/compare.md`: the rulings applied (step 1), the revealed table, and the last line from reveal.
   Return that last line and one sentence why.
