# Dashboard as the agency's CRM: design note

Mock: `dashboard/design/mock_template.html` (static, one file, opens offline; `?page=pipeline`,
`?theme=light|dark`). The mock is a template: fill its embedded data from a real `dash_api.build` snapshot, read only.

**Goal:** open it in the morning and know in 10 seconds what changed and what to do. Very minimal, modern, "like a
CRM", only meaningful numbers, real tables underneath.

## Home: one screen at 1440x900, top to bottom by urgency
| block | what it shows | source | build adds |
|---|---|---|---|
| Next 48 hours | rounds with prep-pack link or "no prep pack", replies waiting on you, approvals waiting, follow-ups due, calendar state | applications in screen/interviewing, prep/packs/, work/approvals status pending, follow_ups | `band48` in dash_api; calendar via calendar_events agenda when the token works, else "not read" |
| KPIs | **KPI one: interviews booked this week against the weekly target** ("target not set" until you set it); then Applied, interviews per application warm vs cold, screens, days to apply (median), days left; each with n and a trend vs last week where the weeks table has it | overview.funnel, weeks, response_by_via, apply_latency_hours, clock | one Applied count everywhere (funnel.applied) |
| Since your last visit | stage moves, replies, rounds, pages ready; counted in the title, newest first, 6 lines then "+N more" | tracker events | `updates` (last 7 days of events); "last visit" kept per viewer in localStorage, filtered client side |
| What the numbers say | at most 3 rule-computed lines, each with n; "too few to say" under n = 10 | response_by_via, apply_latency_hours, tiers | `findings` (rules in dash_api, not prose) |
| 90-day tracker | day N of 90, week marks, applications, replies and rounds plotted by day; events before day 1 summarized | clock + tracker events | `plot` (per-day counts) |
| Next 7 days | rounds by day; calendar events once readable | as above | joins the agenda |
| Today | tasks: round prep, approvals, follow-ups due, next actions due today | as above | `tasks` |

## Pipeline: deals by stage (board, table toggle)
Columns follow tracker stages from Scored to Offer (package-only folders stay off the board). A card: company, role,
days in stage (from the tracker's last transition), warm path (people on the role), fit, next action and date. The table
has sticky headers, sort, filter; a row opens the drawer. Header line: next-up rows checked against their applications
(APPLIED or STALE marks, the tracker's applied count with a drift flag, the mode line).

## Design bar
One system font stack, one accent, neutral greys, light and dark from tokens; 8 px grid, cards with one border and no
boxes inside boxes; numbers right-aligned with n; missing says which ("not read", "no prep pack", "missing"); left nav
with five pages (Home, Pipeline, Companies, People, Jobs); `/` search, j/k rows; 375 px stacks to one column. Detail
opens in a drawer, never a new page. Writes stay the safe ones (tags, stage through tracker.py).

## Data gaps a first mock tends to expose
1. **Calendar:** when the token is expired the build shows "not read", never 0.
2. **Round times are not on record:** the tracker holds "screen", not its time; the calendar is the only source.
3. **"Replies waiting on you" is noisy** if it counts every "answer owed" follow-up, including old portal messages.
   Count only replies from prospects or companies on the pipeline; older ones go in the drawer.
4. **Matching next-up rows to applications by company and title misses** when names differ; match by the row's job link.
5. **Trends** exist only where the weeks table has last week's value (applied, replies, interviews).
