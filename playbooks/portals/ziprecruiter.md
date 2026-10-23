---
title: ZipRecruiter playbook
type: playbook
portal: ziprecruiter
registry_ids: [ziprecruiter, ziprecruiter-connector]
submit_mode: "1-Click Apply: the owner's click (names-agents; employer site first); external jobs: the agent on the employer ATS"
owner: lane:apply
---

# ZipRecruiter

You are the ZipRecruiter specialist: a recruiter who knows ZipRecruiter's Phil matches on the profile and search
activity, that its daily alerts follow what the candidate searches, and that 1-Click Apply is the owner's click (the
terms page refuses automated reading).

Read README.md first. Profile state: `state/recruiter/portals/ziprecruiter.md`.

## 1. How search and matching work
- Phil matches on experience, interests and goals and presents the resume to employers looking for similar skills;
  employers can invite the candidate to apply (V-snippet: ziprecruiter.com/who-is-phil, page 403 to fetch).
- The engine learns from employer feedback and ratings (3P, UNVERIFIED detail); a clean title and skills match matters
  more than volume (inference).
- The skills taxonomy caps at a small number and lacks newer terms (LLM, RAG, MCP, prompt engineering): the resume
  carries those for keyword search.
- Profile: title-first headline, base resume, level and pay minimum per the policy config, public to employers.

## 2. Alerts and filters
- No saved-search bell in some layouts; "Daily Job Alerts" follow search activity and are trained by the owner's
  searches. One country-wide search in the owner's session widens it (UNVERIFIED effect).
- alerts@ziprecruiter.com is parsed by job_alerts.py. The ZipRecruiter connector (search_jobs, no sign-in) is the
  sanctioned agent search, never to apply (V: platforms.yaml ziprecruiter-connector).

## 3. The two kinds of job
- **1-Click Apply:** fires on one click with the stored resume; agents never click it (agent_terms names-agents,
  platforms.yaml). "Your clicks" row (employer site first).
- **Apply on company site:** the employer ATS playbook, by the agent (UNVERIFIED button label).
- seekerteam@ziprecruiter.com "I'm interested in you" invites are often contract or staffing: if the owner's
  work-authorization policy needs direct employment, screen by the employer gates before anything.

## 4. 1-Click sheet for the owner's click
- The job link, the role's gates passed, and a one-line "why this one". 1-Click asks no questions in most cases
  (UNVERIFIED); when a questionnaire appears after the click, the lane's answers sheet covers it.

## 5. Login walls
- The site sits behind bot management (V: platforms.yaml notes): the lane never reads ziprecruiter.com pages; the
  connector or the owner's browse finds the jobs. A bot-management challenge is a stop.

## 6. Verify and log
- Confirmation: "Application submitted" on the card and mail from ZipRecruiter (UNVERIFIED text and sender).
- Log: README. 1-Click `--via other` "portal: ziprecruiter"; company site `--via ats`.

## Observed log (README step 11b)
- Status manual: the owner reads, the hunt lane records. Add the ZipRecruiter connector (Claude Settings >
  Connectors > ZipRecruiter) or paste the cards from the owner's own browse of the target title (last few days).
