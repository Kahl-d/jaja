---
title: SmartRecruiters playbook (career pages)
type: playbook
portal: smartrecruiters
registry_ids: [company-ats, ats-public-feeds]
submit_mode: saved package; the owner attaches the file, the agent fills the rest and submits on the batch yes
owner: lane:apply
---

# SmartRecruiters

You are the SmartRecruiters specialist: a recruiter who knows SmartRecruiters publishes a public Posting API, that its
upload control sits in a shadow DOM the agent tools cannot reach, and that "Apply with LinkedIn" is the owner's sign-in.

Read README.md first.

## 1. Where its jobs come from
- URLs `jobs.smartrecruiters.com/<Company>/<posting id>-<title>`. The public Posting API gives full JDs
  (ats-public-feeds adapter order in platforms.yaml). Score freshness from the first posting date, not the update date.

## 2. How the employer side matches
- UNVERIFIED: no SmartRecruiters ranking source in our research. Treat it like the others: hard fields first, then a
  parsed-resume keyword match.

## 3. Alerts and filters
- None (the feed poll is the alert).

## 4. Submission flow
1. Open the posting in a new tab in this lane's window; "I'm interested" or "Apply" opens the "Easy Apply" form.
2. The form inputs are built in shadow DOM: the browser tools cannot attach the file. The alternatives are the native
   file picker or "Apply with LinkedIn" (the owner's sign-in).
3. So: fill the text fields the tools can reach (type by coordinate click; read back by script into the shadow root;
   UNVERIFIED that typing lands in every field), build the answer table in the package, and put the row on the card
   under "Needs you" with the action "attach <file> in the open tab" (a few minutes by hand).
4. After the owner attaches: re-read every field (the parse may autofill), then the row is an agent row for the next yes.
5. Stop before "Submit"; on the batch yes click once.

## 5. Field traps
- Shadow DOM upload (above). "Apply with LinkedIn" is a sign-in: the owner's.

## 6. Essays
- README rules.

## 7. Verify and log
- Confirmation page and mail text: UNVERIFIED until a submit is on record; quote what appears.
- Log: README, `--via ats`, "portal: smartrecruiters".

## Observed log (README step 11b)
- none yet
