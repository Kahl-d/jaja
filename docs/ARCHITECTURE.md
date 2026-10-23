# Architecture

This page describes how the parts fit together and why each boundary is where it is. For the vocabulary (tiers,
approvals, gates, lanes) see [CONCEPTS.md](CONCEPTS.md); for the reasoning behind the choices see
[DESIGN-DECISIONS.md](DESIGN-DECISIONS.md).

## The shape in one picture

```
                              you  (decide, approve, send)
                                |
          +---------------------+---------------------+
          |  Claude Code session, started in <data>   |
          |  plugin "jaja" loaded from <repo>   |
          +----+---------+----------+----------+------+
               |         |          |          |
            agents/    skills/    hooks/       mcp/
           30 roles   52 skills  10 guards   24 tools
               \         |          |         /
                +--------+----+-----+--------+
                              |
                         scripts/             deterministic work: gates, scoring,
                      (142 + 15 kernel)       rendering, tracking, approvals, lint
                              |
         +--------------------+----------------------+
         |                    |                      |
       <repo>              <data>                <runtime>
       CODE                DATA                  RUNTIME
       no person in it     your records          approval queue, receipts,
                           (private)             registries, secret pointers
```

## Three roots: code, data, runtime

Every script works against three folders. They are resolved in one place, `scripts/agency_paths.py`, and handed to
the kernel scripts as a small value, `Roots(code, data, runtime)` in `scripts/kernel/roots.py`.

| Root | What lives there | Who may write it | In git |
|---|---|---|---|
| **code** (`<repo>`) | `agents/`, `skills/`, `lanes/`, `routines/`, `hooks/`, `mcp/`, `scripts/`, `schemas/`, `templates/`, `playbooks/`, `config/defaults.d/`, `dashboard/` | Contributors, through pull requests | This repository |
| **data** (`<data>`) | `profile/`, `jobs/`, `applications/`, `prospects/`, `outbox/`, `targets/`, `state/`, `hub/`, `config/`, `resumes/`, `touches/` | Agents and scripts, within their role and tier | Your own choice; never this repository |
| **runtime** (`<runtime>`) | `work/approvals/`, `work/inbox/`, `logs/runs/`, `registry/`, secret pointers | Scripts (receipts, registries) and you (approval decisions) | Not committed |

How each root is found, first hit wins:

- **data**: the environment variable `JAJA_DATA`; then `JOB_SEARCH_ROOT` (an older name); then, under pytest or
  with `JAJA_DEMO=1`, the bundled `examples/demo-data` (a configured real folder is never read in that mode);
  then `data_root:` in `~/.config/jaja/config.yaml` (the file can be moved with `JAJA_CONFIG`). If none
  applies, the path points at a name that does not exist, so the error names the setting.
- **runtime**: `JAJA_RUNTIME`; then, in demo or test mode, a temporary folder; then `runtime_root:` in the same
  config file; then `<data>/runtime`.
- **code**: the repository the script file sits in.

Kernel scripts also accept `--root DIR` (one folder that holds all three, laid out like the repo) or `--code`,
`--data` and `--runtime` one by one. Tests use the single-folder form.

Why three and not two: the code must be publishable, the data must never be, and the approval queue and receipts are
neither. Keeping them apart means the repository can be forked without a scrub, the data folder can be backed up or
versioned on its own terms, and the runtime can be discarded without losing a record.

## Data flow

The system is files in, files out. A typical day:

```
 sources                     data folder                               you
 -------                     -----------                               ---
 job boards, alerts   --->   jobs/<id>.md   (gate: pass | review | fail, with the quoted line)
 hiring posts, mail                |
                                   v
                       applications/<slug>/fit.md      (fit-scorer: 1.0 to 5.0, evidence ids)
                                   |
                  +----------------+-----------------+
                  v                                  v
        resumes/variants/<v>/                prospects/<slug>.md  (people paths)
        (tailor -> editor -> blind                   |
         compare against the base)                   v
                  |                          outbox/O-YYYY-NNNN.md   status: drafted
                  v                                  |
        applications/<slug>/apply-pack.md            v
                  \                       <runtime>/work/approvals/A-YYYY-NNNN.md
                   \                                 |      status: pending
                    +--------------------------------+---------------> you read the exact text
                                                                       and set status: approved
                                                     |
                                                     v
                                  sender: approvals.py verify  -> GO or REFUSE
                                          act once, then approvals.py receipt
                                                     |
                                                     v
                              application.md event log (append-only), touches/, receipts
```

Three properties of this flow carry most of the design:

1. **Every hand-off is a file with a typed header.** Schemas for the main record types are in `schemas/`
   (application, approval, company, job, outbox, prospect, pursuit, and others). `scripts/check_records.py` and
   `scripts/kernel/lint.py` validate them, so an agent that writes a malformed record is caught by a script, not by
   the next agent.
2. **Outward actions only leave through the approval file.** Drafting agents write to `outbox/` and request an
   approval. They cannot send. The `sender` role has the send scopes and acts only on a file that verifies.
3. **State is projected, not stored twice.** An application's stage is computed by replaying its append-only event
   log (`scripts/tracker.py`), so the log is the truth and the stage is a view of it.

## Plugin layout

`.claude-plugin/plugin.json` declares what Claude Code loads:

| Plugin field | Points at | Contents |
|---|---|---|
| `skills` | `./skills` | 52 skill folders, each with a `SKILL.md` |
| `hooks` | `./hooks/hooks.json` | 10 hook registrations |
| `mcpServers` | `./.mcp.json` | the `job-search` records server |
| (agents) | `agents/` | 30 role cards, whose native `description` and `tools` fields `sync.py` keeps in step with `purpose` and `tools_allow` |

```
jaja/
  agents/            role cards (30)               source of truth for roles
  lanes/<type>/      LANE.yaml + AGENT.md (8)      source of truth for lanes
  skills/<name>/     SKILL.md (52)                 procedures, slash commands
  hooks/             hooks.json + 11 Python files  guards (9 entry points, 2 shared helpers)
  mcp/               job_search_mcp, tests, pyproject.toml, uv.lock
  scripts/           142 scripts + kernel/ (15)    deterministic work
  routines/          16 specs, all enabled: false
  schemas/ templates/ playbooks/ role-references/ evals/
  config/defaults.d/ 40 neutral YAML fragments
  dashboard/         local app, standard library server
  examples/demo-data a synthetic candidate and records
  tools/             leak_check.py, build_denylist.py, init-data.py, migration helpers
  .claude/ .codex/ .agents/   generated adapters (see below)
```

Role cards carry the contract of an agent in frontmatter: `tools_allow`, `tier_max`, `deny_commands`, `model`,
`effort`, `budget`, `skills`, `reports_to`, `kpis` and `stop_conditions`. Lane cards add `roster`, `scope`, `gates` and
`stops`. See [guides/customizing-agents-and-skills.md](guides/customizing-agents-and-skills.md).

## Hooks and what each guards

`hooks/hooks.json` registers ten entries over four events. Nine distinct scripts back them (`browser_lease.py` is
registered twice); `lane_common.py` and `rulings_common.py` are shared helpers. Lane hooks fail open: an unknown
identity must not block legitimate work. The two protection hooks that matter most for safety are the generated-file
guard and the role command deny list.

| Event | Script | What it guards or does |
|---|---|---|
| SessionStart | `lane_context.py` | Tells the session whether it is inside a multi-session programme and which role it plays, and prints the hub events since the lane's state file was last refreshed. |
| SessionStart | `lane_register.py` | Registers the session (address, name, branch, role) in `hub/registry.json`, so no one has to guess where to send a message. |
| SessionStart | `lane_rules.py` | Prints the standing rulings that apply to this lane type, as a digest, from `hub/rulings.yaml`. |
| Stop | `lane_handover.py` | At the end of a lane turn that changed files, writes the machine-made handoff section (files and their sha256) into the lane's hub file and refreshes its small state file. |
| Stop, and PreToolUse on `mcp__claude-in-chrome__.*` | `browser_lease.py` | One keyboard per browser: a session must bring its own tab forward before it types or clicks, so text never lands in another lane's tab. |
| PreToolUse on Write, Edit, MultiEdit | `guard_generated_files.py` | Refuses edits to any file carrying the `GENERATED by scripts/kernel/sync.py` marker (or another agency script's marker). Outside the agency's own folders only agency markers count. |
| PreToolUse on Write, Edit, MultiEdit | `lane_write_guard.py` | One author per file in the hub: a lane cannot write another lane's file, a brief, or the orchestrator's files; the orchestrator cannot write lane files. |
| PreToolUse on Bash | `role_deny.py` | Reads the calling subagent's `deny_commands` from its role card and refuses any shell command containing one (for example the messenger cannot run `gmail_send.py execute`). |
| SubagentStart | `role_rules.py` | Prints the standing rulings that apply to the subagent's role into its context before its first turn. |

A rule that matters is enforced by a hook or a script, and the documents say so; a rule that only a model would
follow is labelled as guidance. For example, "no em-dashes in a message" is a mechanical check in `scripts/outbox.py`,
while "write warmly" is guidance.

## The MCP server

`mcp/job_search_mcp` is a records server over the data folder, started by `.mcp.json` as
`uv run --directory <plugin>/mcp job-search-mcp`. It exists so any MCP client (Claude Code, Codex, others) can read
and draft against the same files without shelling out.

- **20 read tools**: jobs (`jobs_search`, `job_get`, `job_sightings`), portals, prospects and the network
  (`prospects_list`, `prospect_get`, `network_for`), applications (`applications_list`, `application_get`,
  `application_hub`), pursuits, touches, `dashboard_metrics`, the profile (`profile_facts`, `evidence_lookup`,
  `resume_variants_list`), `reader_model_get`, `focus_profile_get`. Each returns compact JSON under 8 KB, paged with
  `limit` and `cursor`.
- **4 write tools**: `prospect_set`, `tracker_event`, `outbox_create`, `approval_create`. They create records and
  pending drafts inside the data folder. None sends, posts, submits or approves, and no tool accepts or returns a
  credential.
- Text that came from outside (a job description, an email, a LinkedIn message) is returned inside
  `<untrusted_*>` tags. The server's instructions tell the model it is data and never instructions.

## Kernel scripts

`scripts/kernel/` holds the bookkeeping that must be deterministic. Everything else in `scripts/` is domain work.

| Script | Does | Typical call |
|---|---|---|
| `sync.py` | Renders engine adapters from the source cards, skills, hooks and MCP config; with `--check`, reports drift and writes nothing. | `python3 scripts/kernel/sync.py --check` |
| `lint.py` | Validates schemas, links, rulings, skill descriptions, instruction-file budgets and generated adapters. | `python3 scripts/kernel/lint.py` |
| `sweep.py` | Rebuilds the status briefing and registries from files. | `python3 scripts/kernel/sweep.py --help` |
| `approvals.py` | `hash`, `verify`, `receipt`, `refuse`, `decide`: the approval gates the sender runs. | `python3 scripts/kernel/approvals.py verify <file>` |
| `watchdog.py` | Compares each enabled routine's newest receipt with its schedule: OK, LATE, SILENT, ERROR, OFF. | `python3 scripts/kernel/watchdog.py --no-inbox` |
| `nightly_sweep.py` | Watchdog, sweep, lint and `sync --check` in order, one receipt line per step. | optional routine |
| `roots.py`, `frontmatter.py` | The three-roots value and the frontmatter reader every script shares. | library |
| `evals.py` | A harness that runs a role on fixture cases and grades with deterministic checks plus a clean-context judge. | `evals.py prepare` then `grade` |
| `context_budget.py`, `token_ledger.py` | Measure the context a session loads and the tokens it used, from files and transcripts. | read only |
| `redact.py` | Replaces credential-shaped strings in text files. | `redact.py --check <path>` |
| `hub_events.py`, `lane_state.py`, `rulings_digest.py` | The lane hub tools, below. | |

`scripts/doctor.py` sits above them: one line per check (Python, modules, data folders, config, evidence ids,
records, role cards, lint, adapters, hooks, plugin manifest, MCP import, routines, leak guard) and a summary. It is
read-only and makes no network call.

### Hub tools

A programme's coordination files live in `<data>/hub/`: `programme.json`, `registry.json`, `rulings.yaml`,
`events.jsonl`, `lanes/`, `briefs/`. The tools that keep them consistent:

| Tool | Does |
|---|---|
| `scripts/rulings.py` (`next`, `check`, `add`) | The one way to add a ruling: your exact words, with an id nobody else holds. |
| `scripts/kernel/hub_events.py` (`add`, `since`, `render`, `seed`) | The event feed: one JSON line per ruling, park, go or order change. |
| `scripts/kernel/lane_state.py` | Extracts a lane's small live state from its append-only hub file, so a restart reads a few kilobytes. |
| `scripts/rollcall.py` | The roll call from files, and GO as an event, instead of check-in messages. |
| `scripts/day_queue.py` (`build`, `claim`, `done`, `release`, `add`, `show`) | The ranked work queue lanes claim from. |
| `scripts/kernel/new-programme.sh` | Scaffolds a hub for a new multi-session programme. |

See [guides/lanes.md](guides/lanes.md).

## The configuration seam

Nothing about a person is in the code. Scripts read three sections through `scripts/agency_config.py`:

```
  config/defaults.d/*.yaml            neutral defaults, in the repo, merged in name order
            |
            +--->  <data>/config/*.yaml          your values, any file name, merged in name order, later wins
            |
            +--->  (under pytest) scripts/tests/persona.d/*.yaml     a synthetic persona instead of your folder
```

| Section | Holds |
|---|---|
| `identity` | name, emails, signature names, resume file name, profile URLs, time zone, degrees held |
| `style` | banned phrases, dash rules, greeting and sign-off, send format |
| `policy` | years bands, fit thresholds, location, pacing caps, outreach limits |

Access is by dotted path with a default, for example `cfg.get("style.no_em_dash", False)`. A key left empty turns the
check it drives off. Under pytest the data folder is ignored entirely, so a test cannot depend on, or leak, a real
person. [guides/configuration.md](guides/configuration.md) describes the main keys.

## Generated adapters

The canonical files are engine-neutral. `scripts/kernel/sync.py` renders what each engine needs:

| Source (hand-edited) | Generated output |
|---|---|
| `agents/*.md` | `.claude/agents/<name>.md`, `.codex/agents/<name>.toml` |
| `lanes/<type>/AGENT.md` + `LANE.yaml` | `.claude/agents/lane-<type>.md`, `.codex/agents/lane-<type>.toml`, `.claude/lane-settings/<type>.json` |
| `skills/<name>/**` | `.agents/skills/<name>/**` (for Codex; Claude Code reads `./skills` directly) |
| `hooks/hooks.json` | `.claude/settings.json` (the `hooks` key), `.codex/hooks.json` |
| `.mcp.json` | `.codex/config.toml` (the MCP servers) |

Every generated file starts with a `GENERATED by scripts/kernel/sync.py` marker, and `guard_generated_files.py`
refuses edits to it. `sync.py --check` exits 1 on drift and writes nothing, and it also checks that the plugin
manifests agree on name and version and that every path they name exists. `doctor` runs it. In this checkout
`.claude/agents/` holds 38 generated agent files (30 roles and 8 lanes) for sessions started inside the repo.

A role card with no `tools_allow` is refused rather than rendered, because Claude Code reads an empty `tools:` as
"every tool". The refused card produces no adapter, and a previously generated one is removed.

Only Claude Code is tested end to end. The Codex adapters are generated and checked for drift but not run.

## The dashboard

`dashboard/serve.py` is a local app on the Python standard library `http.server`, bound to `127.0.0.1`. It has nine
pages: Cockpit, Pipeline, Jobs, Companies, People, Queue, Applications, Portals, Numbers. It reads the same files as
the scripts. Reads need no token; every write carries a per-run token and a confirm flag, and nothing in it sends,
posts or submits.
