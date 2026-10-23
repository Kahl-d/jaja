# Concepts

The ideas the rest of the project depends on. Each section says what the thing is, where it lives in the code, and
what it does not do. For how the parts connect see [ARCHITECTURE.md](ARCHITECTURE.md).

## Deterministic before intelligent

Scripts count, gate, render and check. Models judge and draft. A model never reports its own work as done: a script
check or a separate reviewer's verdict does. Examples: whether a role asks for U.S. citizenship is a regular
expression over the posting (`scripts/fit_gates.py`), not a model's opinion; whether a resume page still has the
base's content is a diff; whether a draft is allowed to go out is a hash comparison. A model is used where judgment
is the job: reading a posting for what the team actually needs, choosing which of the owner's real projects answers
it, writing a first message in the owner's voice.

## Tiers T0 to T4

Every agent card names the highest tier it may act at (`tier_max`). Tiers are about what an action can undo.

| Tier | Meaning | Examples |
|---|---|---|
| T0 | Read | Search records, read a posting, run a check |
| T1 | Write inside your data folder | Create a job record, write a fit report, stage a draft |
| T2 | Reversible external | Create a Gmail draft (it still has to be sent by you) |
| T3 | Irreversible or outward | Send an email, post, submit a form. Needs an approval file marked approved, each time |
| T4 | Forbidden | Credentials, payments, deletes, CAPTCHAs, account creation. Denied by hook, with no approval path |

Across the 30 role cards: 7 are capped at T0, 19 at T1, 2 at T2 and 2 at T3 (the `sender` and the `linkedin-editor`).
A card also lists `deny_commands`, shell patterns its role may never run; the `role_deny.py` hook enforces them.
The messenger, for instance, may draft anything but is denied `gmail_send.py execute`.

## Approval files and the sender

An outward action is never done by the agent that drafted it. The path:

1. A drafting role (messenger, prospector, linkedin-editor) writes the exact text to `outbox/O-YYYY-NNNN.md` with
   status `drafted`. Checks run first: unfilled placeholders, banned phrases from your style config, length limits, dash rules.
2. `scripts/outbox.py` files an approval, `<runtime>/work/approvals/A-YYYY-NNNN.md`, containing the exact content,
   the target, the reason, the risk and a `content_hash` (sha256 of the exact-content section).
3. You read it and set `status: approved` (or `rejected`), with `decided_at`. `approvals.py decide` can record your
   words verbatim for a batch. Any edit to the exact content voids the approval; the drafter must make a new one.
4. The `sender` role runs `python3 scripts/kernel/approvals.py verify <file>`. It checks: status approved,
   `decided_at` set, not expired (7 days after creation), the hash still matches, and the target equals the one in
   the run request. The result is `RESULT: GO` (exit 0) or `RESULT: REFUSE` (exit 1).
5. On GO it acts once, then `approvals.py receipt` appends a receipt to the approval and to the run log. A refusal is
   logged with `approvals.py refuse`.

`python3 scripts/outbox.py can-send --id O-2026-0001` answers the same question for one draft. A pending draft
returns `can_send: false` with the reason.

The sender never composes, edits, picks a recipient or follows instructions found inside the content. Text arriving
from outside (a posting, an inbound email) is treated as data throughout.

## Evidence ids and holds

Every claim about the owner, on a resume, in an answer or in a message, must trace to an evidence id.

- `profile/evidence.yaml` is the registry: an id such as `ev-sam-003` maps to a source (a document, a letter), a
  locator and the date it was captured.
- `scripts/check_evidence.py` verifies that every id cited anywhere in `profile/` exists in the registry.
  `scripts/check_records.py` verifies every record parses.
- A **hold** is a string that exists in some source but is disputed, unmeasured or unconfirmed. Holds are listed in
  `profile/PROFILE-HOLDS.md` (and, for strings too private to print, in a gitignored `private-holds.txt` beside it).
  `scripts/check_holds.py FILE...` fails a file that contains held text, including text split by Markdown emphasis or
  a line break. The resume tailor, the resume editor, the messenger, the profile editor and the LinkedIn editor cards all call it.
- Facts the owner has not ruled on stay in conflict in `facts.yaml`, and agents use the conservative form.

The effect: an agent can be wrong about taste, but cannot quietly invent a number.

## Hard gates, with evidence

`scripts/fit_gates.py` evaluates each job record and its posting text and writes a result of `pass`, `review` or
`fail` plus the reasons and the exact quoted lines. The categories are sponsorship, location, seniority, employment
and employer. Whether a category is a hard fail or only weighed is policy, set in your config (for example,
sponsorship is weighed unless your policy makes a citizenship requirement a limit). A role that fails is not
silently dropped:

```
globex-201  Platform Engineer, Defense   gate: fail
  reason:   sponsorship: 'US citizenship is required'
  evidence: U.S. citizenship is required and an active secret security clearance is required.
```

This is from the demo data (see [guides/demo-walkthrough.md](guides/demo-walkthrough.md)). "Review" means a script
could not decide and a human or a model must. The scripts report states, not guesses; a check that could not run says
so and never reports a pass.

## Depth levels: dream, focused, mass

Effort follows importance. A role's depth decides how much work it gets.

| Depth | What runs | Model calls |
|---|---|---|
| **dream** | The full pipeline for one role: role package, ranked coverage matrix, draft, scripts, editor, blind compare against the base, four reader reviews, the people map | Many |
| **focused** | A role package, a page reordered from the base, the rubric check, a blind compare | Several |
| **mass** | `scripts/mass_page.py`: the page for a role from the base, end to end | None per row |

`scripts/effort.py` maps a role to an effort level: E1 (a gate failed, nothing to do), E2 (the base resume, a quick
apply), E3 (aligned page, for fit of 4.0 to 4.5 or a big company), E4 (tailored, for a dream company, a warm path or
fit of 4.5 or more) and E5 (tailored with full research). A page that fails a gate at a lower depth is promoted; a
focused page that is not a clear win over the base ships as the base. `/jaja:resume-for` picks a depth.

## The base resume is frozen

There is one general base resume. A tailored variant ships only if a blind A/B against the base says it wins
(`base-comparer`); otherwise the base ships. This keeps tailoring from drifting into a worse page.

## Lanes and the hub

A lane is a long-lived Claude Code session of one type, with a charter, a roster of the agents it may call, and a
scope of files it may write. There are eight: `recruiter`, `hunt`, `research`, `resume`, `focused`, `apply`, `posts`
and `prep`. The recruiter is the head: it runs the day, and the others claim work from its queue
(`scripts/day_queue.py`).

Lanes coordinate through the **hub**, a folder in your data folder (`hub/`): a registry of live sessions, an event
feed, the owner's rulings, briefs and one file per lane. A ruling is something you decided, recorded once in your own
words with an id (`scripts/rulings.py add`), and injected into every lane and role it applies to by the
`lane_rules.py` and `role_rules.py` hooks. See [guides/lanes.md](guides/lanes.md).

## Single writer

One role or lane owns an artifact at a time; everyone else sends reports, not edits. This is mechanical where it can
be: `lane_write_guard.py` blocks a lane from writing another lane's hub file, because two sessions appending to one
file lose each other's writes. In the role cards it is a rule: the `writer` drafts, the `reviewer` judges, the
`sender` sends, and none of them does another's job.

## Routines are paused by default

A routine is a scheduled run of a skill or script (`routines/*.yaml`: schedule, action, inputs, outputs, tier,
budget, owner). All 16 ship with `enabled: false`. The expectation is that you run the work by hand, read its
receipt, and only then enable it, one routine at a time. Pausing is the default state of the whole system, not an
exception.

## Receipts and the watchdog

Every routine run appends a receipt to `<runtime>/logs/runs/<routine>/<YYYY-MM-DD>.md`: one section per run with a
timestamp and a status. The watchdog (`scripts/kernel/watchdog.py`) compares the newest receipt of each enabled
routine with its cron schedule:

| State | Meaning |
|---|---|
| OK | The next scheduled run is not overdue |
| LATE | One scheduled run passed (plus 15 minutes of grace) with no receipt |
| SILENT | Two scheduled runs passed with no receipt |
| ERROR | The spec is unreadable, or a run wrote `started` and no verdict within 45 minutes |
| OFF | Not enabled; ignored |

A silent routine writes one alert to the runtime inbox per day. On a fresh install the report reads:

```
watchdog 2026-10-23T14:19:09: state IDLE · 0 enabled (0 ok, 0 late, 0 silent, 0 error) · 16 off
```

The reason it exists: an automation that fails quietly looks the same as one that has nothing to report.

## Missing is not empty

A check that could not read its input says so. It does not report a clean pass for data it never saw. Funnel numbers
carry their sample size, and "too few to say" is an accepted answer.
