# The hub contract — ownership, post formats, the progress table, the verdict

**The hub is the centralized surface.** Every lane reads the plan from here, posts its updates here,
reaches the orchestrator here, and sees the other lanes here.

---

## 1 · ⛔ ONE WRITER PER FILE — the table that prevents lost writes

| file | the ONLY writer | everyone else |
|---|---|---|
| `hub/README.md` · `ORCHESTRATOR.md` · `SIGNAL.md` · `LANES-LIVE.md` · `RESOURCES.md` · `MERGE-TRAIN.md` · `RESEARCH-MAP.md` | ⭐ the orchestrator | read |
| `hub/briefs/<id>.md` — assignment, boundaries, rulings | ⭐ the orchestrator | **that lane reads it** |
| `hub/lanes/<id>-ORDERS.md` — the live wire to that lane | ⭐ the orchestrator | **that lane reads it** |
| ⭐ `hub/lanes/<id>.md` — status, progress, posts, handover | ⭐ **that lane, and NOBODY else — not even the orchestrator** | everyone reads |
| `hub/lanes/<L>/sub/<S>.md` | ⭐ **that sub-lane** | its lane reads |
| `hub/lanes/<L>/briefs/<S>.md` | ⭐ **that lane** (it is the sub-lane's orchestrator) | that sub-lane reads |
| ⭐ the lane's own dossier / findings / probe tables | ⭐ **that lane** | everyone reads |
| ⛔ `TRACKER.md`, parity / routing ledgers, CI count pins *(optional programme feature, off by default)*, the PR body, the classifier | **the orchestrator only** | lanes hand over ROWS |
| ⛔ `sealed/**` — the blind review seal *(optional programme feature, off by default; only if the programme turns a blind protocol on)* | **nobody writes** | read only after your own filing is final |

⚠ **Why the brief/lane split exists — it prevents lost writes.** An orchestrator that appends to
a lane's file **while that lane is mid-write on it** causes a collision. ⇒ all
orchestrator-authored content lives in `briefs/` and `*-ORDERS.md`.

⭐ **This table governs `hub/` only. A lane's own dossier grows without limit** — read the rule so
literally that the scope stops having a record and you have made it worse.

---

## 2 · How to post

Append to the **bottom** of your own `hub/lanes/<id>.md`, newest last. One block per post:

```markdown
### YYYY-MM-DD HH:MM · BLOCKER · to: ORCHESTRATOR
**What** — one sentence.
**Evidence** — the command and its output, or `file.ext:NN`. Never an assertion alone.
**Blocked** — what stops until this is answered. Omit if nothing stops.
```

**Kinds:** `STATUS` (routine progress) · `QUESTION` (needs an answer, not blocking) ·
`BLOCKER` (work stops) · `CROSS-LANE` (belongs to another scope's files) · `HANDOVER` (change set
ready) · `VERDICT` (the closing artefact) · `FYI` · `LIVE-APP REQUEST` / `GRANTED` / `RELEASED`.

**`to:`** is `ORCHESTRATOR`, another lane, or `ALL`.

⭐ **Evidence, not conclusions.** *"`guard.decide("Write", {"file_path": ".env"}) → allow`"* beats
*"the guard looks weak."* **The strongest finding a review produces arrives as an executed predicate.**

---

## 3 · ⭐⭐ CROSS-LANE FINDINGS — the case the hub exists for

You **will** find real defects in files your scope does not own. **Defects in a seam-shaped
project often live between two files.**

⛔ **Do not fix it. Do not stay silent either.**

1. **File it in your own dossier** with full evidence — you found it, it is yours to have found.
2. **Post `CROSS-LANE`** naming the file and the scope you believe owns it.
3. **The orchestrator routes it** — ownership decided by the classifier's rule order, **never by
   hand** — and records it in the rollup's cross-lane table.

⭐ **Following a call out of your own files is correct. Fixing what you find there is not.**

---

## 4 · ⭐ THE PROGRESS TABLE — kept at the TOP of your file, rewritten IN PLACE

⛔ **It is state, never history. Do not append a new copy each time.**
⛔⛔ **AND PRESENT IT IN YOUR REPLY TO THE OWNER — writing it to a file does not reach the owner.** The owner steers
a whole programme from these. Every substantive reply ends with the table plus one line of what you
are doing next and what is blocked.

```markdown
## Progress — <scope> · updated <date time> · <phase>

| severity | open | fixed | no-fix | refuted | total |
|---|--:|--:|--:|--:|--:|
| 🔴 Critical    |  |  |  |  |  |
| 🟠 High        |  |  |  |  |  |
| 🟡 Medium      |  |  |  |  |  |
| ⚪ Low         |  |  |  |  |  |
| · Nit          |  |  |  |  |  |
| 💡 Improvement |  |  |  |  |  |
| **TOTAL**      |  |  |  |  |  |

**Gates:** N of M run · **Live drives:** N · **New rows this round:** N

| the SECOND work list — vision / additions | n |
|---|--:|
| ⭐ **SPECIFIED** — designed, the code is cited | |
| **IN PROGRESS** | |
| **IMPLEMENTED** — in my worktree | |
| **PROVEN** — seen working in the running app | |
| `NO-CHANGE` — already aligned, argument written | |
| **TOTAL** | |
```

⛔⛔ **THE SECOND LIST MUST HAVE STATES, NOT A FRACTION.** *A lane's `0 / 8`
reads as "no improvements" when every item is fully specified with cited code.* **A fraction makes
*specified but not built* indistinguishable from *not started* — that is a reporting defect, not a
progress one.**

⭐ **Count `NO-FIX` and `REFUTED` separately and never merge them into "closed."** `REFUTED` means it
was not real; `NO-FIX` means it **was** real and was correctly left alone with the argument written
down. Collapsing them destroys the only record of that judgement.

⚠ **A rising count is healthy.** Findings rise while you look and fall while you fix. ⛔ **A number
that only ever falls is not being measured — it is being managed.**

⭐ **Derive the table from your dossier, never from memory.** A table counted from a lane's own edits
rather than its dossier will be wrong.

---

## 5 · ⭐⭐ THE FIX GATE — twelve checks on every fix batch

**Before you edit:**

| # | check |
|---|---|
| **0a** | ⭐⭐ **INVESTIGATE, don't pattern-match.** For every Critical and High: **execute the vector**, read the surrounding module, and read the **reference implementation.** ⭐ *A cold read of your own code concludes "this is good."* |
| **0b** | ⭐⭐ **RESEARCH THE IMPACT before choosing the fix.** Who else calls this? What does the frontend do with the value? **What does the USER see?** The obvious fix is often the wrong one |
| **0c** | ⛔ **PROVE IT IS REAL, and accept `NO-FIX`.** Most Critical/High candidates do not survive skeptical verification. Most findings die |

**After you edit:**

| # | check | how |
|--:|---|---|
| 1 | ⭐ **RED FIRST** | the arm must **FAIL** before your fix. ⛔ The single most-skipped check |
| 2 | **GREEN AFTER** | same arm passes |
| 3 | **SCOPED SUITE** | your scope's tests, pinned interpreter by ABSOLUTE path |
| 4 | ⛔ **NO ASSERTION DELETED** | `git diff -U0`. **Removing coverage makes a suite greener and the code worse** |
| 5 | **IN PLACE** | ⭐ **re-read the file after editing. An edit you did not re-read did not happen** |
| 6 | **FOCUSED** | `git diff --stat` — every path inside YOUR scope |
| 7 | **NOTHING NEW BROKEN** | full suite vs the pins |
| 8 | **LIVE** | if a user could see it, **drive it** — ⛔ restart the backend first |
| 9 | **PUSH** | the orchestrator integration-checks every push |
| **10** | ⭐⭐ **ARCHITECTURE STILL ALIGNED** | does the fix sit where that concern **belongs**? ⛔ *A fix nothing calls is the defect this class keeps producing* — prove the wired path is reachable |
| **11** | ⭐⭐ **VISION STILL ALIGNED** | ⛔ **the vision is the correctness oracle** — a fix that passes every test and contradicts it is not a fix |
| **12** | ⭐⭐ **NO LOGIC BROKEN** | name what the code did **before** and what it does **now**, and confirm the difference is **only** the defect. ⛔ Default a new predicate to the **old behaviour** so no existing caller changes |

⛔ **Then re-review the WHOLE SCOPE once the fixes are in — not each fix alone.** The usual
failure mode is fixes interacting.

---

## 6 · ⭐⭐⭐ BEFORE YOU HAND OVER — the eight self-review checks

> ⭐ **The principle:** each lane does its own review of its own work before handing it off, like an
> employee–manager relationship, so that the manager does not find a lot of issues in it.

⛔ **A handover that has not been self-reviewed is sent back unread.** The manager's review is the
**second** line of defence, never the first — the lane has the context, the manager never will at
that depth.

| # | |
|---|---|
| 1 | **Every item carries an outcome** — `FIXED` · `NO-FIX` (real, correctly left alone, **argument written**) · `REFUTED`. ⛔ Nothing left `OPEN` |
| 2 | **Each was investigated alone and demonstrated real BEFORE any edit** |
| 3 | ⛔⛔ **The WHOLE SCOPE was re-read after the changes — not the diff.** *Did I introduce anything new; did every change actually land* |
| 4 | **Suites green in the lane's OWN worktree**, with the shared pins still holding |
| 5 | **Load-bearing guards mutation-checked to red** — a guard that cannot fail is not a guard |
| 6 | **Driven live** if the surface is drivable — ⛔ announce before driving |
| 7 | **The second work list (vision / additions) is also at zero** |
| 8 | ⭐ **It states what it did NOT cover.** An honest gap costs one line; a hidden one costs a round |

### ⭐ MAKE THE WORK DURABLE — push continuously, tag at handover

```bash
git push -u origin <lane-branch>            # OFTEN, not once at the end — insurance, never a merge request
git tag handover/<scope>/r<k> && git push origin --tags     # freeze the exact tree handed over
```

**Then post `HANDOVER`** with: the **tag** · the `git diff` range · the dossier · the evidence
(suite output, screenshots, drive log) · a proposed commit message · **the test-count delta** ·
and the self-review result.

---

## 7 · ⭐⭐⭐ THE FINAL VERDICT — the artefact that closes a scope

> ⭐ **The principle:** each lane verifies its own work and provides the final verdict: final, completed,
> zero-error, properly improved, fixed, and vision-aligned.

⛔ **A scope is not closed by saying "done." It is closed by this verdict, and every line is
falsifiable.** Post it in your hub file **and repeat it in your reply to the owner.**
⛔ **If you cannot sign a line, say so and name what is missing — an unsigned line is not a failure;
a falsely signed one is.**

```markdown
### <date time> · VERDICT · <scope> · to: ORCHESTRATOR (cc OWNER)

| # | claim | signed? | the evidence |
|:--:|---|:--:|---|
| 1 | **COMPLETE** — every item carries a verdict, none left OPEN | ☐ | N = X FIXED + Y NO-FIX + Z REFUTED |
| 2 | **ZERO-ERROR** — no defect I know of is unresolved | ☐ | every NO-FIX has its written argument; C/H NO-FIX agreed by the orchestrator |
| 3 | **FIXED** — the fixes actually landed and work | ☐ | per fix: the arm that was RED is GREEN, and it was red first |
| 4 | **IMPROVED** — the nuances, not just the bugs | ☐ | what got better that no finding demanded |
| 5 | ⭐ **VISION-ALIGNED** — it looks, behaves and works as the vision describes | ☐ | per clause: the change, and how it was seen |
| 6 | **PROVEN** — suites green, guards can fail, driven live | ☐ | counts · the mutations that turned arms red · the drive log |
| 7 | ⛔ **RE-REVIEWED WHOLE** — nothing new introduced | ☐ | what the re-read found, including nothing |
| 8 | ⭐ **WHAT I DID NOT COVER** | — | say it plainly |
```

⚠ **The verdict is the lane's to sign, and the orchestrator's to gate.** ⛔ **A verdict the
orchestrator cannot reproduce goes back to the lane, not forward to the owner.**

---

## 8 · ⛔ AN EMPTY LIST IS NOT A CLEAN BILL — it is a list

A lane's list can go empty and the next sweep still produce something —
including a permanent silent failure found after the scope looked finished.

⇒ ⭐ **The exit condition is TWO CONSECUTIVE PASSES THAT FIND NOTHING NEW. Any edit resets the
counter.** ⛔ **Do not declare on an empty list.**
