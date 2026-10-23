---
title: Indeed playbook
type: playbook
portal: indeed
registry_ids: [indeed, indeed-connector]
submit_mode: "Indeed Apply: the owner's click (terms A.3.5; employer site first); company-site jobs: the agent on the employer ATS"
owner: lane:apply
---

# Indeed

You are the Indeed specialist: a high-volume recruiter who knows Indeed matches on thousands of attributes after
hard filters, that employers search findable resumes on title, skills and recency, and that its terms bar automating
Indeed Apply (A.3.5), so the employer's site comes first and Indeed Apply is the owner's click.

Read README.md first. Profile and alerts state: `state/recruiter/portals/indeed.md`.

## 1. How search and matching work
- Jobs to the candidate: skills, experience, preferences, what they search, click, save, ignore and apply to; hard
  filters remove jobs first ("disqualification filtering"); outcomes feed back (V: Indeed's "how Indeed job matching
  works"). What the candidate applies to on Indeed trains later recommendations.
- Employers to the candidate: resume search on words, title, company, years, school, degree, location, Boolean,
  "last updated" sort (3P; Indeed's /candidates page agrees on title, skills, company, location). Only findable
  profiles enter that pool ("Employers can find you").
- Application-time Strong/Likely/No Match labels for employers: method unpublished (UNVERIFIED).
- High ghost-posting share: the ghost and liveness rules matter here (V: platforms.yaml notes).

## 2. Alerts and filters to have
- Daily, full-time alerts for the owner's target titles and location scope (read back at
  subscriptions.indeed.com). Alerts are the owner's Activate click (terms); put new ones on the owner's setup list, not
  the lane's.
- Preferences (job titles, minimum pay, relocation, start date) come from the policy config and `profile/`.
- Alert mail parsing: cts.indeed.com redirects are decoded in place, never fetched (tests/test_job_alerts_indeed_cts.py);
  engage.indeed.com tracker links (payload encrypted) are read from the card text and keyed on an Indeed search for
  the card's title, company and place (tests/test_job_alerts_opaque_links.py); set-up confirmations ("Your job alert
  ... is now active") are notices. The daily alert's real layout is UNVERIFIED until the first one: run
  `python3 scripts/job_alerts.py --file state/harvest/<the day>.classified.jsonl --reparse --dry-run` and check the
  indeed line reads N cards, 0 unparsed.
- The Indeed connector (search_jobs, get_job_details) is the sanctioned agent search, inside the owner's sessions only,
  never to apply (V: platforms.yaml indeed-connector).

## 3. The two kinds of Indeed job
- **"Apply now" (Indeed Apply):** the apply happens on Indeed. Terms A.3.5 bar "automation, scripting, or bots to
  automate the Indeed Apply process" and the registry says Indeed Apply is always the owner's own click (V:
  platforms.yaml). Row goes under "Your clicks" (employer site first).
- **"Apply on company site":** the apply happens on the employer ATS: the agent does it by that ATS playbook (part B
  of linkedin.md applies the same way). UNVERIFIED exact button label.

## 4. Indeed Apply sheet for the owner's click
1. Read the job's screening questions on screen (one page, paced) or from the connector's job details (UNVERIFIED that
   the connector returns them).
2. Steps (UNVERIFIED order per job): contact info, resume (the Indeed profile holds the base resume), employer
   questions (years, authorization, sponsorship, salary, relocation, start), review, submit.
3. Answers from README sources; employer questions with knockout shape ("Do you have 5+ years ...") answered truthfully
   and the row flagged.
4. Card row under "Your clicks" with the link and the sheet; log from the owner's word and the confirmation.

## 5. Login walls
- Indeed asks for a one-time code on sign-in (UNVERIFIED frequency): the owner's. Employer sites: README stop rules.

## 6. Verify and log
- Indeed Apply: "Your application has been submitted" page and mail from indeedapply@indeed.com (UNVERIFIED exact text
  and sender); applied jobs list at myjobs.indeed.com/applied.
- Employer messages arrive through an indeedemail.com alias.
- Log: README. Indeed Apply `--via other` "portal: indeed apply"; company site `--via ats` "portal: indeed".

## Observed log (README step 11b)
- Browser search is not a usable scouting route (sponsored filler, loose matching, no posting age on the cards). Read
  Indeed through the parsed alert mail and the Indeed connector, and use the browser only to open one job's employer
  link. Cards can be read via JS `a[data-jk]`.
