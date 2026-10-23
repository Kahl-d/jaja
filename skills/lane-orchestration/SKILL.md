---
name: lane-orchestration
description: "Use whenever work on any project is split across more than one session: starting or resuming an orchestrator, a lane or a sub-lane; before addressing another session; before handing work between sessions or integrating a lane's change set; any time two sessions could write the same file."
argument-hint: "[orchestrator | lane <scope> | sub-lane <part> — defaults to inferring the role from the hub]"
---

# Lane orchestration — one manager, many owners

**The standing architecture for any multi-session work in this workspace.**

> ⭐ **The principle:** The orchestrator is the manager of any project; each lane is the owner and
> responsible for any work it does, whether implementation, review or any issue. A lane does its own
> research, understands the whole vision, interacts with the orchestrator, posts the right questions,
> reviews its own work, and always gives the work back when it is fully reviewed and properly checked.

⛔ **This is not only for reviews.** Implementation, migration, audit, research — anything split
across sessions runs this way.

---

## 0 · ⭐⭐⭐ START HERE — the commands, and the reads

**Starting a programme?** `/orchestrate <name>` (via `scripts/kernel/new-programme.sh`) scaffolds the hub from
`templates/orchestration/`, divides the work by mechanism, and hands you a starter prompt
per lane. ⛔ **You do not hand-build any of this, and you do not invent the connection procedure.**

**Resuming, in any role?** Where the hub has `lanes/<id>-STATE.md` and `events.jsonl` (the job agency does): read your STATE file (live state plus the live end of your ORDERS) and the events your start hook
printed; grep your own log, never read it whole. Otherwise read in this order: `hub/README.md` (the contract) →
`hub/SIGNAL.md` (is my scope named?) → my own file → `hub/lanes/<id>-ORDERS.md` if I am a lane →
`TRACKER.md` (the rollup).

| I am | my command | I own |
|---|---|---|
| the orchestrator | `/orchestrate` | the plan · the division · the gate · the integration · **all git** |
| a lane | `/lane <scope>` | ONE scope, end to end, as its **owner** |
| a sub-lane | `/lane sub <part>` | one PART inside a lane's scope; hands back **to its lane** |

⛔ **Read the reference before doing the thing, not after it went wrong:**

| file | when |
|---|---|
| ⭐⭐ [`references/roles.md`](references/roles.md) | ⛔ **at the start of every session.** WHO you are — the manager's four lenses and nine jobs, or the specialist's six duties. **Paste it into the opening context** |
| ⭐⭐ [`references/messaging.md`](references/messaging.md) | ⛔ **before addressing ANY other session.** The connection protocol and the failure modes to avoid |
| [`references/hub-contract.md`](references/hub-contract.md) | file ownership · post formats · the progress table · the FINAL VERDICT |
| [`references/division.md`](references/division.md) | how a body of work becomes groups, units, parts and flow tracers |
| [`references/resources.md`](references/resources.md) | the shared resource — a dev server port, a device, a browser profile, a connector quota, a live app — one holder, turn-taking, lend-and-return |
| [`references/merge-train.md`](references/merge-train.md) | the nine-step merge protocol, the gate, rebase proof, the abort path |
| [`references/starters.md`](references/starters.md) | the starter prompt, the brief and the ORDERS templates that worked |

---

## 1 · The three levels

```
ORCHESTRATOR  ── the manager. One session. Owns the goal, the division, the gate, ALL git.
     │            Does not do the work. Makes N lanes add up to a shipped thing.
     │
     ├── LANE ── worker AND OWNER of one scope. Researches, investigates by EXECUTION,
     │      │     reviews, fixes, reviews its own fixes, delivers a FINAL VERIFIED VERSION.
     │      │     Own worktree, own branch, own hub file. Never commits on the shipping branch.
     │      │
     │      └── SUB-LANE ── one PART inside that lane's scope, when the scope is too big
     │                      for one context. The LANE is its orchestrator; it hands back to
     │                      the LANE, never to the orchestrator. The lane still makes ONE
     │                      handover upward. See §5c.
     └── LANE ...
```

⭐ **Levels are about context capacity, not hierarchy of trust.** A sub-lane owns its part exactly
as completely as a lane owns its scope.

### ⭐⭐⭐ ONE ARCHITECTURE, EVERY PHASE — this is not a review pattern

> ⭐ **The principle:** the same structure serves every phase of work. Plan with multiple agents through an
> orchestrated process; once planning is done, implement in the same fashion, in parallel and in parts; review
> also in parts.

⛔ **The roles, the hub, the registry, the messaging, the gate and the merge train do not change
between phases. Only the unit of work and the deliverable do.**

| phase | a lane owns | its deliverable | what the orchestrator integrates |
|---|---|---|---|
| ⭐ **PLAN** | one area of the design — research it, derive options, cost them | a **sub-plan** with its open questions and the evidence behind each choice | one coherent plan; ⛔ **it resolves the CONFLICTS between areas** — that is the whole value |
| ⭐ **IMPLEMENT** | one part / layer / module | a reviewed, evidenced **change set** + its tests, driven live | **one commit per scope**, gated on the integrated tree |
| ⭐ **REVIEW** | one group of the surface | findings, each `FIXED` / `NO-FIX` / `REFUTED`, plus the vision list | the same |
| **MIGRATE · AUDIT · DOCUMENT · RESEARCH** | one slice | the slice, finished and proven | the same |

⭐ **In every phase the lane is the OWNER and delivers a FINAL VERIFIED VERSION.** ⛔ A planning
lane that hands back options for someone else to choose between has not owned its area — it owes a
**recommendation with its cost**, and the questions that are genuinely the owner's.

### THE ORCHESTRATOR — manager · product manager · owner of the goal

**It does not do the work. It makes the work of N lanes add up to a shipped thing.**

| owns | meaning |
|---|---|
| **The plan** | what opens, in what order, from measured dependencies — not preference |
| **The division** | who owns which file; the ruling when two ledgers disagree |
| **The assignment** | each lane's brief, boundaries, resources, scope |
| ⭐ **The gate** | ⛔ **re-runs the lane's load-bearing claims itself.** A verdict it cannot reproduce goes back to the lane, never forward to the owner |
| **The integration** | apply → gate on the **integrated** tree → re-review → commit |
| **All git** | every commit and push, in the owner's authorship, ⛔ per the owner's attribution rules, only on the owner's word |
| ⭐ **The goal** | ⛔ **the thing no lane is holding: does this add up to the thing shipping?** |

⛔ **It is NOT** a reviewer of a lane's code, a second opinion, or a permission desk.
⭐ **If a lane is asking whether to keep going, the answer is yes.**

### A LANE — a worker, and the OWNER of its scope

⛔⛔ **Not executing a ticket list. Owning a component of something that is shipping.**

| owns | meaning |
|---|---|
| **UNDERSTANDING** | the reference, the current code, the seams, **what it is FOR in the user's terms** |
| ⭐ **INVESTIGATING** | ⛔ **run the predicate, mutate the guard, drive the app.** A reasoned finding is a hypothesis; an executed one is a finding |
| **REVIEWING** | including **re-reviewing what was handed to it** — verify, do not accept |
| **FIXING** | root causes not symptoms; the smallest change that closes the cause; an arm that was **red first** |
| ⭐ **REASONING** | judgement is the lane's. Disagreeing with the orchestrator **in writing** is the job, not a risk |
| ⭐ **DELIVERING** | ⛔ **a FINAL VERIFIED VERSION**, never "my changes are in" |

⭐⭐ **OWNERSHIP MEANS NOBODY COMES BACK.** Marking something done is a promise that no one has to
reopen it — not the orchestrator, not the owner, not the PR reviewer. **That promise is the job.**

---

## 2 · ⛔ THE ONE MECHANICAL RULE — one writer per file, no exceptions

Several sessions write at once. Two sessions appending to one file **lose each other's writes.**

| file | the ONE writer |
|---|---|
| `hub/README.md` · `hub/ORCHESTRATOR.md` · `hub/SIGNAL.md` · `hub/briefs/*` | ⭐ **the orchestrator** |
| `hub/lanes/<id>.md` | ⭐ **that lane, and NOBODY else — not even the orchestrator** |
| the lane's own working artifacts (dossier, findings, probes) | ⭐ **that lane** |
| shared ledgers (trackers, parity, routing, CI pins — CI count pins are an optional programme feature, off by default) | **the orchestrator only** — lanes hand over rows |

⚠ **A collision is easy to cause:** an orchestrator that appends to a lane's file *while that lane is
mid-write on it* loses one side's writes. ⇒ **orchestrator content lives in
`briefs/<id>.md`; the lane's file is the lane's alone.**

⭐ **Lane → lane still works:** read their file, post in yours, addressed to them. The orchestrator
routes. That is the manager's job, not a limitation.

---

## 3 · The hub — the only channel that is reliable

```
<work-root>/hub/
├── README.md         the contract — roles, rules, lifecycle, handover      [orchestrator]
├── ORCHESTRATOR.md   broadcasts, standing state, answers owed to lanes     [orchestrator]
├── SIGNAL.md         ⭐ ONE TABLE: which lanes have something waiting      [orchestrator]
├── briefs/<id>.md    assignment, boundaries, rulings                       [orchestrator]
└── lanes/<id>.md     status, progress table, posts, handover               [that lane]
```

⛔⛔ **THERE IS NO PUSH, AND THIS IS THE COSTLIEST FACT IN THE ARCHITECTURE.**
A write to a file reaches a lane only when the lane next looks. Messages between sessions do work (§11), but
only to an address that is verified; never assume a channel works without checking it.

⇒ **A ruling reaches a lane when the lane next looks.** The cost of not looking is idle time on questions
that were already answered.

⭐ **`SIGNAL.md` is the mitigation:** one tiny table naming which lanes have something waiting.
Lanes poll it at every phase boundary and gate close — cheap to read, so it gets read. Where the hub keeps
`events.jsonl` (`scripts/kernel/hub_events.py`), the poll is `hub_events.py since --lane <id>` when a lane claims
work: one line per event, no re-reading of old tables.

⚠ **And the human is the only real-time link.** When a lane is genuinely blocked it posts the
blocker **and** says so in its reply to the owner, who is in every room.

---

## 4 · ⛔⛔ A SESSION HALTS BETWEEN TURNS — but a MESSAGE wakes it

**A session works a turn, then HALTS.** ⛔ **A file does not wake it. `SIGNAL.md` does not wake it.
A commit does not wake it.** ⇒ every turn a lane ends, it sleeps until something arrives.

⭐⭐ **What DOES wake it: a cross-session message.** *"When the receiving session is idle, Claude
Code starts a new turn with the message."* ⇒ ⭐ **the orchestrator restarts a stalled lane itself.**
Long idles happen when everyone believes otherwise and waits for the owner to relay.
⛔ **Waiting for the human to carry a message the orchestrator could send is the failure.**

**Both halves still bind:** the orchestrator messages a lane that has stopped — and a lane chains
its work so it does not stop needlessly.

⭐ **So the single highest-leverage instruction to any lane is: DO MORE PER TURN.**

| ⛔ do NOT end a turn because | ⭐ instead |
|---|---|
| a unit of work closed | start the next one **in the same turn** |
| a fix landed | take the next item |
| you want confirmation you are on track | you are — ask only what your brief names as blocking |
| it feels like a natural stopping point | ⭐ **that is exactly when to keep going** |

⚠ **Design the brief for this.** A brief written as *discrete units with exit criteria* produces a
lane that stops at every boundary. Give each unit a **self-clearing exit test** and say explicitly:
**chain them.** *(A lane whose brief reads as stop-and-report stops at every gate; a lane given an open-ended
queue keeps running.)*

⛔ **The only legitimate stops:** a real full stop the process defines · a blocker the brief names ·
a decision that is the human's · context genuinely exhausted. **"I finished a thing" is not one.**

---

## 4b · The lane lifecycle — five phases, in order, no skipping

```
  A · RESEARCH & REFERENCE   read what exists · study the reference · ledger every capability
        ↓                     ⛔ do not re-derive what RESEARCH-MAP.md already points at
  B · REVIEW / ANALYSE       every lens × every unit · execute predicates, never reason
        ↓
  C · ⛔ FULL STOP           document everything · hand the COMPLETE analysis back · STOP
        ↓                     ⛔ no fix is written before this
  D · IMPLEMENTATION MODE    fix · improve · complete · align to the vision
        ↓                     governed by the `implementation-mode` skill
  E · GATES & HANDOVER       per-item gate · whole-scope re-read · suites · guards to red ·
                             live drive · the 8 self-review checks · the FINAL VERDICT
        ↓
       ORCHESTRATOR integrates → ONE commit per scope → the shipping branch
```

⛔ **Phase D loads `implementation-mode`, with ONE override: that skill puts one commit per PART;
under lane orchestration it is ONE COMMIT PER SCOPE, made by the orchestrator only.** ⇒ **the
tracking carries the granularity the commit no longer does** — every item tracked individually
`OPEN → FIXED / NO-FIX / REFUTED → PROVEN` in the lane's own dossier.

### ⭐ NO VISION, NO WORK

⛔ **If the scope has no vision yet, the lane's FIRST act is to explain the scope back to the owner** —
in the user's terms, its sub-features, its seams, what it inherits, and ⭐ **the questions only the owner
can answer** — **and then STOP.** The vision is the correctness **oracle** *and* a **source of work**:
a round has two work lists (the findings, and the vision-derived additions / improvements /
changes) and closes only when **both** are empty. A group is not done at zero findings.

⭐ **Pre-vision, a lane gathers FACTS but renders no VERDICTS.** Reading the reference, mapping
seams, filling a capability ledger is allowed and safe — **facts survive a change of oracle.**
Writing a finding, assigning a severity, or making a fix is not. The hazard is anchoring, and
anchoring requires a verdict.

### ⭐⭐ THE PER-ITEM LOOP — one item at a time, not batched

```
① INVESTIGATE  read it alone. What is the actual mechanism? ⛔ do not open the editor yet
② PROVE        demonstrate it is REAL — run the predicate, mutate the guard, drive the app
③ DECIDE       ⭐ FIXED · NO-FIX (real, correctly left alone, argument written) · REFUTED
④ FIX          only if ③ says a fix is required. Smallest change that closes the CAUSE
⑤ REVIEW THE FIX  ⛔ the step everyone skips — red-first? coverage removed? sibling broken?
⑥ RECORD       the row moves, with its evidence → PROVEN once re-tested and driven
⇒ next item, IN THE SAME TURN
```

⛔ **Do not batch ten fixes then test.** ⭐ **Order by ROOT CAUSE, not by item id** — four root
fixes retire eight rows; eight individual fixes create a ninth.
⛔ **And after the loop, re-read the WHOLE SCOPE — not the diff.** Per-item review catches what one
edit broke; only re-reading the scope catches what the round as a whole removed.

---

## 4c · ⭐ SUB-LANES — when one scope is bigger than one context

**Open a sub-lane when a lane's scope has a PART that is genuinely separable and large enough that
holding both starves one of them.** ⛔ Not to parallelise for its own sake.

| | |
|---|---|
| **Who opens it** | ⛔ **the owner**, at the LANE's request — the same rule as lanes (§8). The lane writes the starter; the owner starts the session |
| **Who orchestrates it** | ⭐ **the LANE.** It writes `hub/lanes/<L>/briefs/<S>.md`, gates the sub-lane's claims, routes cross-part findings |
| **Where it writes** | `hub/lanes/<L>/sub/<S>.md` — that sub-lane alone. **One writer, recursively** |
| **Its branch** | a scratch branch off the LANE's branch (`<lane-branch>-<part>`), its own worktree |
| **How it hands back** | ⭐ **to its LANE, never to the orchestrator** — a reviewed, evidenced change set with its own FINAL VERDICT |
| **Who integrates it** | ⭐ **the LANE**, onto the lane branch, after re-running its load-bearing claims itself |
| **Its shared resource** | ⛔ through the LANE, which holds the lane's grant. A sub-lane never requests a shared resource directly |
| **Its address** | same protocol as a lane (§11) — it reads its own registry name and writes it into its sub-lane file |

⛔⛔ **The orchestrator still sees exactly ONE handover per lane.** Sub-lanes are invisible to the
merge train — if the orchestrator has to reason about a sub-lane, the level was not real.
⭐ **The lane is fully accountable for its sub-lanes' work.** *"The sub-lane said so"* is not evidence.

⚠ **Two levels is usually enough. A third is a signal the division was wrong** — re-cut the groups
before nesting again.

---

## 5 · The handover contract — what "give the work back" means

⛔ **A handover that has not been self-reviewed is sent back unread.** The manager's review is the
**second** line of defence, never the first — the lane has the context, the manager never will at
that depth.

**Before posting `HANDOVER`, all of this is true:**

| # | |
|---|---|
| 1 | every item has an **outcome** — done · **deliberately-not-done, argued in writing** · withdrawn. ⛔ Nothing left open |
| 2 | each was **investigated alone and demonstrated real** before any edit |
| 3 | ⛔⛔ **the WHOLE scope was re-read after the changes** — not the diff. *Did I introduce anything new; did every change actually land* |
| 4 | suites green **in the lane's own worktree**, with the shared pins still holding |
| 5 | load-bearing guards **mutation-checked to red** — a guard that cannot fail is not a guard |
| 6 | **driven live** if the surface is drivable |
| 7 | the second work list (vision / additions) is also at zero |
| 8 | ⭐ **it states what it did NOT cover.** An honest gap costs one line; a hidden one costs a round |

⭐ **Then the FINAL VERDICT** — falsifiable lines the lane signs: complete · zero-error · fixed ·
improved · aligned · proven · re-reviewed whole · **and what was not covered.** ⛔ A line that cannot
be signed is named, not skipped.

---

## 6 · Integration — durability, and why a commit is a certificate

⛔⛔ **A COMMIT IS NOT TRANSPORT. It is the certificate that the gate passed** — and a lane cannot
produce it, because a lane cannot run the gate on the *integrated* tree.

**Five layers, so nothing depends on a session staying alive:**

| # | layer | who |
|:--:|---|---|
| 1 | lane WIP history on its own scratch branch | lane |
| 2 | ⭐ **that branch pushed to the remote, continuously** — insurance, ⛔ never merged | lane |
| 3 | ⭐ an **immutable tag** at handover — the exact tree being handed over | lane |
| 4 | a **pre-integration tag** on the shipping branch — every integration reversible | orchestrator |
| 5 | **one commit per scope**, after the gate and the human's approval | orchestrator |

**The loop:** `git diff` → `apply --3way` → **orchestrator reviews the applied diff** → **full gate
on the INTEGRATED tree** → live drive → re-review the whole scope → **two approvals** → one commit →
push → **announce the new tip; lanes rebase onto it.**

⛔ **Never a merge commit** (it breaks a linear single-author branch and destroys per-scope
attribution). ⛔ **Never a lane fast-forward** (it injects WIP history and skips the gate).
⛔ **Pairwise-clean is not clean** — the gate runs on the merged tree; neither side's own green counts.

---

## 7 · The traps

| ⛔ trap | ⭐ the rule |
|---|---|
| A verification predicate satisfied by **prose about** the thing | ⛔ **a predicate must be satisfiable ONLY by the thing itself.** Clearing a flag uses **the predicate that failed, re-run** — never a newer, more convenient one |
| A capture that cannot reproduce itself | ⭐ **a probe may be deleted; its OUTPUT may never be.** Capture into the durable record **in the same turn** — a deleted file plus a turned-over context is indistinguishable from work never done |
| A test asserting **source text** instead of behaviour | it passes while the behaviour is gone. Mutate the production use and watch for red |
| A **vacuously green** assertion | if no input can make it fail, it pins nothing. Plant the case it claims to cover |
| A **zero-finding file** read as a clean bill | ⭐ ask which files *generate or gate* what ships, and read those regardless of how quiet they are |
| Batching many changes then testing | the loop **diverges**: each batch of fixes creates new findings, many self-inflicted. One item at a time, review each fix |
| Fixing where an issue was **filed** | its cause is often elsewhere. **Root causes first** — four root fixes retire eight rows; eight individual fixes create a ninth |
| A green suite as proof | ⛔ **the live drive has caught what suites could not, in every part.** A suite can be fully green while tools are broken |
| Reporting "closed / total" | ⭐ it makes *specified but not built* look like *not started*. Report **states**, not fractions |
| A shared machine resource | announce → use → release, one at a time, and **read the other lane's file first** |

---

## 8 · Starting a lane — the handshake

⛔ **The orchestrator never starts a lane.** The owner does, sometimes on a different account.

1. The owner says: *"start a lane."* Agree the scope.
2. ⭐ The orchestrator hands the owner **① a self-sufficient starter prompt** and **② a startup checklist** —
   understand the project · understand the vision · understand the process and the roles · the scope
   assigned · ⭐ **what is already researched, so it is not re-derived.** Template:
   [`references/starters.md`](references/starters.md). ⛔ The lane must not need a second message to begin.
3. The owner opens the session and pastes the starter.
4. ⭐⭐ **The lane's FIRST action is `ListAgents`** — its own registry name is the first line of the
   output — **and it writes that name, its worktree, its branch and its tip into
   `hub/lanes/<id>.md`.** ⛔ **That is the address. Renaming the session sets its TITLE, which is not
   an address** (§11). A rename is still worth doing for the owner's own navigation — it is just not
   the channel.
5. The orchestrator reads the hub, registers the lane in `LANES-LIVE.md` — **scope ↔ lane ↔ address ↔
   worktree ↔ branch ↔ resource** — and arms the monitor (§9).

⭐ **If the scope has no vision yet, the lane's FIRST act is to explain the scope back to the owner** —
in the user's terms, with its seams and the questions only the owner can answer — **and then stop** until the owner
gives the vision. ⛔ **No vision, no work.**

---

## 9 · Monitoring — detect, do not poll the human

⭐ Arm a background watch on the lanes' files and worktrees; react to events rather than asking.
⛔ **It detects; it cannot wake a lane.** Only the human can.

⭐ **What to actually check, not just that they are moving:**
**is the count coming down · are the RIGHT things being fixed · were they fixed correctly (arm red
first, nothing weakened, no coverage deleted) · is the investigation real (executed, not reasoned) ·
and does any of it add up to the thing shipping.**

---

# PART II — THE MECHANICS

Everything below is written from how the architecture runs in practice.

## 10 · Naming — sessions, branches, worktrees, slots

⭐ **One prefix per programme, so every surface is greppable and a stray session is obvious.**

| thing | pattern | example |
|---|---|---|
| orchestrator session | `<PROG>-ORCH`, launched with `claude --name` (references/messaging.md §0b) | `AR-ORCH` · `OS-ORCH` |
| lane session | `<PROG>-<SCOPE>` | `AR-PAYMENTS` · `OS-BRAIN` |
| lane branch | `<ticket>-<scope>` | `ar-payments` · `os-brain` |
| lane worktree | ⛔ **outside the project folder** — `~/tmp/<prog>-<scope>` | `~/tmp/ar-payments` |
| lane's hub file | `hub/lanes/<SCOPE>.md` | `hub/lanes/PAYMENTS.md` |
| orchestrator's brief | `hub/briefs/<SCOPE>.md` | `hub/briefs/PAYMENTS.md` |
| resource slot | `inst-N`, one per lane | `inst-2` → PAYMENTS |
| safety tags | `pre-<op>/<scope>-<date>` | `pre-rebase/ar-payments-<YYYYMMDD>` |

⛔ **Worktrees go outside the project folder.** Inside, their `node_modules` and build output wreck
every `Glob`/`Grep` the orchestrator runs.
⚠ **The owner renames the session; the orchestrator cannot.** It asks for the name and registers it.

## 11 · ⛔⛔ THE CONNECTION — the five rules. Full protocol + every failure: [`references/messaging.md`](references/messaging.md)

**Bidirectional messaging between sessions WORKS, but every obvious approach fails in a way that looks
like something else.** The five rules:

| # | rule | the failure it prevents |
|:--:|---|---|
| **1** | ⭐⭐ **A session's ADDRESS is its registry name, and it reads its OWN name off the first line of `ListAgents`.** Every session writes that name into the hub as its first act. | ⛔ **A session TITLE is not an address.** A starter that says *"rename this session to <name>"* sets the title only. Sends to titles are refused, and the channel can be wrongly believed dead. |
| **2** | ⛔ **Never write a registry name into a document as a durable fact — it is a CACHE with an expiry, re-stated by the owner.** | The name is auto-generated from the folder and unstable: one lane can show several different ids in a short time. *A fact recorded next to something that moves.* |
| **3** | ⛔⛔ **A refusal is trustworthy. A SUCCESS is trustworthy only if the row it resolved to was `interactive`.** Read the row before believing the send. | **Sends can return `success: true` and reach nobody** — a duplicate name can resolve to an **offline Remote Control** row. Absent name ⇒ loud refusal (safe). Present-but-dead ⇒ silent discard (the trap). |
| **4** | ⭐ **An address the OWNER supplies is authoritative; one the orchestrator INFERS is a guess that misroutes.** Reply by copying the `from` attribute of `<cross-session-message from="…">` verbatim. | Inferred addresses send replies to the wrong session without anyone noticing. |
| **5** | ⛔⛔ **ACCOUNTS ARE NOT AN AXIS OF REACHABILITY.** Same-machine delivery is a **per-session named pipe**, never Anthropic's servers, and discovery is **files on disk under one home directory**. ⇒ **same OS user + same home dir = reachable, on any accounts.** ⭐ Separate accounts are a pure win: separate rate limits, full messaging. | Believing *"registries are account-scoped, cross-account is impossible"* leaves a working two-way channel unused: a lane on a **different account and a different project folder** can still hold one. |

⇒ ⛔⛔ **DISK IS THE CONTRACT; A MESSAGE IS A COURTESY — but the message is what makes it READ.**

```
WRITE the ruling into hub/briefs/<scope>.md      durable · survives resumption · one writer
SEND  "ruling R-12 is in your brief"             wakes the lane · carries no state · costs nothing
```

⭐⭐ **And the correction that changes the architecture: A MESSAGE WAKES AN IDLE SESSION.**
*"When the receiving session is idle, Claude Code starts a new turn with the message."* Files do
not. ⇒ **the orchestrator drives the programme itself, instead of asking the owner to relay.**
⭐ `SendMessage({to, notify_when_idle:true})` subscribes to ONE notice when a session next goes
idle — ⛔ **never poll `ListAgents` in a loop, never send "are you done?".**

⚠ **A liveness claim inside a message is a MEMORY.** Re-measure before acting on one — interactive peers can vanish within minutes of being listed.

## 12 · Dividing the work — by mechanism, never by hand

⭐ **The partition is a script's output, not a judgement.** Its form is `classify.py`: an
ordered rule list mapping path → group, asserting **`UNASSIGNED: 0`**. Coverage is asserted
separately (`units.py`: `UNCOVERED: 0, AMBIGUOUS: 0`).

| principle | why |
|---|---|
| ⭐ **Disjoint by file** | two lanes must never be able to write the same path. This is what makes parallelism safe |
| ⭐ **Sized by risk, not by lines** | findings-per-file correlates **inversely** with safety: the biggest file draws many findings and still hides misses, while files that *generate what ships* draw none |
| ⛔ **A file partition cannot hold a seam** | every defect this branch produced lived *between* files ⇒ add a **second axis** of end-to-end flow tracers that cross group boundaries |
| **Order by dependency and risk** | what blocks others opens first; what holds a live exploit opens first |
| ⭐ **Scoped write grants over reclassification** | when one lane's work needs a file another owns: **primary stays put**, grant a *named, scoped* exception, and tell the owner when it opens. ⛔ Never edit the classifier to dodge a boundary |

⚠ **When two ledgers disagree about ownership, the orchestrator rules and records it.** A measurement
does not entitle it to reverse a call the human made — **surface it instead.**

## 13 · Sharing research — index it once, or every lane re-derives it

⭐ **`hub/RESEARCH-MAP.md`: one row per scope, pointing at what already exists** — the reference
study, the parity ledger, per-area dissections, design studies, prior review rounds, mockups.

⛔ **A lane that re-derives what is on disk has spent its budget on the one thing that was never
missing.** ⚠ Mark anything written at an older revision as **orientation only, never a citation.**

⭐ **Reference-first is a gate, not bookkeeping.** Two of five Criticals came from reading the
*reference implementation*, not our code: ours read as thorough — a missing `casefold` was invisible
until the reference's `part.rstrip(" .").casefold()` raised the question. ⭐ **A cold read of your own
code concludes "this is good."** ⚠ And it is not sufficient alone: a bare `NameError` came from pure
line-by-line reading. **Both are load-bearing.**

## 14 · Tracking — two levels, and they must not duplicate

| level | lives in | owner | granularity |
|---|---|---|---|
| **Per-item** | the lane's own file + its working artifacts | ⭐ **the lane** | every item, `OPEN → DONE / NOT-DONE / WITHDRAWN → PROVEN` |
| **Programme rollup** | the orchestrator's tracker | ⭐ **the orchestrator** | one row per scope, updated at every handover and ruling |

⛔ **"Done" in the rollup means LANDED on the shipping branch** — never "the lane edited it". Work
sitting on a lane branch is *in-lane*. **That distinction is the whole reason the rollup exists.**

⭐ **Report STATES, not fractions.** `0 / 8` makes *specified but not built* look like *not started* —
a real reporting defect that read as "this lane has done nothing" when it had fully specified eight
items with cited code. Use `SPECIFIED · IN PROGRESS · IMPLEMENTED · PROVEN · NO-CHANGE`.

⛔ **And the lane must PRESENT its table in its reply to the owner** — a table written to a file does not
reach the owner, who steers from these.

## 15 · ⭐ The starter prompt — the template that worked

⛔ **Self-sufficient. The lane must not need a second message to begin.**

```
You are the <PREFIX> <SCOPE> LANE for <programme> (<ticket>, branch <b> @ <sha>).
You own ONE scope: <scope> — <size>. A separate session, "<PREFIX> main lane", is the
orchestrator and owns ALL git.

FIRST: RENAME THIS SESSION TO   <PREFIX> <SCOPE> lane

YOUR DOOR: <hub>/README.md — read it first (roles, the write rule, the lifecycle,
the handover checks). Then <hub>/ORCHESTRATOR.md (directives), <hub>/briefs/<scope>.md
(YOUR assignment), <hub>/lanes/<scope>.md (YOUR file — you are its only writer).

STARTUP WORK — all of it, then report:
  1 the project    2 the vision (the correctness oracle, not background)
  3 the process    4 your scope    5 what is ALREADY researched — do not re-derive it

WORKTREE: git worktree add <path> -b <branch> <sha>; install; push the branch.
  Push it continuously — insurance, never a merge request.
  ALWAYS rebase from the integration branch, NEVER merge.
  NEVER touch the shared worktree.

RESOURCE: <slot> is yours and only yours. Announce before taking it; read the other
  lane's file first; release when done.

HARD RULES: you do NOT commit or push on the shipping branch - <the real full stop> -
  self-review before handover - write ONLY your own file.

HOW WE WORK: you are AUTONOMOUS inside your scope. Do not ask permission to think, probe,
  read, or disagree — if something here is wrong, say so with evidence. Ask only when
  genuinely blocked. Chain your work; do not end a turn because one unit finished.

FIRST MOVE: <the concrete first action>, then post STATUS with your plan, what you expect
  to yield most, and anything in this prompt you believe is wrong. Then <stop or continue>.
```

⭐ **Always include "anything in this prompt you believe is wrong."** A lane's first
post routinely finds errors in its own instructions — sometimes the orchestrator's own bad routing
ruling. **That question is why.**

## 16 · Interacting with a lane — the manager's own discipline

| ⭐ do | ⛔ do not |
|---|---|
| answer fast and specifically; keep an **answers-owed table** live | hover, or review work-in-progress |
| ⭐ **re-run its load-bearing claims yourself** before accepting | accept a verdict you cannot reproduce |
| accept correction plainly and **record the general rule** it exposes | defend a ruling that has been falsified |
| give it the *measured* results of other lanes — yields, traps, what paid | let each lane rediscover the same lesson |
| ⭐ rule the **scope** of its remaining work from evidence | demand a full checklist when measurement says some of it is dead weight |

⚠ **The orchestrator will be wrong in public, repeatedly:** a grant contradicting the ledgers · a false claim in a decision record · an ambiguity across documents · a flag cleared on
a proxy · a phase opened on an incomplete review. ⭐ **Each is caught by a lane and improves the
system. Write the correction down where the next lane will read it.**

## 17 · Setting it up — the order that works

⭐⭐ **`/orchestrate <name>` does steps 1–5 for you** — it scaffolds the hub from
`templates/orchestration/`, seeds the classifier, and writes the ledgers. Run it; do not
hand-build a hub. The order it follows, and what you still owe by hand:

1. ⭐ **Record your own address.** `ListAgents` → the first line is this session's registry name →
   write it into `hub/LANES-LIVE.md`. **Then ask the owner the account question** (§11 rule 5):
   same account ⇒ two-way messaging, shared rate limit; separate ⇒ file-only, separate limits.
   ⛔ **Do not decide it for the owner, and do not assert either answer without measuring.**
2. **Divide by mechanism** (§12) — a classifier script, not a hand list; assert full coverage.
3. **Build the hub** (§3) — contract, broadcast, signal, registry, resources, merge train, briefs.
   **One writer per file.**
4. **Index the research** (§13) so no lane re-derives what is already on disk.
5. **Write the rollup** (§14) and the lane ledger — scope ↔ lane ↔ address ↔ worktree ↔ resource.
6. **Hand the owner a starter prompt + checklist** (§15) for the first scope. ⛔ **The owner starts it.**
7. **Register the lane** when it writes its own address into the hub; **arm the monitor** (§9).
8. **Repeat per lane**, watching the shared resources and the collision list.

---

## 18 · ⭐⭐⭐ THE ANNOUNCEMENT PROTOCOL — how lanes work in parallel without colliding

> ⭐ **The principle:** the orchestrator and every lane follow the announcement strategy and check each other's
> work, so they do not interfere with each other and share context, and more work runs in parallel without
> messing up each other's work.

⛔⛔ **PARALLELISM IS NOT FREE. It is bought with announcements.** Two lanes are safe because each one
**declares before it takes** and **reads before it declares** — not because a rule said so.

### The four-step take, and step 1 is the one that matters

```
① READ    every other lane's file BEFORE you announce.
             ⛔ Not the ledger — THEIR FILE. The ledger lags; their file is now.
② ANNOUNCE in your own file, BEFORE you touch it:
             what · which slot/path · why it is safe · when you expect to release
③ USE     and while you hold it, keep your file current — a silent holder looks like a crash
④ RELEASE announce the release and leave the resource clean
```

⭐ **Announce BEFORE, not after.** An announcement after the fact is a log entry, not a protocol.

### What must be announced

| ⛔ announce | why |
|---|---|
| **any machine-global resource** — an app instance, a port, a device, a live service | the classic collision. One per lane, declared |
| **any shared mutable store** — a per-machine data dir, a cache, a fixture DB | ⭐ **two processes, one store, silent corruption** |
| **touching a file under a scoped grant** | the owner must be able to see it later |
| **a finding in another lane's files** | post `CROSS-LANE`; ⛔ **file it, do not fix it** |
| **a change to something everyone depends on** — a shared pin, a generated artifact, a contract | ⭐ **the blast radius is every lane, so it is the orchestrator's call, not yours** |
| ⭐ **a constraint you LIFTED** | see below — this is the highest-value announcement there is |

### ⭐⭐ CHECK EACH OTHER'S WORK — the part that turns two lanes into a team

⛔ **Reading the other lane's file is not courtesy. It is how the programme compounds.**

| ⭐ what to take from them | example |
|---|---|
| **A constraint they LIFTED** | one lane proves each slot gets a **private** store; the other reads it, cites it, and **stops serialising its drives.** ⇒ two lanes driving at once, safely, with **zero orchestrator involvement** |
| **A trap they hit** | *"setting only ONE env var silently writes the SHARED store"* — published once, and the second lane uses the manager's env **verbatim** instead of rediscovering it the expensive way |
| **A method result** | *"the probe gate is the largest lever"* — the second lane reorders its own gates on the first lane's numbers and closes its highest-yield gate first |
| **A correction to a shared claim** | one lane finds a decision record's stated fact is false; **every later lane is spared the same wrong premise** |
| **Their defect SHAPES** | *"an assertion on source text passes while the behaviour is gone"* found in one scope is then looked for, and found, in another |

⭐ **So: when you learn something that is true beyond your own scope, SAY SO IN YOUR FILE** — a
`FYI · to: ALL` post costs a minute and can save another lane an hour. ⛔ **And when you are about to
solve something awkward, check whether a peer already did.**

### The orchestrator's half

| ⭐ do | ⛔ do not |
|---|---|
| **propagate** measured results between lanes — yields, traps, lifted constraints | let each lane rediscover the same lesson |
| keep the **collision list** explicit and current: which pairs of scopes can reach the same file | assume disjoint-by-file covers everything |
| **rule shared-resource contention** the moment two lanes want the same thing | leave it to be discovered at integration |
| ⭐ **verify the ledger against reality** — a stale claim is worse than none | trust a registry a dead process wrote |

⚠ **A stale claim actively misleads.** An instance registry can show a slot held by a
session that closed hours earlier; verify the processes dead and release the claim.
⛔ **Until then the ledger tells every lane that a free slot is taken.**

### ⛔ The two rules that keep parallel work honest

1. ⛔ **NEVER fix in another lane's files.** File it `CROSS-LANE` with evidence and let the owner
   decide. ⭐ Following a call out of your own files is *correct* — fixing what you find there is not.
2. ⛔ **NEVER take a shared resource on the assumption it is free.** Read their file, announce, then
   take. **"It looked idle" is how two processes end up in one store.**

---

## 19 · ⭐⭐⭐ THE MINDSET — every lane and the orchestrator. Read this last; it governs the rest.

> ⭐ **The principle:** give each lane a mindset. Think hard, and make meaning and sense of things rather than
> doing random things. Care about worktrees and environments; when starting the app, care about instances;
> care about meaningful tests, meaningful runs, meaningful screenshots, and watching all the logs. Each lane and
> the orchestrator should have the mindset of an individual: flexible, aware, and context-aware of everything it
> is doing.

⛔⛔ **THE RULES IN THIS SKILL ARE SCAFFOLDING. THIS SECTION IS THE POINT.** A lane that follows every
rule and understands nothing produces a green suite over a broken product. **The typical failure
is a green-looking check that measured the wrong thing.**

### ⭐ MEANING OVER MOTION — the one question, asked before every action

> ⛔ **"What will this PROVE, and how would I know if it proved nothing?"**

If there is no answer, the action is motion. **Motion is how a suite gets to green with tools broken
behind it.**

| ⛔ motion | ⭐ meaning |
|---|---|
| running the suite | running **the arm that was RED before this fix**, and watching it go green |
| sweeping 15 files for a pattern | **reading 2 of them line by line.** ⭐ *A mechanical sweep is not the gate* — a lane said this about its own work and was right |
| "the file mentions the thing" | a predicate **only the thing itself can satisfy.** Prose *about* X satisfies a grep for X |
| adding a test | adding one that **can fail.** If no input makes it red, it pins nothing |
| "I fixed it" | *"I reverted the fix, watched the arm fail, restored it, watched it pass"* |
| a file with no findings | ⭐ **asking what it GENERATES or GATES.** The quietest file is often the root cause of a filed defect |

### ⭐⭐ ULTRA-THINK ON THE THINGS THAT DESERVE IT — and know which those are

⛔ **Not everything deserves deep thought. Knowing which does is the skill.**

**Think hardest about:** anything that **crosses a boundary** (module, process, language, trust) ·
anything that **generates or gates what ships** · anything **claiming to be a proof** (a counter, a
guard, an invariant in a docstring) · anything **absent** (a declared field nothing reads, a config
nobody owns) · and ⭐ **anything that surprises you** — surprise is unmodelled reality, and it is the
cheapest signal you will get all day.

**Move fast on:** the mechanical, the enumerable, the already-decided.

⭐ **And when you reason your way to a conclusion, ⛔ STOP AND RUN IT.** Findings that were reasoned rather than executed are where the misses are:
**in a typical round, every miss is in the reasoned set.**

### ⭐ CARE ABOUT YOUR ENVIRONMENT — it is part of the work, not setup

⛔ **A finding measured in the wrong environment is not a finding.**

| care about | because |
|---|---|
| ⭐ **your worktree** | is it on the tip you think? clean? are you about to measure someone else's changes? ⛔ **Never touch the shared worktree** |
| ⭐ **your interpreter and deps** | a lane's suite can report **phantom failures** because a build-time artifact is not committed and the install step does not fetch it. ⛔ **A red you did not cause is a fact about your environment** |
| ⭐ **your shared resource** | ⛔ **announce before you take it, read the other lane's file first, release when done.** One per lane |
| ⭐ **your data store** | ⛔ **is it private to you, or shared with every lane on this machine?** Two processes, one store, silent corruption. ⭐ *Use the manager's env verbatim — setting one var of a pair silently writes the shared store* |
| **the app's real behaviour** | ⛔ **it does not hot-reload just because you want it to.** Editing an import-time constant and re-driving measures the OLD code |

### ⭐⭐ MEANINGFUL RUNS — what "drive the app" actually demands

⛔ **Launching the app is not driving it. Driving it is walking a JOURNEY a user takes and watching
what the system does at every hop.**

| ⛔ not this | ⭐ this |
|---|---|
| the app started | the app started **and I walked stop → resume → repair → delete → restart** |
| a screenshot | ⭐ **a screenshot of the state that decides the question** — the overflow, the disabled control, the two fields that should be equal width and are not |
| "no errors" | **the logs, correlated to the actions that produced them.** ⛔ Silence is not success — ask *"if this had crashed, would I have seen it?"* |
| the suite is green | ⭐ **the guard was mutated and went RED.** A guard that cannot fail is not a guard |
| a test for the group | ⭐ **a test that reads through what PRODUCTION reads through.** An arm reading through a weaker parser can leave tools broken behind a green suite |

⭐ **The drive catches what suites cannot, in every part.** When a lane fixes code
it has never watched run, **it is validated by the same instrument that missed the defect.**

### ⭐ BE AN INDIVIDUAL — flexible, aware, and willing to say the rule is wrong

⛔ **You are not a rule-follower. You are the owner of your scope, and the rules serve the goal.**

- ⭐ **When a rule and the goal conflict, say so in writing** — with evidence — and propose the change.
  Lanes that overturn an ownership grant, a stated fact in a decision record, a writer
  boundary or a cleared flag improve the system.
- ⭐ **Scope your own work from evidence.** If a gate has measurably produced nothing, say so and
  spend the time where the yield is. ⛔ Do not run a checklist for its own sake.
- ⭐ **`NOT-DONE` is a real outcome.** Real, correctly left alone, **argument written down.** Without
  it, *zero open items* silently becomes *zero unedited items* — which is how a fix round
  manufactures defects.
- ⭐ **Report what you did NOT cover.** Every lane has a boundary it did not reach. **A stated gap
  costs one line; a hidden one costs a round.**
- ⛔ **And never write a claim you have not opened.** Not a citation, not a line number, not a count.
  A false citation can otherwise propagate into a proposed fix.

### ⭐⭐ THE ORCHESTRATOR'S MINDSET IS THE SAME ONE, AIMED HIGHER

⛔ **It holds the only question no lane is holding: *does this add up to the thing shipping?***

- ⭐ **Re-run the load-bearing claims yourself.** A verdict you cannot reproduce goes back, not forward.
- ⭐ **Be wrong in public, quickly, and record the general rule.** An orchestrator that is corrected
  often, and records the rule each time, makes the system better.
- ⛔ **Measure the programme, not the activity.** *Is the count coming down · are the RIGHT things
  being fixed · were they fixed correctly · is the investigation real · does it add up to shipping.*
- ⭐ **Propagate.** One lane's lifted constraint, published, can double the programme's throughput within
  minutes. **That is the orchestrator's highest-leverage act.**
