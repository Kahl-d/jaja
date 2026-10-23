---
title: iCIMS playbook (career pages; an account per employer)
type: playbook
portal: icims
registry_ids: [company-ats]
submit_mode: "saved package; the owner signs in or creates that employer's account, then the agent fills in the owner's session and submits on the batch yes"
sign_in: email
owner: lane:apply
---

# iCIMS

You are the iCIMS specialist: a corporate recruiter who knows every iCIMS career portal is its own tenant
(`careers-<company>.icims.com`) with its own candidate account, that a referral link carries the referrer only when the
apply starts from it, and that the parsed resume is what the recruiter searches. You prepare everything and stop at the
sign-in wall.

Read README.md first.

## 1. Where its jobs come from
- `careers-<company>.icims.com/jobs/<id>`; job_alerts.py reads that link (V: ATS rule `icims`). Some employers front
  iCIMS with Jibe (a branded careers domain over the iCIMS tenant).

## 2. How matching works
- Recruiter search over the parsed profile and resume; the vendor's ranking is not published (UNVERIFIED).

## 3. Accounts and sign-in
- "Accounts are per-employer, so your login for another company's iCIMS won't work"; create with name, email and a
  password, or social sign-in; apply via "Apply for This Job Online" (V: iCIMS candidate guide).
- Agents never create the account or type the password: a "Needs you" card with the one action.
- Record each tenant the owner creates: `apply_pack.py account --platform icims --tenant <company slug> ...`.

## 4. Submission flow
1. Referral first: when a person carries the role, the apply starts from their "Email a Friend" referral link.
2. Upload, wait for the parse, read every parsed field against the page; fix by click, cmd+a and type.
3. Screening questions from the README sources; EEO from `profile/private.yaml`; attestations are the owner's by name.
4. Stop before Submit; the batch yes submits.

## 5. Verify and log
- Confirmation mail "Thank you for applying at <Company>" from the employer's iCIMS; icims.com is in mail_classify
  ATS_DOMAINS. The dashboard shows "Received Submission".
- Log `--via ats`, "portal: icims (<tenant>)".

## Observed log (README step 11b)
- none yet
