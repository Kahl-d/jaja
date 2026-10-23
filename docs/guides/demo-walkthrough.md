# Demo walkthrough

This walkthrough runs the project against the bundled synthetic data: a fictional candidate, Sam Rivera, and
fictional employers (Acme Corp, Globex, Initech). Nothing is real and nothing is sent. Every output below was
produced by running the command against a copy of `examples/demo-data`, as shown.

It takes about ten minutes and needs no Claude session: these are the deterministic parts that the agents rely on.

## 0. Work on a copy

Run from your clone of the repository (`~/jaja`). Copy the demo data so that nothing you do touches the
repository, and point the two environment variables at the copy:

```bash
cp -R examples/demo-data /tmp/ja-demo
export JAJA_DATA=/tmp/ja-demo
export JAJA_RUNTIME=/tmp/ja-demo/runtime
```

`JAJA_DATA` is the data folder; `JAJA_RUNTIME` is where the approval queue and receipts live (the demo
ships its pending approval under `runtime/work/approvals/`). Closing the shell forgets both.

If you used `./install.sh --demo --yes` in [getting started](getting-started.md) you already have a private copy at
`~/jaja-demo` that the config file points to. The commands below work on it too: replace `/tmp/ja-demo` with
`~/jaja-demo` and drop the `export` lines.

What the demo folder contains: a profile (facts, 7 evidence ids, holds), a base resume, 7 job postings at different
gate outcomes, 3 applications at different stages, 5 prospects, a company dossier, one outbox draft with an approval
waiting, targets, views, and `config/` with Sam's style and policy.

## 1. Health check

```bash
python3 scripts/doctor.py --data /tmp/ja-demo
```

```
[ok  ] python           Python 3.12.0
[ok  ] python-modules   pyyaml 6.0.2
[ok  ] python-dev-modules pytest present
[ok  ] python-requests  requests present
[ok  ] uv               uv found
[ok  ] data-root        /private/tmp/ja-demo
[ok  ] data-folders     9 required folders present
[ok  ] runtime-root     /private/tmp/ja-demo/runtime
[ok  ] config-values    3 file(s) in config/ parse
[ok  ] profile-files    5 profile files present
[ok  ] profile-facts    facts.yaml names Sam Rivera
[ok  ] evidence-ids     check_evidence: 7 registry ids, 7 cited, 0 problem(s)
[ok  ] records          OK check_records: 53 files, no problems
[warn] role-cards       90 cards audited, 120 finding(s): bloat 8, eval 30, handoff 6, path 58, ruling 10, skill 5, stale-name 3  -> advisory: most are data files you have not created yet
[ok  ] lint             lint: clean
[ok  ] adapters         sync --check: adapters up to date; plugin manifests in step
[ok  ] hooks            10 hooks: files exist and each runs on an empty payload
[ok  ] plugin-manifest  jaja 0.1.0, marketplace jaja; claude plugin validate ok
[ok  ] mcp-server       job_search_mcp imports under `uv run` (data layer and server)
[ok  ] routines         all 16 routines disabled
[ok  ] leak-guard       leak_check: 0 files, 0 failing findings, 0 soft hits

doctor: 23 ok, 1 warn, 0 fail
```

Some lines are trimmed here (the optional-extras, config-file and connectors lines differ per machine) and the exact
finding counts on the `role-cards` line vary with the folder you point at. The shape is the point: one line per check,
a count of problems, and an exit status you can script. The one warning is an advisory audit of the role cards (see the
[roadmap](../ROADMAP.md)); it does not fail the run.

## 2. Evidence

```bash
python3 scripts/check_evidence.py
```

```
check_evidence: 7 registry ids, 7 cited, 0 problem(s)
```

Every evidence id cited in `profile/` exists in `profile/evidence.yaml`. Open that file to see the shape: an id such
as `ev-sam-003`, a source, a locator, a capture date, a note.

Held text is checked the same way:

```bash
python3 scripts/check_holds.py /tmp/ja-demo/profile/PROFILE.md --holds /tmp/ja-demo/profile/PROFILE-HOLDS.md
```

```
OK /tmp/ja-demo/profile/PROFILE.md: no held text; 15 held strings from /tmp/ja-demo/profile/PROFILE-HOLDS.md
```

## 3. Hard gates

```bash
python3 scripts/fit_gates.py --root /tmp/ja-demo --all
```

```
gates: evaluated 7, pass 4, review 1, fail 2; 5 records updated
```

The gate result is written into each job record. Read the three that did not pass:

```bash
cd /tmp/ja-demo/jobs
grep -A3 -E '^(gate_reasons|gate_evidence):' globex-200.md globex-201.md initech-302.md
cd -
```

| Job | Title | Gate | Reason | Quoted evidence |
|---|---|---|---|---|
| `globex-200` | Staff Machine Learning Engineer | fail | `seniority: staff level`; `seniority: asks 10+ years of experience` | the title line |
| `globex-201` | Platform Engineer, Defense | fail | `sponsorship: 'US citizenship is required'` | "U.S. citizenship is required and an active secret security clearance is required." |
| `initech-302` | Machine Learning Engineer | review | `location: remote without a stated country (US employer and W-2 needed)` | "Remote" |

The other four pass: both Acme Corp roles, Initech's LLM Evaluation Engineer, and Globex's Machine Learning
Engineer. The thresholds come from Sam's `config/policy.yaml` (`reject_years: 10`, `cold_max_years: 4`). Change a
number there, run the command again and the result changes; no model is involved. `--dry-run` shows the counts
without writing.

## 4. The approval gate

The demo has one drafted reply and one pending approval. List the outbox:

```bash
python3 scripts/outbox.py list --root /tmp/ja-demo --os-root /tmp/ja-demo/runtime --today 2026-10-23
```

```
id           prospect        channel  to                           status   approval
-----------  --------------  -------  ---------------------------  -------  -----------
O-2026-0001  dana-whitfield  email    dana-whitfield@example.test  pending  A-2026-0001
```

Ask whether it may go:

```bash
python3 scripts/outbox.py can-send --id O-2026-0001 --root /tmp/ja-demo --os-root /tmp/ja-demo/runtime --today 2026-10-23
echo "exit=$?"
```

```
{
  "id": "O-2026-0001",
  "can_send": false,
  "problems": [
    "status pending, not approved"
  ]
}
exit=1
```

Now run the check the `sender` role runs:

```bash
python3 scripts/kernel/approvals.py verify /tmp/ja-demo/runtime/work/approvals/A-2026-0001.md --today 2026-10-23
```

```
FAIL status pending is not approved
FAIL decided_at is empty
OK expires 2026-10-29
OK content_hash sha256:deaf8d846415f1e6bb03a04b2389ac78f10b3ea04020c36a382a0d94349919ce
RESULT: REFUSE
```

Open `runtime/work/approvals/A-2026-0001.md`. Under "Exact content" is the message, word for word. Read it as you
would before approving, then play the owner. Edit the frontmatter of the copy:

```
status: approved
decided_at: 2026-10-23
decided_via: desktop
```

and run verify again:

```
OK status approved
OK decided_at 2026-10-23
OK expires 2026-10-29
OK content_hash sha256:deaf8d846415f1e6bb03a04b2389ac78f10b3ea04020c36a382a0d94349919ce
RESULT: GO
```

Mark the draft approved from the decision, and ask again:

```bash
python3 scripts/outbox.py apply-decisions --approval /tmp/ja-demo/runtime/work/approvals/A-2026-0001.md --root /tmp/ja-demo --os-root /tmp/ja-demo/runtime --today 2026-10-23
python3 scripts/outbox.py can-send --id O-2026-0001 --root /tmp/ja-demo --os-root /tmp/ja-demo/runtime --today 2026-10-23
```

```
{
  "id": "O-2026-0001",
  "can_send": true,
  "problems": []
}
```

Nothing was sent: these commands only answer questions. The point is the next step. Change one word of the
message in the approval's Exact content section (for example "Any afternoon" to "Any morning") and verify again:

```
OK status approved
OK decided_at 2026-10-23
OK expires 2026-10-29
FAIL content_hash sha256:eed8b9cc... does not match the recorded sha256:deaf8d84...
RESULT: REFUSE
```

An approval covers exactly the text you read. Edit it and the approval is void. Undo your change before going on, or
copy the demo again.

## 5. The funnel

```bash
python3 scripts/tracker.py --root /tmp/ja-demo --today 2026-10-23 metrics
```

```
applications: 3
folders: 3 (applications 3, package-only 0, untagged 0)
funnel: discovered 3, fit_checked 3, tailored 3, applied 3, confirmation 3, screen 2, oa 1, interviewing 1, final 0, offer 0, accepted 0
outcomes: rejected 0, ghosted 0, withdrawn 0
response rate by source: greenhouse 2/3
response rate by applied_via: ats 0/1, recruiter 1/1, referral 1/1
by path: missing 2/3 responded (2 screens)
apply latency: median 87.0 h, 1/3 within 72 h
interview rounds: booked 2, held 0, upcoming 0, unconfirmed 2 (time passed, no held record), ended unheld 0
```

A few lines (the ghost thresholds, median days in stage and a hint about the missing path field) are omitted here. Note that every rate is a fraction
with its denominator, and "unconfirmed" is reported as such. Each application's stage is computed by replaying the
event log in `applications/<slug>/application.md`; look at one.

## 6. The dashboard

```bash
python3 dashboard/serve.py --root /tmp/ja-demo --os-root /tmp/ja-demo/runtime --no-open
```

```
job agency dashboard: http://127.0.0.1:8765/ (root /private/tmp/ja-demo; vault runtime); Ctrl+C stops it
```

Open <http://127.0.0.1:8765/>. It binds to `127.0.0.1` only. Nine pages: Cockpit, Pipeline, Jobs, Companies, People,
Queue, Applications, Portals, Numbers. On the demo, the Pipeline lists three deals (Initech LLM Evaluation Engineer
at interviewing, Acme Corp Applied AI Engineer at screen, Acme Corp Machine Learning Engineer at confirmation), the
Cockpit's first suggested action is a follow-up to Acme Corp, and one approval is shown as waiting on you. The page
also reports connector status: the Gmail items show red in the demo because no Gmail tokens exist, which is correct
and harmless. Use `--port` for another port. Stop with Ctrl+C.

The README shows screenshots of the same pages ([cockpit](../assets/dashboard-cockpit.png),
[pipeline](../assets/dashboard-pipeline.png), [numbers](../assets/dashboard-numbers.png)).

## 7. Routines and the watchdog

```bash
python3 scripts/kernel/watchdog.py --no-inbox
```

```
ROUTINE               STATE  SCHEDULE        EVERY  LAST RECEIPT  SILENT AFTER  OWNER
js-apply-batch        OFF    15 8 * * 1-5    1d     never         -             recruiter
js-backlog-harvest    OFF    30 7 * * 1      7d     never         -             inbox-harvester
...                   (16 rows in all, every one OFF)
js-weekly-metrics     OFF    0 16 * * 0      7d     never         -             tracker-ops
watchdog 2026-10-23T14:19:09: state IDLE · 0 enabled (0 ok, 0 late, 0 silent, 0 error) · 16 off
```

`--no-inbox` makes it report only: it writes no state file, alert or receipt.

## 8. Consistency checks

```bash
python3 scripts/kernel/lint.py          # lint: clean
python3 scripts/kernel/sync.py --check  # sync --check: adapters up to date; plugin manifests in step
python3 tools/leak_check.py --no-gitleaks --only docs   # the leak guard over this documentation
```

The last prints a summary and exits 0 when there are no failing findings. It never prints the text that matched, only
the file, line and rule.

## 9. Tests

The default suite runs on the bundled demo data and a fictional persona, never on a real folder. Unset the two
variables first, because an explicit `JAJA_DATA` takes precedence over the bundled demo:

```bash
unset JAJA_DATA JAJA_RUNTIME
python3 -m pytest scripts/tests -m "not real_records and not workspace" -q
```

It passed (5,192 tests) at the time of writing. It is long; run a single file while you work.

## 10. With a Claude session

With the plugin installed ([getting started](getting-started.md)), start Claude Code in the demo folder and try
`/jaja:daily-job-plan`, `/jaja:score-fit` or `/jaja:research-company`. The agents read the same
files you just checked. Anything they draft goes to the outbox and waits for the approval you just practiced.

## Clean up

```bash
rm -rf /tmp/ja-demo
unset JAJA_DATA JAJA_RUNTIME
```
