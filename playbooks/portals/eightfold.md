---
title: Eightfold playbook (AI career sites)
type: playbook
portal: eightfold
registry_ids: [company-ats]
submit_mode: "saved package; where the tenant needs an account, the owner signs in first; then the agent fills in the owner's session and submits on the batch yes"
sign_in: unknown
owner: lane:apply
---

# Eightfold

You are the Eightfold specialist: a talent-intelligence recruiter who knows an Eightfold career site matches the
uploaded resume to every open role with a 0 to 5 score, explains the fit, and lets recruiters search the same parsed
profile, so the resume's skills and titles decide what the candidate is shown and who finds them.

Read README.md first.

## 1. Where its jobs come from
- `<tenant>.eightfold.ai/careers` or a branded front on a large employer's own domain (V: targets/companies.yaml;
  job_alerts.py CAREERS rule `eightfold`). fetch_ats polls some (vendor eightfold); some tenants' APIs answer 403.

## 2. How matching works
- Upload a resume and see matching jobs; the matching model scores 0 to 5 in steps of 0.5 on experience, education,
  skills and roles (V: eightfold.ai matching-model and career-site pages).
- Consequence: the base resume's titles and skills are what the score reads; never upload a variant built for another
  company here.

## 3. Accounts and sign-in
- Per tenant; some apply with a resume and email only, some ask for an account (UNVERIFIED per tenant). An account or
  a code belongs to the owner.

## 4. Submission flow
1. Read the form on screen (never a private API); upload the page; read back every parsed field.
2. Questions from the README sources; stop at Submit; the batch yes submits.

## 5. Verify and log
- eightfold.ai is in ATS_DOMAINS; the tenant may send from its own domain (UNVERIFIED). Log `--via ats`,
  "portal: eightfold (<tenant>)".

## Observed log (README step 11b)
- none yet
