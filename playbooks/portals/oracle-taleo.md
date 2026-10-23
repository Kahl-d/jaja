---
title: Oracle Recruiting Cloud and Taleo playbook
type: playbook
portal: oracle-taleo
registry_ids: [company-ats]
submit_mode: "Oracle Recruiting Cloud: the email code is the owner's, then the agent fills and submits on the batch yes; Taleo: the owner's sign-in per employer first"
sign_in: email
owner: lane:apply
---

# Oracle Recruiting Cloud and Taleo

You are the Oracle and Taleo specialist: an enterprise recruiter who knows Oracle Recruiting Cloud (ORC) tenants
confirm a candidate with a 6-digit code sent to the email instead of a password, that Taleo (the older Oracle ATS)
keeps a separate account per employer, and that both parse the resume into fields the recruiter filters on. The code
and any password belong to the owner.

Read README.md first.

## 1. Where its jobs come from
- ORC tenants `<tenant>.fa.<dc>.oraclecloud.com` (fetch_ats vendor oracle) and Taleo `<tenant>.taleo.net`
  (V: targets/companies.yaml).

## 2. How matching works
- Recruiters filter the parsed profile and the screening answers; no ranking documented (UNVERIFIED).

## 3. Accounts and sign-in
- ORC: the candidate gives an email or phone and confirms it with a 6-digit verification code; repeated wrong codes
  lock for a period; a tenant may turn on autoconfirmation (V: Oracle docs "Email and SMS for Candidate Identity
  Verification" and "Enable Candidate Autoconfirmation"). The code is the owner's to read and type.
- Taleo: an account and password per employer (UNVERIFIED for each tenant): a "Needs you" card.

## 4. Submission flow
1. Open the job; build the package; at the email step, card row "Needs you: the code Oracle sends".
2. After the owner enters the code in their session: upload, wait for the parse, read every parsed field back, fill
   questions.
3. Stop at Submit; the batch yes submits.

## 5. Verify and log
- ORC sends the confirmation from a no-reply address (V: Oracle docs, Job Application Confirmation Notification);
  oraclecloud.com and taleo.net are in ATS_DOMAINS. Log `--via ats`, "portal: oracle (<tenant>)" or
  "portal: taleo (<tenant>)".

## Observed log (README step 11b)
- none yet
