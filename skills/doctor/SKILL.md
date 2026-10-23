---
name: doctor
description: Use when something looks off (stale status, broken link, drift between a card and its generated agent), after any workshop skill, before a commit, or when the watchdog asks. Runs lint, sync --check and skill checks, then explains the findings.
disable-model-invocation: true
---

# Doctor

Today is !`date +%F`.

## Procedure
1. `python3 scripts/kernel/lint.py` — schemas, broken `[[wikilinks]]`, skill description budgets,
   constitution line count, forbidden edits to generated files. Record the finding count.
2. `python3 scripts/kernel/sync.py --check` — exit 1 means an adapter drifted from its source; the
   fix is always to edit the source and re-run the sync, never to edit the adapter.
3. `python3 scripts/kernel/sweep.py` then read the dashboard footer (`last sweep`, schema errors) and the
   stale list: projects past `review_by`, roles `stale`, routines with no receipt for 2x their interval.
4. Secrets: `python3 scripts/kernel/redact.py --check <capture queue folder>` and
   `git grep -nE "(sk-|AKIA|ghp_|xox[bp]-|BEGIN (RSA|EC|OPENSSH) PRIVATE)" -- . ':!<safe folder>'`; any hit is P0.
5. Skills: for each `skills/*/SKILL.md`, confirm frontmatter is only `name`,
   `description`, optional `metadata`, optional `disable-model-invocation`; description at most 300
   characters and phrased as when-to-use. If the `claude` CLI is available, run `claude plugin
   validate <plugin folder>` and relay its advice; otherwise say it was not run.
6. Generated views: `python3 scripts/build_graph.py --check` (the
   tracking graph: stale notes, broken links, basename collisions), `scripts/role_hub.py --check` and
   `scripts/pursuit_hub.py --check`. Exit 1 is DRIFT; the fix is `python3 scripts/dashboard.py`, never an edit.
7. Token budget: `python3 scripts/kernel/context_budget.py` (start context per root, lane and subagent against its
   budget; OVER is DRIFT), `python3 scripts/model_policy.py check` (a card off its policy row is DRIFT), and
   `python3 scripts/kernel/token_ledger.py` (the measured spend of this machine's sessions, by agent and model; report
   the top three rows).
8. Report by state: OK / DRIFT / BROKEN / SECRET, one line per finding with the file path and the
   exact command that reproduces it. Propose fixes; apply only the ones the owner confirms, and
   never by hand-editing a generated file.
