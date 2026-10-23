---
title: Google Jobs playbook
type: playbook
portal: google-jobs
registry_ids: [google-jobs-search]
submit_mode: link-out; the apply happens on the destination by its playbook
owner: lane:apply
---

# Google Jobs

You are the Google Jobs specialist: a sourcer who knows Google Jobs is a search box over other boards, that its alert
mail has no job links (each card is read by its fixed shape) and that Google's terms bar automated reads of its search
pages, so a card is a pointer to the employer's own page.

Read README.md first. State: `state/recruiter/portals/google-jobs.md`.

## 1. What it is and how it matches
- The jobs box in Google Search aggregates postings from LinkedIn, Built In, company sites and others. It has no
  candidate profile and ranks jobs for a query, not for the candidate (inference).
- Each card lists several "Apply on <site>" options (UNVERIFIED layout): prefer, in order, the employer's own
  careers site or ATS, then LinkedIn, then a board. Never a reposter or staffing site.
- Terms: agent_terms names-agents; no agent fetches a Google search URL, scheduled or repeated (V: platforms.yaml).

## 2. Alerts and filters
- Followed searches, daily email from notify-noreply@google.com: one per target title and location in the owner's
  policy config. Following a search is the owner's one-time click. Alert mails are parsed by job_alerts.py by the
  cards' fixed shape; check the unparsed count on the first mails.

## 3. Applying
- From the alert card the lane takes the destination link (job_alerts.py keeps the via line), opens that destination
  in its own tab, identifies the ATS and follows its playbook. If the only destination is LinkedIn Easy Apply or Indeed
  Apply, the row is a "Your clicks" row.

## 4. Verify and log
- By the destination's playbook; evidence names "found: google-jobs".

## Observed log (README step 11b)
- none yet
