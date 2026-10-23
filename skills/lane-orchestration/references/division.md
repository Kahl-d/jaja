# Dividing the work — groups, units, parts and flow tracers

**The partition is a script's output, not a judgement.** Its form is `classify.py`: an ordered
rule list mapping path → group, asserting `UNASSIGNED: 0`. Coverage is asserted separately
(`units.py`: `UNCOVERED: 0, AMBIGUOUS: 0`).

⭐ **Why it has to be mechanical:** a hand list cannot be re-run, so a new file silently escapes it,
and the division becomes something to re-argue instead of re-measure.

---

## 1 · The rules of the division

| principle | why |
|---|---|
| ⭐ **Disjoint by file, for the PRIMARY label** | two lanes must never be able to write the same path. **This is what makes parallelism safe** |
| ⭐ **Overlap is a feature — as a SECOND LENS** | a file may be *also-read* by other groups (`ALSO` rules). It gives a second reader without a second writer |
| ⭐ **Sized by risk, not by lines** | findings-per-file correlates **inversely** with safety: the biggest file draws many findings and still hides misses, while files that *generate what ships* draw none |
| ⭐ **Tests ride with their subject** | never a "tests" group. Reviewing a component means reviewing the arms that claim to cover it — half the surface is usually the safety net |
| ⭐ **Sub-parts, not more groups** | a group is a coherent unit that must be *understood* together; sub-parts are how a large one is *worked* in sittings (and, if large enough, given to a sub-lane) |
| **Order by dependency and risk** | what blocks others opens first; what holds a live exploit opens first |
| ⭐ **Scoped write grants over reclassification** | when one lane needs a file another owns: **primary stays put**, grant a *named, scoped* exception, tell the owner. ⛔ **Never edit the classifier to dodge a boundary** |

⚠ **When two ledgers disagree about ownership, the orchestrator rules and records it.** A
measurement does not entitle it to reverse a call the human made — **surface it instead.**

---

## 2 · ⛔⛔ A FILE PARTITION CANNOT HOLD A SEAM — so add a second axis

**Defects tend to live *between* files.** A large diff reviewed unit by unit, and
stopping there, leaves the end-to-end path never traced.

⇒ ⭐ **Two axes, always:**

| axis | what it is | proven by |
|---|---|---|
| **① FILE UNITS** | the groups and their sub-parts. Every file in exactly one primary | reading, probing, executing predicates |
| ⭐ **② FLOW TRACERS** | the end-to-end journeys that **cross group boundaries** — start · stop · resume · repair · delete · restart · failure path | ⛔ **a live drive**, not by reading |

⛔ **A flow tracer that emits no findings is decoration.** Give it a quota.

### ⭐ AND THE THIRD BLIND SPOT: a file your change BREAKS WITHOUT TOUCHING

**A diff-scoped division is structurally blind to it.** The shape is always the same: *something
enumerates its members by hand, we added a member, and the hand list did not move.*

⇒ ⭐ **Standing entry condition on every scope: *what UNTOUCHED file does this change obsolete?*** —
switchers, pins, denylists, allowlists, registries, generated catalogues, docs. Those rows are
routed **by hand**, because no rule derived from the diff can reach them.

---

## 3 · The classifier — the shape that works

```python
# The INPUT IS MEASURED, NOT REMEMBERED.
# ⛔ It must NEVER fall back to a snapshot on disk: a check that cannot fail
#    reads exactly like a check that keeps passing.
out = subprocess.run(["git","-C",REPO,"diff","--numstat",BASE], check=True, ...).stdout
if not rows: sys.exit("Refusing to report full coverage of nothing.")

G = [ ("G1","<title>", [r"^path/regex/", ...]),        # ORDER decides the PRIMARY label
      ... ]
ALSO = { "G4": [r"^other/group/file\.ts$", ...] }      # secondary membership: a second READER

# for each file: first matching rule wins the primary; every ALSO rule adds a reader
print(f"TOTAL: {n}  ASSIGNED: {a}  UNASSIGNED: {u}")   # ⛔ the division is not accepted until u == 0
```

⛔⛔ **THE TWO WAYS THIS INSTRUMENT LIES:**

1. **It reads a FROZEN snapshot** (`diff/00-numstat.txt`) while the live diff has grown
   — so `UNASSIGNED: 0` is **true by construction**. ⇒ **measure from git, and exit rather
   than fall back.**
2. **An empty diff would report full coverage of nothing.** ⇒ **refuse explicitly.**

---

## 4 · What the division produces, per scope

| artefact | who writes it | what it is |
|---|---|---|
| `DIVISION.md` | orchestrator | the groups, the counts, **how the division was made so it can be re-run rather than re-argued**, and the coverage proof |
| `diff/<G>.diff` · `manifests/<G>.md` | orchestrator (emitter script) | the per-group slice — **this is how a big diff is managed in parts** |
| `hub/briefs/<G>.md` | orchestrator | the assignment, boundaries, resources, rulings |
| `hub/RESEARCH-MAP.md` | orchestrator | ⭐ **one row per scope pointing at what already exists** — reference study, parity ledger, prior dissections, mockups, earlier rounds |
| `findings/<G>.md` | ⭐ **the lane** | its dossier — §0 the vision (verbatim), the units, the two work lists, the evidence |
| `TRACKER.md` | orchestrator | the programme rollup, one row per scope |

⛔ **A lane that re-derives what is on disk has spent its budget on the one thing that was never
missing.** ⚠ Mark anything written at an older revision as **orientation only, never a citation.**

---

## 5 · ⭐ THE ADVERSARIAL PASS ON THE DIVISION ITSELF

**A file partition built to catch seam defects is itself a place a seam defect can hide.** Run this
before opening the first lane; it typically finds several problems, some real:

1. **Does any group hold the ORACLES for another group's subject?** *(Tests ride with their subject —
   a guard that tests someone else's code must at least be `ALSO`-read by them.)*
2. **Does every overlap stated in PROSE exist in the MECHANISM?** *(A group's brief that promises it
   reads files the classifier gave it no rule for is a claim the code does not honour.)*
3. **Is any large test file primary in a different group from the module it imports?**
4. **Does any generated / cross-cutting artefact have a one-group review and a four-way seam?**
5. **Do the unit counts in every document agree?** *(Documents drift into different counts, none
   of them right.)*

---

## 6 · Tracking — two levels, and they must not duplicate

| level | lives in | owner | granularity |
|---|---|---|---|
| **Per-item** | the lane's own dossier | ⭐ **the lane** | every item: `OPEN → FIXED / NO-FIX / REFUTED → PROVEN` |
| **Programme rollup** | `TRACKER.md` | ⭐ **the orchestrator** | one row per scope, updated at every handover and ruling |

⛔ **"Done" in the rollup means LANDED on the shipping branch** — never "the lane edited it". Work
sitting on a lane branch is *in-lane*. **That distinction is the whole reason the rollup exists.**

⭐ **Report STATES, not fractions.** `0 / 8` makes *specified but not built* look like *not started* —
a reporting defect that reads as "this lane has done nothing" when it has fully specified every
item with cited code. Use `SPECIFIED · IN PROGRESS · IMPLEMENTED · PROVEN · NO-CHANGE`.

⛔ **And the lane must PRESENT its table in its reply to the owner** — a table written to a file does
not reach the owner. **The owner steers from these.**
