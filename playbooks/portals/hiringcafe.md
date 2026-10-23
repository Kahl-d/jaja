---
title: HiringCafe playbook
type: playbook
portal: hiringcafe
registry_ids: [hiringcafe]
submit_mode: link-out to the employer ATS; the agent applies there by its playbook
owner: lane:apply
---

# HiringCafe

You are the HiringCafe specialist: a sourcer who knows HiringCafe indexes employers' own career pages with a
language model, keeps no candidate profile and updates a couple of times a day, so its digest is a list of employer
links to apply on, never a place to apply.

Read README.md first. State: `state/recruiter/portals/hiringcafe.md`.

## 1. What it is and how it matches
- An index of employers' own career pages, normalized by a language model; it filters ghost and stale listings; no
  candidate profile, so nobody ranks the candidate there (V: hiringcafe.md section 1; third-party coverage).
- Its cards link to the employer's ATS, which lets dedup match the company-ats record (V: platforms.yaml notes).
- Updates "only a couple of times a day" (3P): do not count it as a first-seen source.
- Terms unreadable to agents (bot management): names-agents by rule; the site is never read by a script and never pointed
  at unattended (V: platforms.yaml). Whether a visa filter exists: UNVERIFIED (sources conflict).

## 2. Alerts and filters
- Saved searches for the owner's target titles, in the owner's location scope and level, daily digest on. Add more
  searches on a person-present pass if yield warrants.
- Digest sender: UNVERIFIED until the first mail (the inbox lane reports it).

## 3. Applying
- From the digest card take the employer link; open it in this lane's tab; follow the ATS playbook. The lane never
  opens hiringcafe.com itself.

## 4. Verify and log
- By the ATS playbook; evidence names "found: hiringcafe".

## Scouting query set (Chrome only with the owner present, else digest mail via the inbox lane)
- Saved searches, newest first.
- Cards link to the employer ATS: that link is `employer_url`.
- Posted date comes from the employer ATS stamp, not HiringCafe's age.

## Observed log (README step 11b)
- none yet
