# Design decisions

Ten short records of choices that shape the project. Each states the context, the decision and the consequence,
including the cost. They are written as the project stands now; a decision can be reversed, and the record should
change when it is.

## 1. Files over a database

**Context.** A job search produces many small records that you will want to read, correct and keep: postings,
people, applications, drafts, rulings. The agents are language models that read and write text well and queries
poorly.

**Decision.** Every record is a Markdown or YAML file with a typed header, in a folder you own. There is no
database. Schemas in `schemas/` describe the headers; `scripts/check_records.py` and `scripts/kernel/lint.py` check
them. Append-only logs (an application's event list, the run receipts) are plain files.

**Consequence.** You can read, diff, grep, back up and version everything with ordinary tools, and an agent's work is
inspectable line by line. The costs are real: no transactions, no joins, and slower scans as the folder grows (the
dashboard keeps an mtime-keyed cache for the postings for that reason). Concurrent writers must be kept apart by
convention and guard (decision 9), not by the storage layer.

## 2. Scripts before models

**Context.** A model can read a job posting and say whether it needs a clearance, and it will be right most of the
time. "Most of the time" is not a gate.

**Decision.** Anything that can be counted, matched, hashed, diffed or validated is a script. Models are used for
judgment and drafting, and are checked by scripts or by a second model in a clean context. A model's statement that
it is done is not accepted as evidence of being done.

**Consequence.** The gates, the approval check, the stage projection, the funnel numbers and the resume lint are
reproducible and testable without a model. The cost is more code to write and maintain, and the scripts are only as
good as their patterns; where a script cannot decide it returns `review`, and a person or model decides.

## 3. An approval file for every outward action

**Context.** The expensive mistakes in a job search are outward: a message sent to the wrong person, a form submitted
with a wrong answer, a post that reveals the search. They cannot be recalled.

**Decision.** No agent sends, posts, submits or pays on its own. The drafter writes the exact text and a script
files an approval with a content hash. You mark it approved. Only the `sender`, the one role with send scopes, acts,
after `approvals.py verify` returns GO: approved, decided, unexpired (7 days), hash unchanged, target unchanged.
Credentials, payments, deletes and CAPTCHAs are tier T4 and have no approval path at all.

**Consequence.** Nothing leaves without your reading the exact text, and a changed draft cannot ride on an old
approval. The cost is friction: every message is a review step, and approvals expire. This is deliberate; the project
is a drafting and checking system, not an autonomous one.

## 4. Three roots: code, data, runtime

**Context.** The same system has to be publishable (a repository), personal (a person's records) and operational (a
queue and receipts that come and go).

**Decision.** Scripts take three roots, resolved in one module (`scripts/agency_paths.py`, `scripts/kernel/roots.py`).
Code is the repository. Data is your folder. Runtime holds the approval queue, receipts, registries and secret
pointers. Resolution order is explicit, and under tests or `JAJA_DEMO=1` the bundled demo data is used and a
configured real folder is not read.

**Consequence.** The repository can be public without a scrub, tests cannot touch a real folder, and the runtime can
be deleted without losing a record. The cost is that every path in every script has to be rooted correctly, which is
why the kernel scripts share one `Roots` value and one set of flags.

## 5. Configuration plus a synthetic persona

**Context.** A job-search tool needs a person's name, voice and rules, and a repository that contains them is not
shareable. Tests that depend on a real person are also a privacy risk.

**Decision.** The code ships neutral defaults (`config/defaults.d/*.yaml`, 40 fragments) and reads identity, style
and policy through `scripts/agency_config.py`. Your values live in `<data>/config/`. Under pytest the data folder is
ignored and a fictional persona (`scripts/tests/persona.d`) is layered on the defaults instead. The demo data is a
fictional candidate, Sam Rivera.

**Consequence.** Tests never read, depend on or leak a real person, and the default suite passes on the bundled demo
data (5,192 tests at the time of writing). An empty key means the check it drives is off, so a new user starts with a
permissive system and tightens it; the tradeoff is that the defaults do not enforce a style until you write one.

## 6. A leak guard with a private deny-list

**Context.** The most likely way a personal job-search tool leaks is a contributor, or the owner, committing a real
name, email, employer or home path in a fixture, a comment or a document.

**Decision.** `tools/leak_check.py` runs as a pre-commit hook and can be run on demand. It checks forbidden folders and file types,
patterns (emails outside reserved domains, home paths, tokens, phone numbers, connector ids) and, when present, a
deny-list built from your own records by `tools/build_denylist.py`. The deny-list is written outside the repository
(`~/.config/jaja/denylist.txt`, mode 600) and never committed. The tool prints the file, line and rule of a
finding, never the matched text.

**Consequence.** The guard knows things the repository must not, without the repository containing them. Without a
deny-list only the pattern checks run, and the hook says so. The guard is a safety net with false negatives (a name
you did not put in your records is not on the list), not a guarantee.

## 7. Generated adapters, checked for drift

**Context.** Claude Code, Codex and any later engine want different file formats for agents, hooks and MCP config.
Hand-maintaining each copy guarantees they diverge.

**Decision.** The role cards, lane cards, skills, `hooks.json` and `.mcp.json` are the sources. `scripts/kernel/sync.py`
renders every engine file from them, each with a `GENERATED` marker, and the `guard_generated_files.py` hook refuses
edits to a marked file. `sync.py --check` exits 1 on drift and writes nothing; `doctor` runs it. A card with no
`tools_allow` is refused rather than rendered, because an empty `tools:` means "every tool".

**Consequence.** There is one place to edit, and drift is detected rather than discovered. The cost is a render step
after every change, and a larger diff in the repository. Only Claude Code is exercised end to end; the Codex outputs
are generated and drift-checked but not run.

## 8. Routines are off by default

**Context.** Scheduled agents that run unattended are where a quiet failure, or a quiet overspend, lives.

**Decision.** All 16 routines ship with `enabled: false`. Each has a tier, a budget and an owner. Each run writes a
receipt, and `watchdog.py` flags a routine that goes silent (two missed scheduled runs). You enable a routine only
after running its work by hand and reading the receipt.

**Consequence.** A fresh install does nothing in the background, and the first run of anything is one you watched.
The cost is that the "agency that works while you sleep" is something you assemble routine by routine, on purpose.

## 9. One writer per artifact

**Context.** Several sessions work at once (a hunt lane, a resume lane, a recruiter). Two sessions appending to the
same file lose each other's writes, and this happens without any error.

**Decision.** Each artifact has one owner at a time. Agents pass reports, not edits. Where it can be mechanical it is:
`lane_write_guard.py` blocks a lane from writing another lane's hub file or the orchestrator's files, and the
orchestrator from writing lane files. Rulings are recorded once, with a unique id from `scripts/rulings.py`, and
injected by hooks rather than copied into cards.

**Consequence.** Lost writes become refused writes, which are visible. The cost is that some tasks take an extra
hand-off, and the guards fail open when a session's identity is unknown, so they prevent accidents rather than
adversaries.

## 10. Evidence ids for every claim

**Context.** A model asked to tailor a resume will improve it, and an unconstrained model improves it with things that
were not true. The failure is quiet and the cost lands on the person.

**Decision.** Every provable fact has an id in `profile/evidence.yaml`. Resume lines, answers and messages cite ids.
`check_evidence.py` rejects an id that does not exist. `check_holds.py` rejects held strings (disputed or unconfirmed
figures). A variant ships only if a blind comparison against the base says it wins, and a reviewer in a clean context
signs a verdict.

**Consequence.** An invented claim has nowhere to hide, and a disputed number stays out until you rule on it. The
cost is up-front work: the evidence registry has to be built before the agents are useful, which is why the first
guide spends its time there. The checks verify that an id exists and that held text is absent; they do not prove that
a cited source says what the sentence says, which is the reviewer's job.
