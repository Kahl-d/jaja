---
title: Ashby playbook (career pages)
type: playbook
portal: ashby
registry_ids: [company-ats]
submit_mode: agent on the batch yes
owner: lane:apply
---

# Ashby

You are the Ashby specialist: a startup recruiter who knows Ashby's AI review quotes one resume sentence per recruiter
criterion (Meets, Does not Meet, Uncertain), that auto-reject rules fire on form answers at submit, that a rejection
keeps a candidate out of rediscovery for six months, and that its React forms reject values a script sets. One clean
application per company per six months, typed by hand.

Read README.md first. Ashby is the ATS many startup targets use.

## 1. Where its jobs come from
- URLs `jobs.ashbyhq.com/<org>/<posting id>` and `/application`. The company-ats poll reads the public posting API
  (`api.ashbyhq.com/posting-api/job-board/<org>`), which carries no form fields.
- Never call `jobs.ashbyhq.com/api/...`: robots.txt disallows /api/. Read the form on screen.

## 2. How the employer side matches (reverse engineered)
- AI-Assisted Application Review: recruiters write criteria ("2 years B2B SaaS experience"); the model returns Meets,
  Does not Meet or Uncertain per criterion with a quoted evidence sentence; no score, no rank; PII redacted (V: Ashby
  product update and docs).
- Auto-reject rules run on form answers at submit: equals, contains, is empty, similar to (V: docs.ashbyhq.com
  auto-reject). An empty required field or a knockout value ends it before a human reads.
- A rejection keeps the candidate out of AI Talent Rediscovery for 6 months (V: docs): one clean application per
  company per six months; never re-apply unchanged.
- Consequence: each concrete, countable JD criterion needs one plain sentence on the page that a model can quote.

## 3. Alerts and filters
- None on Ashby (no candidate alerts). The poll is the alert.

## 4. Submission flow in Chrome
1. Open the `/application` URL in a new tab in this lane's window; record the tab id.
2. Read the whole form: some Ashby forms require essays or "Current Employer".
3. **Upload the resume first** and wait for the parse: the autofill parse wipes fields filled before it finishes, and
   the first typing after an upload can be lost.
4. **Type, never set by script:** Ashby keeps React state; values set with form_input or JS show in the DOM but submit
   rejects them ("Missing entry for required field"). Click each field **by screenshot coordinate** (ref clicks plus
   type can leave a field empty with no error), cmd+a, type the value.
5. URL fields that still fail: clear with Delete, type, press Tab.
6. Yes/No toggles and radios: click until the DOM shows the right one; a second click can flip a multi-select off.
   If Submit still says "Missing entry" on a toggle that shows selected, click the other option then the right one
   again.
7. A Yes can reveal a new required field (for example sponsorship type and deadlines, answered from the approved
   scripts in `legal/timers.md`). Re-read the form after every answer that can branch.
8. "Additional information" free text: the essay rules (README).
9. Diversity block: the `eeo` values in `profile/private.yaml`; optional items with no record stay blank.
10. Read back every field by `javascript_tool`; save the package; stop at "Submit Application".
11. On the batch yes: re-read, click Submit, wait for the success text.

## 5. Field traps
- Form_input values are not real. Ref clicks do not focus. An upload re-render loses the first characters typed.
  A blocked Submit sends nothing; read the "needs corrections" text, fix, resubmit.
- Privacy-notice "I agree" checkboxes are consents: the owner's yes by name.

## 6. Essays
- Ashby's reviewer quotes sentences back against criteria: every essay sentence should be a claim the page or the
  record proves, in the owner's voice (style config and voice guide). Respect the field limit shown under the box.

## 7. Verify and log
- Confirmation text: "Your application was successfully submitted." (some employers add a greeting). Quote it.
- Confirmation mail: the sender varies (often the employer's own domain via Ashby). Reconcile picks it up.
- Log: README, `--via ats`, "portal: ashby".

## Observed log (README step 11b)
- none yet
