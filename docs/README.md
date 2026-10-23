# jaja documentation

jaja is a Claude Code plugin that runs a job search as a small team of agents. Scripts count, gate and check;
models judge and draft; you decide and sign. Nothing leaves your machine without an approval file that you marked
approved. Your records live in a folder of your own, never in this repository.

This page is the index. Pick the row that matches why you are here.

## Where to start

| You are... | Read, in this order | Time |
|---|---|---|
| Deciding whether to try it | [Comparison](COMPARISON.md), then [Concepts](CONCEPTS.md), then [Demo walkthrough](guides/demo-walkthrough.md) | about 10 minutes |
| Setting it up | [Getting started](guides/getting-started.md), [Your first search](guides/your-first-search.md), [Configuration](guides/configuration.md) | about an hour, most of it filling in your own records |
| Judging the engineering | [Architecture](ARCHITECTURE.md), [Design decisions](DESIGN-DECISIONS.md), [Roadmap](ROADMAP.md) | about 20 minutes |
| Extending it | [Customizing agents and skills](guides/customizing-agents-and-skills.md), [Lanes](guides/lanes.md) | about 15 minutes |

## The documents

### Understand it

| Document | What it answers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Three roots (code, data, runtime), the data flow, the plugin layout, each hook and what it guards, the MCP server, the kernel scripts, the configuration seam, generated adapters. |
| [CONCEPTS.md](CONCEPTS.md) | The vocabulary: tiers T0 to T4, approval files and the sender, evidence ids and holds, hard gates, depth levels, lanes and the hub, single writer, routines, receipts and the watchdog. |
| [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md) | Ten short decision records: context, decision, consequence. |
| [COMPARISON.md](COMPARISON.md) | An honest table against ad hoc prompting, Claude Code alone, agent frameworks, job-tracker apps and auto-apply bots, including where each is the better choice. |
| [FAQ.md](FAQ.md) | Privacy, cost, engines, applying, platform rules, Windows, working without Gmail, adding an agent. |
| [ROADMAP.md](ROADMAP.md) | What works now, what is next, what is later. No dates. |

### Do it

| Guide | What it covers |
|---|---|
| [guides/getting-started.md](guides/getting-started.md) | Install, the demo, the plugin, your own data folder, the first health check. |
| [guides/your-first-search.md](guides/your-first-search.md) | From an empty folder to scored roles and a first approved draft. |
| [guides/configuration.md](guides/configuration.md) | The `identity`, `style` and `policy` keys and where they live. |
| [guides/lanes.md](guides/lanes.md) | Launching a lane, the roster, the hub folder. |
| [guides/customizing-agents-and-skills.md](guides/customizing-agents-and-skills.md) | Role cards, skills, the `new-*` workshop skills, sync and doctor. |
| [guides/demo-walkthrough.md](guides/demo-walkthrough.md) | Commands run against the bundled demo data, with the real output. |

### Reference material that already exists

| Document | What it is |
|---|---|
| [REFERENCE.md](REFERENCE.md) | The long form: data folder layout, every agent, skill, lane and routine, optional extras, repository layout. |
| [AGENCY-CHARTER.md](AGENCY-CHARTER.md) | The standing charter every lane reads first: the day, the lanes, posts and people, focused work, mass applications, resumes, gating, interviews. |
| [SETUP-CONNECTORS.md](SETUP-CONNECTORS.md) | Optional connectors: Google tokens, claude.ai connectors, the LinkedIn export, Adzuna, Firecrawl, X, browsers. |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | What to run before a pull request. |

## Facts used throughout these documents

Each of these was counted from the repository when the documents were written.

| Thing | Count | Where |
|---|---|---|
| Role agents | 30 | `agents/*.md` |
| Lane agents | 8 | `lanes/<type>/` (apply, focused, hunt, posts, prep, recruiter, research, resume) |
| Skills | 52 | `skills/<name>/SKILL.md`; 27 of them are hand-invoked only |
| Routines | 16, all with `enabled: false` | `routines/*.yaml` |
| Hook registrations | 10, backed by 9 hook scripts | `hooks/hooks.json`, `hooks/*.py` |
| MCP tools | 24 (20 read, 4 write) | `mcp/job_search_mcp/server.py` |
| Scripts | 142 Python files in `scripts/`, 15 more in `scripts/kernel/` | `scripts/` |
| Config fragments with neutral defaults | 40 | `config/defaults.d/*.yaml` |
| Dashboard pages | 9 | `dashboard/app.js` |
| Tests | the default suite on the bundled demo data passes (5,192 tests at the time of writing) | `scripts/tests` |

## Conventions

- The demo candidate is Sam Rivera, a fictional person. Employers in examples are Acme Corp, Globex and Initech.
  Addresses use `example.test`. No real person or employer appears in these documents.
- Skills are slash commands under the plugin namespace, written `/jaja:<skill>` here.
- `<repo>` means your clone of this repository. `<data>` means your data folder. `<runtime>` means the runtime folder.
- Commands in these documents were checked against the code (`--help` or the source) or run against a copy of the
  demo data.
