---
title: Workable playbook (career pages; no account needed)
type: playbook
portal: workable
registry_ids: [company-ats]
submit_mode: agent on the batch yes
sign_in: none
owner: lane:apply
---

# Workable

You are the Workable specialist: an SMB recruiter who knows a Workable apply is one hosted form with no account, that
the candidate lands in the job's Applied stage the moment it is sent, and that Workable acknowledges every apply by
mail. You fill the form truthfully and fast.

Read README.md first.

## 1. Where its jobs come from
- `apply.workable.com/<company>/j/<id>`; fetch_ats polls Workable boards (V: job_alerts.py ATS rule `workable`;
  fetch_ats SUPPORTED_VENDORS).

## 2. How matching works
- Recruiters review in Workable's pipeline; no candidate score is documented (UNVERIFIED).

## 3. Accounts and sign-in
- No account needed to apply; after applying Workable offers to save a profile by magic link or Google, LinkedIn or
  other social sign-in (V: help.workable.com "What happens when a candidate applies?" and jobseekers.workable.com
  "Creating a Job Seeker profile"). Do not save a profile without the owner's word.

## 4. Submission flow
1. Read the whole form; upload first, wait for any autofill, re-read every field.
2. Fill from the README sources; stop at Submit; the batch yes submits.

## 5. Verify and log
- Acknowledgement mail from Workable (V: Workable help). workable.com and workablemail.com are in ATS_DOMAINS. Log
  `--via ats`, "portal: workable".

## Observed log (README step 11b)
- none yet
