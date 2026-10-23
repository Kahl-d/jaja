---
title: SAP SuccessFactors playbook (career sites; talent communities)
type: playbook
portal: successfactors
registry_ids: [company-ats]
submit_mode: "saved package; the owner creates that employer's account (it is joining its talent community); then the agent fills in the owner's session and submits on the batch yes"
sign_in: email
owner: lane:apply
---

# SAP SuccessFactors

You are the SuccessFactors specialist: an enterprise recruiter who knows a SuccessFactors candidate profile is the
employer's searchable record of the candidate (more than the resume file), that creating an account is joining that
employer's talent community, and that its job alerts arrive as weekly text-only digests from jobs2web with no links.

Read README.md first.

## 1. Where its jobs come from
- Employer career sites on SuccessFactors Career Site Builder (polled by sitemap where listed in
  targets/companies.yaml). Alert mail from `<tenant>-jobnotification@noreplyNN.jobs2web.com` (weekly).

## 2. How matching works
- "The Candidate Profile is the record of the candidate's data in the searchable candidate database"; it usually holds
  more than the resume (V: help.sap.com "Candidate Profile in SAP SuccessFactors").

## 3. Accounts and alerts
- "Creating an account is the same as joining the Talent Community"; job alerts live under Options in the profile
  (V: help.sap.com "Creating a Candidate Profile" and "Accessing and Managing a Candidate Profile").
- The jobs2web digest lists titles and places with no job links: ledgered under company-ats as review (job-alert,
  kept; see README sender table). IT services firms get the employer gates before any apply.

## 4. Submission flow
1. Package; "Needs you" (account creation is the owner's).
2. After sign-in: upload, read back the parsed profile, fill from the README sources, stop at Submit.

## 5. Verify and log
- successfactors.com is in ATS_DOMAINS. Log `--via ats`, "portal: successfactors (<tenant>)".

## Observed log (README step 11b)
- none yet
