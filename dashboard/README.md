# Job agency dashboard

A minimal, clean, local dashboard for the agency, on the same files the Obsidian views read, with controls for
tags, people on roles and stage changes, and above all the stats that say whether the search is working. Nothing
outward exists in it: no send, post, submit or connector call.

## Run it

```
cd <your agency data folder>
python3 <path to this repo>/dashboard/serve.py            # opens http://127.0.0.1:8765/
python3 <path to this repo>/dashboard/serve.py --port 9000 --no-open
```

Python standard library only (plus PyYAML, which the agency's scripts already use). It binds 127.0.0.1 and
nothing else. Use `--no-open` when you do not want a browser tab. Each run makes a new token
(`secrets.token_urlsafe`), puts it in the page, and every write (POST) must carry it in `X-Dashboard-Token` with a
Host of 127.0.0.1 or localhost; reads need no token. Stop it with Ctrl+C.

**Speed.** `jobs/` is parsed once into `state/cache/dashboard/jobs.pickle` (gitignored), keyed by each file's mtime
and size, so a restart re-parses only what changed (`jobs_cache.py`). Each finished snapshot is saved to
`state/cache/dashboard/snapshot.json`; on a cold start the page shows it at once with "Data as of ... (saved).
Refreshing." while one fresh build runs, then swaps it in. Applications, prospects, touches, the next-up list, the
clock and the tags are re-read by themselves when one of them changes; **Refresh** re-reads the postings.

**Obsidian links.** Rows that name a file get an **Open** link. The page builds `obsidian://` links from two
optional `<meta>` tags in `index.html`: `dash-vault` (the vault name) and `dash-data-rel` (where the data folder
sits inside the vault, if it is not the vault root). Both empty: the path is shown as text and no link is built.

**Who "you" is.** Section titles that address the reader ("Needs you") use `identity.owner_label` from your
config (default "you"). The JS keeps to the neutral word "you".

## Files

| File | What it is |
|---|---|
| `serve.py` | the local server: static page plus a JSON API; host check, a per-run token on writes, a confirm flag |
| `dash_api.py` | the data layer: builds the snapshot from the agency's own loaders (below); runs the writes |
| `dash_tags.py` | the single writer of `state/dashboard-tags.yaml` (tags on applications and jobs, segment picks) |
| `crm.py` | the CRM block: KPIs, updates, findings, 90-day tracker, 7 days, tasks, Pipeline, Fresh 10, Companies, timelines, approvals |
| `jobs_cache.py` | the mtime-keyed parse cache of `jobs/` |
| `design/` | the design note (DESIGN.md) and the static mock |
| `index.html`, `app.js`, `style.css` | the shell and the CRM pages: vanilla JS, inline SVG bars, system font, light and dark |
| `ui.js`, `ui.css` | the component library: one component per thing (Person, Deal, Job, Company, Stage, Rate, When, Tags, Pane, Group), used by every page |
| `portals_inv.py`, `portals.js`, `portals.css` | the Portals page: one row per portal from `state/recruiter/portal-inventory.yaml` with a status per setup part, who it waits on (`owner`, `recruiter` or `nobody`) and the next step; a row opens the portal's parts, updates newest first, the owner's clicks (the newest `state/recruiter/*-clicks-*.md`) and the read-back findings; a part the inventory does not record reads "not recorded"; the portal digest sits below as activity |
| `cockpit.py`, `cockpit.js`, `cockpit.css` | the cockpit, the first tab: a workbench of three panes (Next, Coming up, Pipeline) between a status strip (this week, what works) and a footer (the machine, the feed); `/cockpit` redirects to `/#cockpit` |
| `../scripts/tests/test_dashboard_api.py` | tests on fixtures: every number below on known data, the writes, the server's guards |
| `../scripts/tests/test_crm*.py`, `test_dashboard_speed.py` | the CRM on fixtures, the cache and the saved snapshot, and `test_crm_ui.py` in a real Chrome (Playwright; skipped without Chrome) |

## Where every number comes from

No record format is parsed anew. Applications come from `scripts/tracker.py` (folders, event logs, `project`,
`metrics`); postings from `fetch_ats.load_jobs`; the watchlist from `fetch_ats.load_companies`; follow-ups from
`follow_ups.collect`; portals from `portal_digest.digest`; pursuits from `pursuits.rows`; role families from
`focus_profiles.pick`; the clock from `dashboard.clock_status`. Two hub tables have no script reader and are read
by their header row: the next-up table and the example names in the H1 of each `targets/segments/*.md`.

## The CRM pages

Five pages in the left nav (Cockpit, Pipeline, Jobs, Companies, People); the older views stay below a rule. The page
always opens on the Cockpit; a link such as `/#pipeline` opens another page, and back and forward move between
them. Keys: `/` searches, `j` and `k` move the selected row, Enter opens it. Times use the time zone in your config
(`identity.timezone`). Missing data says what is missing, never 0.

| Block | What it shows | Source |
|---|---|---|
| Header | the clock line; the mode line | `clock.yaml` via `dashboard.clock_status`; `crm.MODE_LINE` |
| Next 48 hours | rounds with times and prep packs; replies owed to pipeline contacts; approvals waiting; follow-ups due | `calendar_events.py agenda` (live; "calendar not read" when it fails), tracker rounds the calendar lacks ("time not on record"), `prep/packs/`, `follow_ups.collect`, `work/approvals` (pending and not expired) |
| KPI 1 | interviews booked this week against the weekly target ("target not set" until you set it); its drawer names them | `scripts/interviews.py` (`booked`); the weekly floor from `targets/goals.yaml` (`js_common.weekly_interview_floor`) |
| Other KPIs | applied; interviews per application, warm vs cold, with n; screens; days to apply | `tracker.metrics`, `interviews.rates` (n shown, "small sample" under 10) |
| Last 7 days | stage moves, pages ready, and sent and received touches | tracker events; `touches/*.jsonl` (drafts never show) |
| What the numbers say | at most 3 rule lines with n; "too few to say" under 10 | `crm.findings` |
| 90-day tracker | applications, replies and rounds plotted per clock day | tracker events against `clock.yaml` |
| Today | round prep, approvals, follow-ups, next actions due | the same sources |
| Pipeline | deals by stage (board or table): days in stage from the entering event, years asked, lane, warm names, stopped | `tracker.project`, `fit_gates.required_years`, the lane files, `state/recruiter/stopped.yaml` |
| Jobs | Fresh 10 first: expected interviews with the rates' n, warm names, people "to verify"; then every posting | `fresh10.pick` once per snapshot build; `state/daily/<date>-fresh10-people.md` |
| Companies | accounts: tier, segment, open roles, deals, people, last touch; the drawer adds activity | `targets/companies.yaml`, job rows, applications, prospects, touches |
| People | contacts; the drawer adds the timeline (the record's "## Timeline" lines plus touches), the LinkedIn link and the connections (introduced by, asked by, whom they introduced); the Connections view groups every introduction by the person who opened the door | `prospects/` (`introduced_by`, `asked_by`), `touches/` |
| Approvals drawer | pending approvals by kind and age, each linked to its file; expired apart. Read only: approvals are signed in their own files | `work/approvals/*.md` |

## What each number means (the Numbers page)

- **Applied**: applications whose event log reached `applied` or beyond (tracker.py metrics). **Roles taken on** is
  every folder under `applications/` (records plus package-only folders).
- **Replied**: an applied role with any inbound event (email_in, reply, call, scheduled ...) or a company stage
  (screen onwards, or a rejection). An ATS confirmation is not a reply.
- **Screens, Interviews, Offers**: applications that reached `screen`, `interviewing`, `offer` (a later stage counts
  the earlier ones). Rates are against Applied.
- **Days left**: the clock from `clock.yaml` (scripts/clock.py).
- **Is the search working?** The funnel: postings discovered (duplicates left out), queued, applied, replied,
  screen, interview, offer; each rate is against the step before.
- **Startups, big tech or AI labs?** Every role by its company's segment. The segment comes from the first source
  that has one: your pick in this dashboard, `segment:` in targets/companies.yaml, the named examples of the
  segment lenses, the dossier's `stage:`, the prospects' `company_type` at that company; else unknown.
- **Target companies, truly or not?** How many top-tier companies in targets/companies.yaml have at least one
  application, what share of all applications go to them, how many have any role in work.
- **Are we getting fast interviews?** Median days from applying to the first reply and to the first screen or
  interview, how many came within 7, 8 to 14 or over 14 days, how many have none yet and the longest wait.
- **What kind of roles?** The focus profile (`scripts/focus_profiles.py pick`) of each title; "unmatched" means no
  profile in `profile/lenses/` covers the title.
- **How many for each thing?** By tier: the newest apply-batch sheet that lists the role (`state/apply-batches/`),
  else the next-up rule from your config. Then applications per company.
- **This week against last**: the last 7 days against the 7 before: applications submitted, first replies, first
  screens or interviews, postings discovered, messages sent, roles taken on.
- **Who answers?** Response rate by the posting's source and the applying speed (tracker.py metrics).
- **Data gaps**: what the numbers cannot see yet (missing segments, tiers derived by rule, roles with nobody
  attached, unscored postings, files whose frontmatter does not parse, duplicate records).

## The other views

- **Queue**: the next-up list in the orchestrator's order with fit, tier, state (READY, PACKAGE, JOB, BLOCKED),
  age of the posting, the next action, chance and page; the notes and the mass queue below.
- **Applications**: every role (records and package-only folders), sortable and filterable by stage, tier,
  segment, family and text; click a row for its panel and controls.
- **Jobs**: Fresh 10 first, then the jobs table from the server, filtered (fresh 72 h, the scout's finds, open
  only, fit, company tier, sponsorship, verdict) and paged 200 at a time.
- **People**: prospects (with the roles attached and whether they refer), referrers, the people watch, pursuits.
- **Portals**: the portal digest: new postings, postings on two or more portals, answers owed, follow-ups, setup
  pending, and the messages waiting.

## Controls (writes)

Every write shows the exact command first and runs only on the confirm click. It runs the agency's own script from
the data folder and is logged in `state/dashboard-writes.jsonl`.

| Control | Command |
|---|---|
| Stage event (with the evidence you type, optional time and applied-via; for applied, the resume that went, required) | `python3 scripts/tracker.py event <slug> <type> --source manual --evidence "..." [--at ISO] [--via ...] [--resume <PDF path or sha256, or none --reason "...">]` |
| Attach or detach a person on a role | `python3 scripts/prospects.py set <person> applications+=<slug>` (or `-=`) |
| Tag a person | `python3 scripts/prospects.py set <person> --tag <tag>` (the controlled vocabulary in schemas/tags.yaml) |
| Tag an application or a job | `python3 dashboard/dash_tags.py add <application or job> <id> <tag>` (or `remove`) |
| Set a company's segment | `python3 dashboard/dash_tags.py segment <company> <startup, big-tech, ai-lab, enterprise or none>` |

Applications and jobs have no sanctioned tag field, so their tags live in `state/dashboard-tags.yaml`, which only
`dash_tags.py` writes (history in `state/dashboard-tags.log.jsonl`). `ghosted` is not offered: the tracker infers it.
