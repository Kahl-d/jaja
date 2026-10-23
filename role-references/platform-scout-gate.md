# platform-scout: Gate (rubrics)

Moved from the platform-scout card to keep it lean; the card binds it as if inline.

**Channel** (first row that holds wins; evidence quoted from the platform's own page):

| Evidence | access | Who acts |
|---|---|---|
| documented public API or feed, terms allow this personal use | `api` / `rss` | a script, once engineering builds it |
| the platform emails job alerts to the owner | `email-alert` | `job_alerts.py`, from the harvest |
| general anti-bot terms | `browser` | job-scout reads pages the owner opens, with the owner present |
| terms name AI or intelligent agents | `manual` | the owner reads; job-scout records what they report |
| every channel forbidden, the site gone, or it charges the owner to be seen | `blocked` | nobody |

`tos.quote` is the clause that grants or forbids the chosen access (from the API or developer terms page when it lives
there, with that URL). Good: "'Use bots or other unauthorized automated methods to access the Services' ·
https://www.linkedin.com/legal/user-agreement · read YYYY-MM-DD · verified yes." Bad: "LinkedIn doesn't allow scraping."

**Keep or retire** (30 days of `platform_yield.py`): queued 1+ in the month: keep, `probation` to `active` after two
such months; new 1+ and queued 0 for two months: keep on probation, tighten filters; new 0 for two months with
`alerts_on`: retire; mostly duplicates (new under 20% of fetched): keep only if it surfaces jobs first (`first_seen`).
Tightening: an `email-alert` platform's own filters are the owner's steps (a Needs you line; `alert_setup` updated once
he confirms); an `api` or `rss` platform's filter is its `queries:` list in `sources.yaml` (job-scout edits it); with no
query list, the title filter in `targets/roles.md` or the watchlist in `targets/companies.yaml`, owner named.

**Standing rule for status changes.** A retirement or promotion is a proposal: set the proposed `status` with
`proposed: {from: <old status>, date: <watch date>}`; list it under `Proposals` with its numbers; open one `issues.md`
row for the Recruiter: "ratify or revert at the Monday review <date>" (first Monday on or after the watch). On the
ruling: ratified, drop `proposed:`; reverted, restore `from` and undo the retirement fields. `--check` errors on a
malformed marker and warns once the Monday has passed. Immediate (no proposal): new entries at `probation`, `manual`
for terms naming agents, `blocked` or narrower `access` for forbidding terms.
**A retirement:** `status: manual` with the marker and `setup_priority: none`; `access` and `alert_senders` stay
(history, and late alerts keep their platform); `alerts_on` keeps its date until the owner confirms deletion, then null
with "alerts deleted <date>" in `notes`. The deletion is a Needs you line after ratification; ask job-scout to
switch off the platform's `<id>-alert` source (a shared generic `email-alert` source stays); carry `--check`'s
"switch off" warning into `Next:`.
