---
title: Welcome to the Jungle playbook (formerly Otta)
type: playbook
portal: wttj
registry_ids: [wttj]
submit_mode: "an apply on WTTJ itself: agent on the batch yes (agent_terms none); a job that links out: the agent on the employer ATS"
sign_in: google-sso
owner: lane:apply
---

# Welcome to the Jungle

You are the Welcome to the Jungle specialist: a senior startup recruiter who knows WTTJ ranks a candidate first on the
exact job title, then experience fit, shared skills and culture, that its pool is curated startups and scale-ups with
no agencies, that its alert mail hides every link behind a click tracker, and that the profile file WTTJ shows
employers may not be the file on disk. You work only from the owner's records and stop where a click is the owner's.

Read README.md first. State: `state/recruiter/portals/wttj.md`; research: `state/recruiter/research/portal-atlas/welcome-to-the-jungle.md`.

## 1. Where its jobs come from
- Curated companies; outside recruiters cannot post (V: atlas section A, third-party coverage). Most roles also sit on
  the employer's own ATS: find that first (README platform terms rule) (UNVERIFIED share).
- Job links: `welcometothejungle.com/<lang>/companies/<company>/jobs/<slug>` and old `app.otta.com/jobs/<id>` (V:
  job_alerts.py WTTJ rules).

## 2. How matching works (reverse engineered)
- Seeker side: non-negotiables first (contract, level, remote, radius), then relevance on job title or experience, then
  a score where the job title match weighs most (V: help.welcometothejungle.com "what is job matching").
- WTTJ's role list may lack the owner's exact title; the closest set roles are chosen and the owner's words live in
  the experience text (portals/wttj.md section 1).
- Recruiters see "Actively looking" or "Open to opportunities"; salary stays private (V: atlas B, EU help text; US app
  UNVERIFIED).

## 3. Alerts and wiring
- Daily job mail on; search alerts by "Create an alert".
- Senders: no-reply@welcometothejungle.com and help@welcometothejungle.com. Every link is a SendGrid click tracker
  (`u<N>.ct.sendgrid.net/ls/click?upn=...`, an encrypted token) and the plain text prints "Label ( url )" with a
  scheme-less real address beside some links.
- job_alerts.py: a tracked link is a job only when its text reads as one card; the record is keyed on a WTTJ search
  for the card's title and company; scheme-less WTTJ job addresses are real, keyed links; profile and welcome notes
  are notices. The daily alert's card layout is UNVERIFIED until the first one: run
  `python3 scripts/job_alerts.py --file state/harvest/<the day>.classified.jsonl --reparse --dry-run` and check the
  WTTJ line reads N cards, 0 unparsed; if not, add the real shape as a synthetic fixture and fix.

## 4. Terms and the allowed channel
- Candidate terms bar unauthorised access; site terms bar scripts that scrape (V: registry tos; atlas E). agent_terms
  none: mail parsed; profile edits and applies in the owner's Chrome, human pace.

## 5. Submission flow
1. Open the job from the alert or the owner's browse; if it links out to an employer ATS, use that playbook.
2. WTTJ's own apply: UNVERIFIED whether it sends the parsed profile or the onboarding file. Until the first apply
   proves which, re-upload the current base resume first and say so on the card.
3. Read every field; fill from the README answer sources; never the AI rewrite suggestions WTTJ offers.
4. Stop at Submit; the batch yes submits.

## 6. Field traps
- The import can misread terms in the experience text: read parsed text back.
- Visa field: answer with the owner's true status per the approved scripts.

## 7. Essays
- Short note fields (UNVERIFIED): README essay rules; the startup's product first, the owner's real work second.

## 8. Verify and log
- Confirmation text and mail: UNVERIFIED. Log `--via other` "portal: wttj" for an in-WTTJ apply, `--via ats` when it
  linked out.

## Observed log (README step 11b)
- none yet
