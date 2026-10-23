---
title: THE HUB — the contract every lane and the orchestrator work through
programme: {{PROGRAMME}}
ticket: {{TICKET}}
shipping_branch: {{BRANCH}}
base: {{BASE}}
doc_type: hub-contract
writer: ORCHESTRATOR ONLY
status: STANDING — read this before posting anything
---

# THE HUB — {{PROGRAMME}}

⭐⭐⭐ **This is the centralized surface.** Every lane reads the plan here, posts here, reaches the
orchestrator here, and sees the other lanes here.

⛔ **THE DOCTRINE IS THE `lane-orchestration` SKILL. This file is the PROGRAMME-SPECIFIC contract.**
Where they disagree, the skill wins on method and this file wins on facts about {{PROGRAMME}}.
Read `SKILL.md` §0–§12 and its `references/` — do not re-derive them here.

---

## 0 · WHO WE ARE

| | |
|---|---|
| ⭐ **ORCHESTRATOR** — one session, `{{PREFIX}} MAIN lane` | the manager. Owns the plan, the division, the assignment, **the gate**, the integration, **all git**, and the goal. ⛔ **Not** a reviewer of your code, not a second opinion, not a permission desk |
| ⭐⭐ **A LANE** | a worker **and the OWNER of its scope**. Understands · investigates **by execution** · reviews · fixes root causes · reasons · delivers a **FINAL VERIFIED VERSION** |
| ⭐ **A SUB-LANE** | one PART inside a lane's scope. **Its lane is its orchestrator.** Hands back to the lane, never to the orchestrator |

⭐⭐ **OWNERSHIP MEANS NOBODY COMES BACK.** Marking something done promises that no one has to reopen
it. **That promise is the job.**
⭐ **If you are asking whether to keep going, the answer is yes.**

---

## 1 · THE MISSION

**{{MISSION}}**

**The goal for YOUR scope:** finished — every item resolved, every improvement made, the vision
aligned, the whole thing proven, handed over as a final verified version nobody has to come back to.
⛔ **Not "reviewed." Not "findings filed." Not "a batch landed."** ⭐ **FINISHED.**

```
   ORCHESTRATOR ({{PREFIX}} MAIN lane) — plans, assigns, gates, integrates, owns all git
        │
        ├── {{LANE_TREE}}
        │
        ▼   each lane: understand + analyse → fixes → self-review → CHANGE SET
   ORCHESTRATOR: gate on the INTEGRATED tree → live drive → the owner's two approvals
        │        → ONE commit per scope → push → announce the tip → lanes rebase
        ▼
   {{DELIVERABLE}}
```

⭐ **With one commit per scope and a single deliverable, it ships at the standard of its weakest
scope.** A scope that closes with open rows does not cost its own lane — it costs the whole thing.

---

## 2 · ⛔ ONE WRITER PER FILE

| file | the ONLY writer |
|---|---|
| `README.md` · `ORCHESTRATOR.md` · `SIGNAL.md` · `LANES-LIVE.md` · `RESOURCES.md` · `MERGE-TRAIN.md` · `RESEARCH-MAP.md` · `briefs/*` · `lanes/*-ORDERS.md` | ⭐ **the orchestrator** |
| ⭐ `lanes/<id>.md` | ⭐ **that lane — nobody else, not even the orchestrator** |
| `lanes/<L>/sub/<S>.md` | that sub-lane · `lanes/<L>/briefs/<S>.md` → that lane |
| the lane's own dossier / findings / probes | ⭐ **that lane** |
| ⛔ `../TRACKER.md` · `../DIVISION.md` · `../classify.py` · shared pins · the PR body | **the orchestrator only** — hand over ROWS |

⛔ **A lane never edits another lane's files, and never FIXES a defect it finds in them.**
Post `CROSS-LANE` with evidence; the orchestrator routes it **by mechanism, never by hand**.
⭐ **Lane → lane works: read their file, post in yours, addressed to them.**

---

## 3 · ⛔⛔ THE CHANNEL — read `lane-orchestration/references/messaging.md` BEFORE addressing anyone

**Measured for {{PROGRAMME}} on {{DATE}} — re-probe and re-record whenever it changes:**

| | |
|---|---|
| accounts | {{ACCOUNTS}} *(same ⇒ two-way `SendMessage`; separate ⇒ file-only, separate rate limits)* |
| orchestrator address | `{{ORCH_ADDRESS}}` ⚠ a **cache** — re-stated by its owner, never inferred |
| lane addresses | ⭐ **`LANES-LIVE.md` §VOLATILE**, each row with the UTC time it was verified |

⛔ **A session TITLE is not an address.** Only the auto-generated registry name resolves — and every
session reads **its own** off the first line of `ListAgents`.
⛔ **A refusal is trustworthy; a SUCCESS is trustworthy only if the row was `interactive`.**
⛔⛔ **DISK IS THE CONTRACT; A MESSAGE IS A COURTESY.** Anything not written here did not happen.

⛔ **THERE IS NO PUSH.** A ruling reaches you when you look. ⇒ ⭐ **read [`SIGNAL.md`](SIGNAL.md) at
every phase boundary, every gate close, and before every blocking question.**

---

## 4 · ⛔⛔ A SESSION IS NOT A DAEMON — do more per turn

It works a turn, then **HALTS until a human types.** ⇒ every turn you end, your lane is dead until
someone notices. ⛔ **Do not end a turn because a unit closed, a fix landed, a gate closed, or you
posted a STATUS.** ⭐ **A status post is a broadcast, not a checkpoint.**

**End every turn this way:** ① re-read `SIGNAL.md` → ② rewrite your `## Progress` table in place →
③ post what you did and what is next → ④ **continue.**

⛔ **The only legitimate stops:** the full stop this programme defines · a `BLOCKER` your brief names
· a decision that is the owner's · context genuinely exhausted.

---

## 5 · THE LIFECYCLE — five phases, no skipping

```
A RESEARCH & REFERENCE → B ANALYSE → C ⛔ FULL STOP (hand the complete analysis back)
                                      → D IMPLEMENTATION MODE → E GATES & HANDOVER
```

⛔ **No fix is written before C closes.** ⛔ **Phase D loads `implementation-mode`, with ONE
override: ONE COMMIT PER SCOPE, made by the orchestrator only.**
⛔ **NO VISION, NO WORK** — if your scope has no vision, explain the scope back to the owner in the
user's terms with the questions only the owner can answer, **and STOP.** Pre-vision you may gather FACTS;
⛔ you may not render VERDICTS.

---

## 6 · HANDOVER — the eight self-review checks, then the FINAL VERDICT

⛔ **A handover that has not been self-reviewed is sent back unread.** Full text + the verdict table:
`lane-orchestration/references/hub-contract.md` §6–§7.

⭐ **Push your lane branch continuously; tag at handover.** ⛔ **You do not commit or push on
`{{BRANCH}}`. Ever.** You produce a reviewed, evidenced CHANGE SET; the orchestrator converts it
into a commit — **a commit is the certificate that the gate passed.**

⛔ **An empty list is not a clean bill; it is a list.** Exit condition is **two consecutive passes
that find nothing new**, and any edit resets the counter.

---

## 7 · PROGRAMME FACTS THAT BITE

{{ENTRY_CONDITIONS}}

- ⛔ **Work in your OWN worktree, outside the project folder.** Never touch the shared one.
- ⛔ **Announce → take → use → release** for every shared resource. Read the other lanes' files first.
- ⛔ **One author and one committer, forever: {{AUTHOR}}.** Test with `git var GIT_AUTHOR_IDENT` —
  environment variables override git config, and no guard catches it.
- ⛔ **Follow the repo's attribution policy; the author above is the only one.**
