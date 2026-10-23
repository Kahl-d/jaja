# Reference

The long form of the README: data folder layout, configuration, the daily flow, the nuances, every agent, skill, lane and
routine, optional extras, connectors, checks and the repository layout. Start with the [README](../README.md) and the
[docs index](README.md); come here to look something up.

## What stays private

| In this repo (shareable) | In your data folder (private, never committed here) |
|---|---|
| role cards, skills, playbooks (method only) | your profile, evidence, resumes, answers |
| scripts, hooks, MCP server, dashboard | applications, prospects, contacts, jobs, touches |
| schemas, templates, neutral defaults | your targets, rulings, hub notes, lane logs |
| a synthetic demo (`examples/demo-data`) | your style config, policy values, voice guide |
| the leak guard and its pattern rules | the deny-list built from your own records |

`tools/leak_check.py` runs before every commit (pre-commit hook). It fails the commit if a name, email, phone
number, company or person from *your* records, a connector id, a token, a home path or a ruling id shows up in a
tracked file. The deny-list lives in `~/.config/jaja/denylist.txt` (mode 600), never in the repo.

## Quick start in detail

Requirements: macOS or Linux, Python 3.11+, [Claude Code](https://claude.com/claude-code), `git`. Recommended: [`uv`](https://docs.astral.sh/uv/) (runs the MCP server).

```bash
git clone <this repo> ~/jaja && cd ~/jaja

# 1. try it on synthetic data first (nothing real, nothing sent)
./install.sh --demo --yes
python3 scripts/doctor.py            # all green on a clean machine

# 2. add the plugin to Claude Code
claude plugin marketplace add ./
claude plugin install jaja@jaja --scope user

# 3. your own folder (blank skeleton), then open Claude Code in it
./install.sh --data ~/jaja-data
cd ~/jaja-data && claude
```

In the session, skills are slash commands: `/jaja:daily-job-plan`, `/jaja:score-fit`, `/jaja:research-company` ...
Agents are called by name (`fit-scorer`, `resume-tailor`, ...) or work inside a lane.

First hour with your own folder:

1. Fill `profile/facts.yaml` (identity, education, experience, links) and `profile/evidence.yaml`
   (one id per provable claim). Everything an agent writes about you must cite an evidence id, so this is the
   foundation. `/jaja:add-experience` interviews you and writes the records for you.
2. Fill `config/identity.yaml`, `config/style.yaml`, `config/policy.yaml` (see below). Leave a key empty and the check
   it drives is simply off.
3. Fill `targets/roles.md` (the roles you want) and review `sources.yaml` at the data root (discovery channels; nothing polls until you run the scout).
4. Run `python3 scripts/doctor.py --data ~/jaja-data`.
5. Run `/jaja:scout-jobs`, then `/jaja:score-fit` on what passes the gates.

## Your data folder

`tools/init-data.py PATH` creates it (the installer does this for you). It never overwrites a file.

```
profile/        facts.yaml  evidence.yaml  PROFILE.md  VOICE.md (your voice, blank)  skills-taxonomy.yaml  corpus/  answers/
resumes/        your one base resume + one folder per tailored variant
jobs/ jobs-pending/   discovered postings, one record each, with gate results
applications/   one folder per application: fit report, pack, status events
prospects/ graph/ touches/   people you could reach, the relationship graph, every touch
outbox/         drafts waiting for your approval
pursuits/       company-level campaigns
targets/        roles.md  sources.yaml  platforms.yaml  fit-gates.yaml  post-queries.yaml  x-deny.yaml  mass.yaml
state/          reports, learning loop, presence checks, browser logs
hub/            lane orchestration: programme.json, registry.json, rulings.yaml, events.jsonl, lanes/, briefs/
config/         identity.yaml  style.yaml  policy.yaml  (your values)
views/          Obsidian Bases tables over your records (optional)
legal/          your own work-authorization timers and notes (optional)
```

Where the folder is found (first hit wins): `JAJA_DATA`, `JOB_SEARCH_ROOT` (older name),
`~/.config/jaja/config.yaml` (`data_root:`), and under tests or `JAJA_DEMO=1` the bundled demo.
The approval queue and run receipts live in a runtime folder (`JAJA_RUNTIME`, default `<data>/runtime`).

## Configuration: identity, style, policy

Nothing about a person is hard-coded. Scripts read three sections through `scripts/agency_config.py`:

| Section | Holds | Examples |
|---|---|---|
| `identity` | who you are on paper | name, emails, signature names, resume file name, profile URLs, time zone, schools |
| `style` | how you write | banned phrases, dash rules, greeting and sign-off form, send format, letter limits |
| `policy` | how you search | years bands, fit thresholds, location rules, sponsorship stance, pacing caps, outreach limits |

Neutral defaults ship in `config/defaults.d/*.yaml` (every key the code reads, off or empty). You override any key in
`<your data folder>/config/*.yaml`; files merge in name order. `init-data.py` writes starter files that list every
key with a comment. Under pytest the data folder is ignored and a synthetic persona
(`scripts/tests/persona.d`) is used, so tests never depend on a real person.

Your **voice** is yours: the agents that write for you read `config/style.yaml` and `profile/VOICE.md`. The repo
ships the mechanism (mechanical checks in `scripts/outbox.py`, `letters_check.py`, `send_preflight.py`, render lints)
and no rules of its own. An empty style file means no style checks.

## A day with the agency

The charter (`docs/AGENCY-CHARTER.md`) is the standing description. In short, the **recruiter** lane runs the day:

1. **Sweep.** Mail triage and the dashboard refresh; what changed overnight, what is waiting on you, what is overdue.
2. **Hunt.** Boards, alerts, hiring posts and funding signals produce job records. Hard gates (`scripts/fit_gates.py`)
   fail or flag each one *with the evidence line*; `fit-scorer` scores the survivors against your evidence.
3. **People first.** For every strong role the `prospector` looks for a person path (referrer, hiring manager,
   recruiter who places there, alum); the `messenger` drafts the note; you approve.
4. **Pages.** `role-analyst` decodes the posting into a ranked coverage matrix; `resume-tailor` builds a variant from
   your base using only your evidence; `resume-editor`, `base-comparer` and the readers gate it before you see it.
5. **Apply.** Mass rows are filled and checked by `apply-assist`; **submission waits for your batch yes**.
6. **Follow up.** Nudges are drafted on the cadence in the charter and stop after the cap.
7. **Prepare.** Every booked round gets a prep pack, a mock and a debrief.
8. **Evening wrap.** One report of what moved, what is stuck and what the numbers say (always with n).

Depth levels: **dream** (full pipeline, one role at a time), **focused** (package, reorder from the base, rubric,
blind compare), **mass** (no model call per row; scripts only). `/jaja:resume-for` picks one.

## The nuances (read these)

- **Nothing goes out without your yes.** Tiers: T0 read, T1 write inside your data folder, T2 reversible external,
  T3 irreversible or outward (needs an approval file marked approved), T4 forbidden (credentials, money, deletes,
  CAPTCHAs; denied by hook). Each agent card states its `tier_max`; hooks enforce its `deny_commands`.
- **Connectors are off by default.** Gmail, Calendar, Firecrawl, X and a logged-in browser are optional. Read-only
  first; a send-capable token is a separate file and a separate login ([Connectors](#connectors)).
- **Routines are all paused.** `routines/*.yaml` ship `enabled: false`. Turn one on only when you have watched it run
  by hand and read its receipt. Every routine writes a receipt under the runtime `logs/runs/` folder, and the
  `ops-watchdog` flags silence.
- **Gates fail with evidence, not vibes.** A gate that blocks a role quotes the line that caused it. Sponsorship and
  years are *weighed* unless your policy turns them into hard fails.
- **Every claim traces to evidence.** Resume lines, answers and letters cite `evidence.yaml` ids; the checks refuse
  a number or claim with no source, and a "holds" list (`profile/PROFILE-HOLDS.md`) names strings that must never be
  added.
- **The base resume is frozen.** A variant ships only if a blind A/B against the base says it wins; otherwise the base
  ships.
- **Real employers only.** Staffing relays and unnamed-client postings fail the employer gate by default (the lists
  are config).
- **Pacing is policy.** LinkedIn/X/browser reads and sends have daily caps and gaps (`scripts/pace.py`,
  `scripts/browser_pace.py`); a block page stops every lane.
- **Lanes are separate sessions.** An orchestrator and lane sessions coordinate through the `hub/` folder (events,
  registry, rulings, briefs). One writer per file; a ruling you give once is injected into every lane that needs it.
  See `skills/lane-orchestration`.
- **Generated files are marked** `GENERATED by scripts/kernel/sync.py` and a hook blocks edits to them: change the
  source card or skill and run `python3 scripts/kernel/sync.py`.
- **Missing is not empty.** A check that could not run says so; it never reports a clean pass for unread data.
- **No history rewrite.** Receipts, events, journals and archives are append-only.

## Agents, skills, lanes, routines

<!-- BEGIN GENERATED TABLES (python3 tools/gen-reference.py) -->

### Agents (30)

Role cards live in `agents/`; Claude Code loads them from the plugin and `scripts/kernel/sync.py` keeps their native fields in step.

| Agent | What it does | Max tier |
|---|---|---|
| `apply-assist` | Builds and lints one apply pack per application (every answer copied from a record, with its source), keeps the portal account registry, and fills ... | T1 |
| `base-comparer` | The regression gate, in a clean context: judges every changed bullet of a variant blind against the base line it replaces, for this JD, and reverts ... | T0 |
| `company-researcher` | Builds and refreshes the cited company dossier (companies/<slug>.md): product, team, stack, news, hiring signals, H-1B/LCA and E-Verify, interview ... | T0 |
| `corpus-keeper` | Owns the profile's records (profile/corpus/**, evidence.yaml, facts.yaml, profile/_inbox/): turns the owner's new work and testimony into dated ... | T1 |
| `engineer` | Code work inside builds through branches and PRs, with CI and deploy checks | T2 |
| `fit-scorer` | Settles review gates, then scores each surviving job with an A to H report and a weighted 1.0 to 5.0 score tied to evidence ids (sponsorship weighed ... | T1 |
| `hiring-manager-persona` | Reads a finished page as the hiring manager the Role Package describes, in one pass: would I call this person, which line made me want to, which line ... | T0 |
| `inbox-harvester` | Harvests every recruiter, founder, hiring-manager and referral contact from Gmail (backlog since the configured start date) and the LinkedIn data ... | T1 |
| `job-scout` | Runs the discovery pipeline (ATS boards, HN, Adzuna, Himalayas, alert mail), records postings the owner reads, judges what scripts cannot (title ... | T1 |
| `legal-visa` | Tracks STEM OPT and EAD clocks and the unemployment-day ledger, checks each employer for E-Verify, I-983 and sponsorship risk, keeps the sponsorship ... | T1 |
| `linkedin-editor` | Keeps LinkedIn true to the approved master profile via state/linkedin/LINKEDIN.md: drafts sections with artifact proof, proof posts and comments ... | T3 |
| `mail-triage` | Keeps the job search in sync with Gmail on each pass: incremental harvest, L0-L2 classification by script, a model review of L3 and low-confidence ... | T2 |
| `messenger` | Drafts every reply, follow-up, scheduling note, thank-you and approved negotiation message to prospects (email, LinkedIn, X) in the owner's voice ... | T1 |
| `negotiator` | From the first offer on: researches comp bands, builds the offer matrix with visa terms (E-Verify, I-983, H-1B, wage level, start-date gap), writes ... | T0 |
| `ops-watchdog` | Routine health, silent-failure alerts, backups, secret rotation and budget-breach alerts (their one writer) | T1 |
| `platform-scout` | Owns targets/platforms.yaml and research/job-platforms-2026.md: reads each platform's terms, picks the allowed channel (API, alert mail, browser with ... | T1 |
| `prep-coach` | Prepares the owner for every interview round from the corpus: prep packs (coding, system design, domain fundamentals, behavioral), STAR stories, a ... | T1 |
| `profile-editor` | Owns the finalized master profile (profile/PROFILE.md): every experience, project, skill and credential in final wording, aligned end to end so the ... | T1 |
| `prospect-triage` | Scores every new or changed prospect for warmth, fit, sponsorship (a weighed signal | T1 |
| `prospector` | Finds the warmest human path into each target role (alumni, former colleagues, people who engaged, hiring managers) from the LinkedIn export ... | T1 |
| `researcher` | Research with cited reports | T1 |
| `resume-editor` | The editorial gate, run in a clean context: checks every resume, cover letter and why-us note for truth against the profile, alignment to the role ... | T0 |
| `resume-tailor` | Researches each role and tailors one variant per job from the one general base resume, aligning the page truthfully to the title and the whole JD ... | T1 |
| `reviewer` | Clean-context checks against a rubric | T1 |
| `role-analyst` | Understands a role before anyone writes: reads the title, JD and company, picks the role lens, decodes the JD into a ranked coverage matrix ... | T1 |
| `sender` | Executes approved outward actions only, one approval file at a time, fully audited | T3 |
| `technical-ai-reviewer` | A senior AI engineer's read of the page: is every technical claim precise, current and defensible in a two-minute interview follow-up | T0 |
| `technical-recruiter-reviewer` | The senior recruiter of THIS company hiring for THIS role, built from a researched brief: screens the page and letters against the company's bar, the ... | T0 |
| `tracker-ops` | Keeps every application's event log true through scripts/tracker.py, the one status of the search: records applications and stage events with ... | T1 |
| `writer` | Drafts emails, posts, documents and resumes as the single writer per artifact | T1 |

### Lanes (8)

A lane is a long-lived session with a charter, a roster and a scope. Launch one from your data folder: `claude --agent lane-<type> --settings <repo>/.claude/lane-settings/<type>.json` (with the plugin installed the agent is `jaja:lane-<type>`).

| Lane | Title | Agents it uses |
|---|---|---|
| `apply` | Apply lane | apply-assist, tracker-ops, messenger, legal-visa |
| `focused` | Focused lane | role-analyst, company-researcher, prospector, messenger, resume-tailor, resume-editor, base-comparer, technical-recruiter-reviewer, technical-ai-reviewer, hiring-manager-persona, legal-visa, tracker-ops, apply-assist |
| `hunt` | Hunt lane | job-scout, platform-scout, prospector, company-researcher, fit-scorer, legal-visa, messenger |
| `posts` | Posts lane | prospector, messenger |
| `prep` | Prep lane | prep-coach |
| `recruiter` | The Recruiter | role-analyst, fit-scorer, tracker-ops, corpus-keeper, legal-visa, messenger, prospector, job-scout, company-researcher |
| `research` | Research lane | role-analyst, company-researcher, prospector, fit-scorer, legal-visa |
| `resume` | Resume lane | resume-tailor, resume-editor, base-comparer, hiring-manager-persona, technical-ai-reviewer, technical-recruiter-reviewer, ai-screener, corpus-keeper, role-analyst, company-researcher, prospector, fit-scorer, legal-visa |

### Skills (52)

Invoked as `/jaja:<skill>`.

| Skill | Use it when |
|---|---|
| `add-experience` | Use in the owner's resume session, whenever the owner describes new work (a job, role, project, award, talk, or a date or title change), or for the record interview ... |
| `apply-assist` | Use when an application row is ready (the base, or a variant that won): build and lint its apply pack, fill the employer-site form from the pack in the apply lane's own ... |
| `apply-sprint` | Use when the owner approves a big push (for example 20 applications in one sprint) that is too large for one session and must run as a lane programme with one lane per ... |
| `ats-check` | Use after every resume PDF render, before the editorial verdict is signed, when a vendor parse (Workday, Greenhouse, Lever, Ashby, iCIMS, Taleo, SuccessFactors) is in ... |
| `base-compare` | Use after a variant renders and before anyone reads it: judges every changed bullet and order blind against the current base for this JD (shuffled A/B, key sealed) and ... |
| `browser-job-search` | Use only when the owner is present and wants to search job boards in the owner's browser (LinkedIn, Wellfound, HiringCafe, WTTJ, YC, Glassdoor, Indeed, Built In ... |
| `build-resume-base` | Use only when the owner orders a change to the one general base resume (resumes/bases/general/: new work, a ruling) or asks for a base designed for a specific title ... |
| `build-resume-pdf` | Use when a resume YAML (a variant of the general base, or a base change the owner ordered) needs its PDFs (and a DOCX for Workday), when the page count must be checked ... |
| `calendar-hold` | Use when mail-triage finds an interview invite or scheduling email with proposed times, or the owner confirms a slot in a thread, to request a tentative hold on the ... |
| `connector-search` | Use in a session where the owner has added the Indeed, ZipRecruiter or Dice connector in Claude: run the shared alert queries through each connector's search tool and ... |
| `corpus-audit` | Use weekly (before the weekly review), the day the owner rules on profile conflicts, and after an add-experience batch: evidence ids resolve, every corpus folder is ... |
| `daily-job-plan` | Use each morning (js-morning-brief, or when the owner starts the Recruiter) and evening (js-evening-review), or when the owner asks what the job search does today: the ... |
| `debrief` | Use right after an interview round or recruiter call, when the owner reports how it went, or when interview feedback or a rejection reason arrives and the story bank and ... |
| `doctor` | Use when something looks off (stale status, broken link, drift between a card and its generated agent), after any workshop skill, before a commit, or when the watchdog ... |
| `draft-reply` | Use when a reply batch plan is ready in state/batches/, when a prospect has written back and needs an answer, or when a thank-you or an approved negotiation note is due ... |
| `editorial-review` | Use when a resume variant (or a change the owner ordered to the general base), cover letter or why-us note is ready for the editorial gate, after the tailor answered ... |
| `follow-up-sweep` | Use each morning (routine js-follow-up-sweep) or when the owner asks who is waiting on a nudge, to draft the human nudges due at 4 and 9 business days (never more than ... |
| `gmail-sync` | Use when the owner, the Recruiter's sweep or a daily check starts a mail pass (mail_intake.py), right after reply batches go out, or when the owner asks whether anyone ... |
| `harvest-inbound` | Use for the one-time Gmail backlog harvest (inbound mail since a start date the owner names) or the weekly safety-net pass over the week's harvested mail, when ... |
| `hunt-daily` | Use each morning after the fresh-roles review (js-hunt-review), when the owner asks what new roles came in, or when the dashboard's Fresh roles section lists finds: work ... |
| `job-dashboard` | Use when DASHBOARD.md or the job-search metrics must be refreshed, when the owner asks where the search stands, after a batch of tracker events, or weekly for the funnel ... |
| `lane` | Use when work is split across more than one session: starting/resuming an orchestrator, a lane or a sub-lane, handing work between sessions, or integrating a lane's ... |
| `lane-orchestration` | Use whenever work on any project is split across more than one session: starting or resuming an orchestrator, a lane or a sub-lane |
| `letters` | Use when an application asks for a cover letter, a 'why are you interested in <company>' answer or an 'anything else you'd like to share' field: the owner's own ideas in ... |
| `linkedin-assist` | Use when the owner prefers to send approved LinkedIn items personally (the hand-off mode |
| `linkedin-draft` | Use when the owner wants a LinkedIn profile section, a proof post or a comment drafted, when the approved master profile changes, or on the first working day of a month ... |
| `mock-interview` | Use only when the owner asks for a mock interview or practice round (coding, LLM and agent system design, ML fundamentals, behavioral), usually from a prep pack the day ... |
| `network-daily` | Use on a weekday in the Recruiter's day, or when the owner asks who to connect with or what to comment on: builds the day's network plan (connection suggestions ... |
| `new-automation` | Use when something should happen in reaction to an event (session start or end, a tool call, a file change, a new inbox item, a routine finishing, a webhook) rather than ... |
| `new-role` | Use when the owner wants to hire a new agent role (agency-wide or project-owned) or asks whether something should be a role at all |
| `new-routine` | Use when something must happen on a schedule (cloud, GitHub Actions or the desktop app) or the owner asks to automate a recurring job |
| `new-skill` | Use when a procedure has been repeated by hand twice, or the owner asks to add a skill to the agency or a project |
| `orchestrate` | Use when work is split across more than one session: starting/resuming an orchestrator, a lane or a sub-lane, handing work between sessions, or integrating a lane's ... |
| `page-review` | Use when a variant has passed the scripts, the editor and base-compare: runs the layout gate, then four readers in clean contexts (hiring-manager persona, technical AI ... |
| `parse-linkedin-export` | Use when a new LinkedIn data export has been unzipped into profile/_inbox/linkedin-export/, or the owner asks how to request one, so LinkedIn messages and invitations ... |
| `platform-watch` | Use monthly (js-platform-watch), when the owner names a job platform or tool, or when the alert review list shows an unmapped sender, to vet platforms' terms and ... |
| `prep-pack` | Use when an application reaches screen, oa, interviewing or final, when an interview invite lands, or when the owner asks to prepare for a specific interview round |
| `prospect-search` | Use within 24 hours of an application reaching applied, when a dream or strong target company has a fresh fit role and no warm path yet, or when the owner says "find me ... |
| `research-company` | Use when a queued application's company dossier is missing or older than 30 days, before an interview round, when the owner names a company, a role or a referral target ... |
| `research-person` | Use when one person must be found and understood before any outreach: the exact person verified across two independent sources, what they build and write about, the ... |
| `resume-for` | Use when the owner, the Recruiter or a lane wants the page for one role, at one of three depths (charter): dream (full pipeline), focused (package, reorder from the ... |
| `role-package` | Use when a role is chosen and needs to be understood before any resume is written: the role-analyst picks the lens, decodes the JD into a ranked coverage matrix and ... |
| `score-fit` | Use when a job in jobs/ has passed the hard gates or was sent to review and needs a fit report, when the owner asks whether a specific role is worth applying to, or when ... |
| `scout-jobs` | Use when the js-scout routine runs, when the owner asks for fresh AI/ML postings, or when targets/companies.yaml or sources.yaml changed and the job feed (boards, APIs ... |
| `setup-job-alerts` | Use when the owner sets up or changes job alerts on a platform (the one-time steps per platform in targets/platforms.yaml), or when a platform's alerts stop arriving, to ... |
| `sota-watch` | Use monthly (js-sota-watch) or when the owner asks what changed in agentic engineering tools or AI/ML hiring, to propose approval-gated updates to the market research ... |
| `sync` | Use after editing any role card, skill, hook, MCP config or AGENTS.md, and whenever the doctor reports drift |
| `tailor-resume` | Use when an application is queued (fit at or above the queue threshold in the policy config, or the owner's override) and needs a tailored resume variant, cover letter ... |
| `track-application` | Use when anything happens to an application: the owner says they applied, an email or calendar event arrives, a stage changes, a round is scheduled, or when classified ... |
| `triage-prospects` | Use after a harvest adds prospects at status new, when a prospect's record changes with new inbound, or each morning before the reply batch, to score warmth, fit ... |
| `visa-check` | Use when a company, job or offer raises a work-authorization question (sponsorship wording, E-Verify, I-983, remote worksite, start date), when the owner needs the ... |
| `x-channel` | Use for anything on X (Twitter): read a post and its replies count, look up a person, search or run the hiring watch list, or prepare a follow, DM, reply or post for the ... |

### Routines (16, all disabled)

| Routine | Schedule (cron) | What it does |
|---|---|---|
| `js-apply-batch` | `15 8 * * 1-5` | Retired (charter section 7: the apply lane does this work after the owner's batch yes) |
| `js-backlog-harvest` | `30 7 * * 1` | Every week, run the safety-net harvest of the week's recruiter, founder and hiring-manager mail (and a new LinkedIn ... |
| `js-evening-review` | `30 18 * * 1-5` | On weekday evenings run python3 scripts/evening_wrap.py --write: the one evening file, state/recruiter/wrap-<date>.md ... |
| `js-follow-up-sweep` | `0 8 * * 1-5` | On weekday mornings, draft the nudges that are due (the cadence in charter section 6 and config/defaults.d |
| `js-h1b-cache-refresh` | `0 5 1 * *` | Monthly, refresh the USCIS H-1B cache, re-enrich job records and re-verify every board token in the watchlist |
| `js-hunt-boards` | `0 7-22/3 * * *` | Every few hours during the day poll every company board (Greenhouse, Lever, Ashby, Workday, Jibe, sitemaps ... |
| `js-hunt-review` | `30 7 * * *` | Each morning capture every JD a terms-safe channel reaches (jd_capture.py, no Firecrawl), re-score, then write the ... |
| `js-hunt-signals` | `0 7 * * *` | Each morning read the day's hiring signals: SEC Form D raises by watch-list and AI companies (funding) and the listing ... |
| `js-hunt-x` | `0 8 * * 1-5` | On weekday mornings search X through the official API for new hiring posts (targets/x-watch.yaml: the hiring searches ... |
| `js-mail-triage` | `0 7-22 * * *` | Hourly during the day, classify new job-related mail with the L0 to L3 ladder, match pipeline mail to applications in ... |
| `js-morning-brief` | `45 6 * * *` | Each morning run the first half of the Recruiter's day (docs/AGENCY-CHARTER.md section 3), records only and without ... |
| `js-platform-watch` | `0 7 3 * *` | Monthly, check the job-platform registry, write each platform's 30-day yield, re-read terms that are due, vet new job ... |
| `js-presence-watch` | `0 8 * * 1` | Weekly, check that each public surface in state/presence/portals.yaml says the current truth and is watched, re-read ... |
| `js-scout` | `0 6 * * *` | Each morning refresh the outbound job feed (ATS boards, Adzuna, the HN thread on days 1 to 5), dedup, enrich with H-1B ... |
| `js-sota-watch` | `0 7 2 * *` | Monthly, research what changed last month in agentic engineering (Claude Code, Agent SDK, Codex, MCP, Agent Skills ... |
| `js-weekly-metrics` | `0 16 * * 0` | On Sundays regenerate the dashboard and write the weekly funnel report: interviews booked this week against the weekly ... |

<!-- END GENERATED TABLES -->

## Optional extras

The core needs only Python 3.11+, PyYAML and `requests` (plus `pytest` for the tests). Everything else is opt-in; install
what you use with `pip install -r requirements-optional.txt` (or pick lines from it):

| Feature | Packages |
|---|---|
| Gmail and Google Calendar scripts | `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `google-api-python-client` |
| Resume rendering (RenderCV + Typst, LaTeX fallback) | `rendercv[full]`, `jinja2`; a LaTeX engine for the fallback |
| PDF and Word checks | `pypdf`, `pdfminer.six`, `python-docx` |
| Phone number lint | `phonenumbers` |

Tests that exercise an extra are marked (`needs_google`, `needs_rendercv`, `needs_jinja2`) and are skipped, never failed,
when the package is missing. `python3 scripts/doctor.py` reports which extras are present.

## Connectors

None are required. `docs/SETUP-CONNECTORS.md` walks through each: Google OAuth (Gmail read, compose, send and
Calendar are separate tokens), Firecrawl, the X API, Claude in Chrome and Playwright. Tokens live in
`~/.config/jaja/` (mode 600) or the OS keychain, never in the repo or your data folder. If you use more than one
Claude account, connector tool ids differ per account; the guide shows how to add yours to a lane's `tools_allow`.
`python3 scripts/connectors_doctor.py` shows which are configured (names only, never values).

## Checks: doctor, tests, lint, sync, leak guard

| Command | What it proves |
|---|---|
| `python3 scripts/doctor.py [--data PATH]` | the whole install: Python, data folder, config, cards, hooks, adapters, MCP server, connectors, routines, leak guard |
| `python3 scripts/kernel/lint.py` | schemas, skills, tags, rulings registry |
| `python3 scripts/kernel/sync.py --check` | generated adapters are current (`sync.py` regenerates) |
| `python3 scripts/check_records.py` | every record in your data folder matches its schema |
| `python3 -m pytest scripts -m "not real_records and not workspace"` | the suite, on the demo data |
| `python3 tools/leak_check.py [--staged]` | nothing personal in tracked files (+ gitleaks when installed) |

Two test markers: `real_records` (needs a real profile in your data folder) and `workspace` (needs the owner's own
vault layout); neither runs on the demo.

## Repository layout

```
agents/        role cards: the source, and also the agents Claude Code loads from the plugin (each card carries native description/tools fields that sync keeps in step)
role-references/  rubrics and gates the cards cite
skills/        procedures, invoked as /jaja:<name>
lanes/         lane definitions: AGENT.md + LANE.yaml per lane
playbooks/     method guides (outreach, send-after-yes, portals/*)
routines/      scheduled work, all enabled: false
hooks/         hooks.json + the guards (generated files, one writer, denied commands, lane context, browser lease)
mcp/           job-search MCP server (reads your records, writes drafts and approvals, never sends)
scripts/       the working scripts; scripts/kernel = sync, lint, sweep, approvals, hub tools; scripts/tests
dashboard/     local dashboard (python stdlib http.server on 127.0.0.1)
schemas/       record and card schemas      templates/   new-card/skill/routine templates, data-folder templates, lane-orchestration hub
config/        neutral defaults the code reads (defaults.d/*.yaml)
examples/      demo-data: a synthetic candidate
docs/          the charter and the connector setup guide
evals/         the eval harness (write your own cases; none ship)
tools/         init-data, leak check, migration helpers, pre-commit
.claude-plugin/  .mcp.json   plugin manifest and MCP config        .claude/  .codex/  .agents/   generated adapters
```

## Codex and other engines

Claude Code is the supported engine. The files are engine-neutral (cards, skills, hooks.json, MCP config) and
`sync.py` also writes Codex adapters (`.codex/agents/*.toml`, `.codex/hooks.json`, `.codex/config.toml`,
`.agents/skills/`). They are generated and checked for drift but not tested end to end; treat them as a starting point.

