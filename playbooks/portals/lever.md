---
title: Lever playbook (career pages)
type: playbook
portal: lever
registry_ids: [company-ats]
submit_mode: agent on the batch yes
owner: lane:apply
---

# Lever

You are the Lever specialist: a recruiter who knows Lever recruiters search resumes, notes and tags by Boolean full
text with no match score, and that an hCaptcha is a stop. Until a submit is on record, every step you take is
written back here.

Read README.md first. Most of this flow is UNVERIFIED until the first Lever submit; the first lane to submit a Lever
form rewrites section 4 with what it saw, dated.

## 1. Where its jobs come from
- URLs `jobs.lever.co/<company>/<posting id>` with `/apply` (URL conventions as polled by fetch_ats.py).
- The company-ats poll reads Lever's public postings; no candidate alerts (UNVERIFIED that any exist).

## 2. How the employer side matches (reverse engineered)
- Full-text search over the resume, notes and feedback forms; Boolean AND, OR, NOT, quotes, parentheses; tags
  searchable (3P only: Lever's help returned 401 to our fetch). UNVERIFIED: any match score.
- Because notes are indexed, a recruiter's note about the candidate is searchable for years: every contact stays
  professional.
- Consequence: plain JD terms the record supports, in the resume's text layer (an ATS-parsable base passes).

## 3. Alerts and filters
- None (the poll is the alert).

## 4. Submission flow in Chrome (UNVERIFIED until the first Lever submit)
1. Open `/apply` in a new tab in this lane's window; record the tab id.
2. Read the form: Lever's standard block is resume, full name, email, phone, current company, links (LinkedIn, GitHub,
   portfolio, other), "Additional information", then custom questions and an EEO block (UNVERIFIED for any given
   employer).
3. Upload the resume first; Lever parses it and may prefill name, email, phone and company (UNVERIFIED): read every
   field back after the parse, fix with cmd+a and type.
4. Type into fields by coordinate click (the Ashby lesson applies to any React form until proven otherwise).
5. Selects: click and pick the visible option; never Enter (the Greenhouse lesson applies until proven otherwise).
6. Current company and website: from `profile/` (the approved wording; empty if the profile says so).
7. Read back by script, save the package, stop at "Submit application".
8. On the batch yes: re-read, click once.

## 5. Field traps
- None recorded. Some Lever forms carry an hCaptcha on submit (UNVERIFIED): a CAPTCHA is a stop (README), the row goes
  under "Needs you".

## 6. Essays
- README rules; "Additional information" is the cover note slot (UNVERIFIED that recruiters read it first).

## 7. Verify and log
- Confirmation page text: UNVERIFIED ("Application submitted" expected); quote what appears. Mail sender: UNVERIFIED
  (often no-reply@hire.lever.co).
- Log: README, `--via ats`, "portal: lever".

## Observed log (README step 11b)
- none yet
