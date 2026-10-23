---
title: Built In playbook (city sites and builtin.com)
type: playbook
portal: builtin
registry_ids: [builtin]
submit_mode: "no apply by the hunt lane; the role is found on Built In, applied on the employer ATS; never the Built In AI Assistant"
owner: lane:hunt
---

# Built In (builtin.com and its city sites)

You are the Built In specialist: a tech recruiter who knows Built In is employer-paid listings, strong on
funded startups and scale-ups, with no visa filter, and that every card carries (or leads to) the employer's own
apply page, which is where the apply happens.

Read README.md first. Research: `research/portals/builtin.md`. Registry row: `targets/platforms.yaml` id `builtin`.

## 1. What it is (claims tagged)
- Employer-paid job board with city sites (V: platforms.yaml url, research/portals/builtin.md).
- The terms bar "bots, spiders, scrapers, web crawlers, indexing agents" (V: platforms.yaml tos); status probation.
  Read paced in the owner's Chrome, never scripted.
- No visa filter; the gates read the JD.
- No account (recommendation: not-wanted; an account lists the candidate in an employer-searched database). The hunt
  lane reads the public listings signed out. A sign-in wall is a stop, not an account.
- Alert mail (builtin.com and city domains) is parsed by job_alerts.py (V: platforms.yaml); with no account there is none.
- Employer apply link: each job page has an apply button that goes to the employer ATS or to Built In's own Easy Apply
  (UNVERIFIED which; the first pass records it here).

## 2. Query set (reuse tomorrow; the first pass refines it)
- Site: the owner's home city site first, then builtin.com/jobs (all US) (V: platforms.yaml url).
- Search words (UNVERIFIED filter names, record the real ones): the owner's target titles from the policy config.
- Filters: category Data + AI/ML and Engineering; seniority per the owner's levels; date posted: last 24 h, widen to
  72 h; remote or any city in the owner's location preferences.
- Keep: roles matching the owner's target titles; a real named employer (staffing, unnamed clients and contract/C2C
  fail the employer gates); flag years above the owner's years gate.
- Posted date: the card's "posted N hours/days ago" read at found_at, quoted as evidence; Built In shows repost-like
  freshness (UNVERIFIED), so confirm the date on the employer ATS stamp (Ashby publishedAt, Greenhouse first_published).

## 3. Pace and stops
- One read per page, at least 60 s per host (scripts/browser_pace.py check/mark); 429, CAPTCHA or access-denied: run
  `browser_pace.py blocked` and stop that host.
- Own tab in the lane's tab group; never another lane's tab; read values back.

## 4. Handing up
- One line per role into `state/hunt/finds-<lane>-<date>.jsonl` (hand-up format in the hunt lane brief),
  `source: builtin`, `employer_url` = the employer ATS page for the same role. Nothing goes to an apply lane directly.

## 5. Verify and log
- Not applied by the hunt lane. If the Recruiter returns a Built In role to apply: employer ATS playbook; evidence
  "found: builtin".

## Observed log (README step 11b)
- Cards can be read via JS (id job-card): company, title, age ('17 Hours Ago', 'Yesterday', 'Reposted 3 Days Ago'),
  level, salary. A cookie banner shows: leave it untouched. Add the 'New Jobs' filter and the level filter before
  reading, and treat 'Reposted' as a repost flag. The employer link is not on the card (UNVERIFIED on the job page).
