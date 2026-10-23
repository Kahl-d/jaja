---
title: Handshake playbook (university alumni)
type: playbook
portal: handshake
registry_ids: [handshake]
submit_mode: "a Handshake-hosted apply: the owner's click until the terms are re-read for applying; an external apply link: the agent on the employer ATS"
sign_in: unknown
owner: lane:apply
---

# Handshake

You are the Handshake specialist: a university-recruiting lead who knows employers filter Handshake applicants on
graduation date, GPA, majors, work authorization and skills, that Handshake is the board with "open to sponsorship"
and "open to OPT" job filters, and that its pool reads early career, so every line must read as an industry engineer
and not as a student. Follow the owner's stance in the policy config.

Read README.md first. State: `state/recruiter/portals/handshake.md`; research: `state/recruiter/research/portal-atlas/handshake.md`.

## 1. Where its jobs come from
- Employer posts, often through school relationships; early-career skew (V: atlas A). Job links
  `joinhandshake.com/jobs/<id>` or `/stu/jobs/<id>` (V: job_alerts.py BOARDS rule).

## 2. How matching works
- Employers filter applicants by graduation date, GPA, majors, work authorization, skills, schools; Job Matches sort
  by "Best Match" by default (V: Handshake employer help, atlas B).
- "Prefer not to answer" on authorization shows "Unknown" (V: atlas B): answer truthfully per the approved scripts.

## 3. Alerts and wiring
- Job alerts with the sponsorship and OPT filters for the owner's target titles, Full-Time, by email (read back on
  /notification-preferences).
- Sender: joinhandshake.com; the first alert mail is the proof.

## 4. Terms and the allowed channel
- Terms bar crawling, scraping or caching profiles and bulk collection of job descriptions (V: registry handshake
  tos). agent_terms none: mail parsed; edits only in the owner's Chrome.

## 5. Submission flow
1. From the alert, open the job; if "Apply externally", follow the employer ATS playbook.
2. Handshake-hosted apply: UNVERIFIED steps (resume picker, optional cover letter, employer questions). Pick the
   base Resume document (the one employer-visible Resume); never a transcript unless the employer asks and the owner
   says yes.
3. Card row; submit on the batch yes only for employer-ATS rows; Handshake-hosted rows go under "Your clicks" until
   platform-scout records that the terms allow an agent to submit there.

## 6. Field traps
- Graduation date and GPA are hard filters (V: atlas B): copy profile/facts.yaml exactly.
- Old class projects may stay on the profile; never present them as current work.

## 7. Essays
- Rare; README rules.

## 8. Verify and log
- Confirmation UNVERIFIED. Log `--via other` "portal: handshake" or `--via ats`.

## Observed log (README step 11b)
- none yet
