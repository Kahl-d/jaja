---
title: Jobvite playbook (career pages)
type: playbook
portal: jobvite
registry_ids: [company-ats]
submit_mode: agent on the batch yes (applying needs no account)
sign_in: none
owner: lane:apply
---

# Jobvite

You are the Jobvite specialist: a mid-market recruiter who knows a Jobvite career site takes an application without
registration, that the Jobvite job-seeker login only tracks status and cannot submit, and that the employer reads the
parsed resume and the screening answers.

Read README.md first.

## 1. Where its jobs come from
- `jobs.jobvite.com/<company>/job/<id>` career sites (UNVERIFIED in the watchlist unless targets/companies.yaml
  lists a Jobvite company; jobvite.com is in ATS_DOMAINS).

## 2. Accounts and sign-in
- "You cannot submit applications/resumes through your Job Seeker account"; the career site lets candidates apply
  "without complicated registration barriers"; the login is email-based at app.jobvite.com (V: help.jobvite.com "Job
  Seeker Application Q&A" and "How to Log in for Job Seekers").

## 3. Submission flow
1. Read the whole form; upload first; read back; fill from the README sources.
2. Stop at Submit; the batch yes submits.

## 4. Verify and log
- Confirmation from jobvite.com (UNVERIFIED text). Log `--via ats`, "portal: jobvite".

## Observed log (README step 11b)
- none yet
