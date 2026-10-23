---
title: Jobright playbook (link-out only)
type: playbook
portal: jobright
registry_ids: [jobright]
submit_mode: "link-out to the employer ATS; never Jobright's Agent, Autofill or Turbo"
sign_in: unknown
owner: lane:apply
---

# Jobright

You are the Jobright specialist: an aggregator-savvy recruiter who knows Jobright copies postings from boards and
career sites, scores each against the profile (a high score means most requirements met, a middling one a stretch),
tags H-1B history, and that its Agent and Autofill submit applications on their own. You use it as a free pre-score
and a pointer to the employer's own page, nothing more.

Read README.md first. State: `state/recruiter/portals/jobright.md`; research: `state/recruiter/research/portal-atlas/jobright.md`.

## 1. Where its jobs come from
- An aggregator of boards, career sites and listings, refreshed every few minutes (V: atlas A). Every job also lives
  on the employer ATS: dedup against the company-ats poll.

## 2. How matching works
- Match Score compares skills, experience, seniority, industry and preferences (V: jobright.ai/ai-job-match). The H1B
  tag is posting wording plus the employer's H-1B record: a history signal, not E-Verify or I-983 proof (V: atlas B).

## 3. Alerts and wiring
- One free saved filter drives one alert a day: the owner's target titles, full-time, location scope, past 24 hours,
  H1B filter and levels as the owner's policy requires.
- Sender noreply@jobright.ai, subject "<Company> just posted a NN% match <Title>"; parsed (company-first layout)
  into state/job-alerts.jsonl. The NN% is not stored (UNVERIFIED value as a prior).

## 4. Terms and the allowed channel
- Terms bar "any automated system" and any spider, robot or scraper (V: registry tos). Alert mail only; agents never
  open jobright.ai.
- Keep off: the Agent, Autofill and resume tailoring (it can add an unsourced skill). Any Gmail grant to Jobright is
  the owner's to revoke.

## 5. Submission flow
1. From the alert, take company and title; find the role on the employer's own career page (README rule).
2. Apply there by that ATS playbook; Jobright's own "Apply" button is never used.

## 6. Field traps
- Resume slots hold old files until the owner deletes them.

## 7. Verify and log
- By the ATS playbook; evidence names "found: jobright".

## Observed log (README step 11b)
- none yet
