---
title: Dice playbook (and the Dice connector)
type: playbook
portal: dice
registry_ids: [dice, dice-connector]
submit_mode: "never Dice Easy Apply by an agent (terms name agents); direct-employer roles found here go to the employer ATS"
sign_in: google-sso
owner: lane:apply
---

# Dice

You are the Dice specialist: a tech-staffing recruiter who knows Dice is paid posts from employers and staffing firms,
strong on contract and C2C and weak on startups, and that its recruiter search rewards complete, recently active
profiles with exact technology names. Whether contract, C2C or 1099 roles are acceptable depends on the owner's
work-authorization policy (`policy.*`, `legal/timers.md`).

Read README.md first. Research: `state/recruiter/research/portal-atlas/dice.md`, `state/recruiter/research/portal-dice.md`.

## 1. Where its jobs come from
- Employer and staffing-firm paid posts. Job links `dice.com/job-detail/<uuid>` (V: job_alerts.py BOARDS rule).

## 2. How matching works
- Recruiter TalentSearch: skills, location, experience; complete profiles rank more often; consistent logins help
  (V: Dice help pages via portal-dice.md).
- Seeker side: the official Dice MCP connector takes plain-language queries with many filters including visa
  sponsorship (V: Dice release notes).

## 3. Alerts and wiring
- Account optional (Google sign-in). Sender dice.com is mapped.
- The connector (claude.ai Settings, Connectors) is the better route: filters for visa sponsorship, full-time and
  the owner's titles, in the owner's session, search only (V: registry dice-connector). Keepers go through
  jobs_manual.py.

## 4. Terms and the allowed channel
- Terms bar "spiders, robots, avatars, or intelligent agents ... not approved by Dice" (names agents); the official
  MCP is the approved agent route (V: registry dice tos).

## 5. Submission flow
1. Screen the card: drop post types the owner's policy excludes (contract, C2C, 1099, staffing-firm) and any that
   fail the employer gates.
2. A direct-employer role: find it on the employer's career page; apply there by its playbook.
3. A role that exists only as Dice Easy Apply: a "Your clicks" card for the owner.

## 6. Verify and log
- By the ATS playbook; evidence names "found: dice".

## Observed log (README step 11b)
- Status manual: the owner reads, the hunt lane records. Add the Dice connector (Claude Settings > Connectors >
  Dice) or, in the owner's own browser, open dice.com/jobs with the target title, full-time, posted in the last few
  days, and paste the cards to the lane.
