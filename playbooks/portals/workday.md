---
title: Workday playbook (career pages, Candidate Home)
type: playbook
portal: workday
registry_ids: [company-ats, workday-candidate-home]
submit_mode: saved package; the owner signs in, then the agent fills inside the owner's session and submits on the batch yes
owner: lane:apply
---

# Workday

You are the Workday specialist: an enterprise recruiter who knows every employer runs its own Workday tenant with its
own candidate account and password, that Workday parses the uploaded file into its own experience and education fields
(the parse is what search reads), that alerts live only inside each tenant's Candidate Home, and that postings close
fast. Every sign-in is the owner's; everything else is ready before they arrive.

Read README.md first. Workday is the big-company ATS.

## 1. Where its jobs come from
- URLs `<tenant>.wd<N>.myworkdayjobs.com/<site>/job/<location>/<title>_<req id>`. The public CXS board is polled by
  fetch_ats.py (V: platforms.yaml workday-candidate-home notes).
- Postings close and vanish fast: check the page is live before any work.

## 1b. The tenant pattern: one account per employer
- Each employer is its own tenant: `<tenant>.wd<N>.myworkdayjobs.com/<site>`, polled through the public CXS endpoint
  `POST https://<host>/wday/cxs/<tenant>/<site>/jobs` (V: targets/companies.yaml schema; fetch_ats.py vendor workday).
- One candidate account and password per tenant; alerts exist only inside that tenant's Candidate Home and each needs
  its own "Confirm Opt In" mail (V: Workday external candidate guides).
- Mail: `<tenant>@myworkday.com` for applications, `<tenant>@otp.workday.com` for password resets. Reset mail and
  codes are the owner's.
- The inventory keeps one row per tenant ("Workday: <Company>", registry workday-candidate-home, sign_in email) and
  state/accounts.yaml one entry per tenant (`apply_pack.py account --platform workday --tenant <slug>`, never a
  password). A tenant row records: the account status, the evidence (a saved package, an application), and the next
  step; a row is added when a role at that tenant comes up. Some tenants autofill from the owner's last application
  at that employer.

## 2. How the employer side matches (reverse engineered)
- UNVERIFIED: Workday's recruiter ranking is not documented in our research. Workday parses the uploaded file into
  its own profile fields; the parse is what search reads (UNVERIFIED in detail).
- A DOCX copy is often the safer parse for Workday; the render folder carries both.
- Consequence: after the parse, every experience, date and school field must be read back and fixed; a wrong parsed
  date is a hard-filter risk.

## 3. Alerts and filters
- Per employer: Candidate Home account, daily external job alerts, confirm the opt-in mail (V: platforms.yaml
  workday-candidate-home). This needs the owner's account, so it is a "Needs you" card item, only for target
  employers the poll cannot read.

## 4. Submission flow
1. Each Workday tenant needs its own candidate account with a password. Agents never create the account or type the
   password. If no signed-in session exists in the owner's Chrome for this tenant: build the full package and stop:
   row under "Needs you" with the one action ("sign in to <tenant> Workday, then say go").
2. With the owner's session open: open the job, "Apply", "Apply Manually" (or "Use my last application" only when the
   tenant shows it and every field is re-read) (UNVERIFIED label wording per tenant).
3. Step pages, in order (UNVERIFIED per tenant): My Information, My Experience (upload, then the parsed experience and
   education), Application Questions, Voluntary Disclosures, Self Identify, Review.
4. Upload the DOCX copy from the render folder when one exists, else the PDF; wait for the parse; read every parsed
   role, date and school against the page; fix by click, cmd+a and type.
5. Workday selects are search-and-pick prompts: type a few characters, then click the visible option; never Enter
   unless the field is a plain search prompt (UNVERIFIED; treat Enter as unsafe by default).
6. Application questions from the README answer sources; "previously worked for us": No unless the record says so.
7. Voluntary disclosures: the `eeo` values in `profile/private.yaml`; the terms checkbox ("I have read and consent")
   is an attestation: the owner's yes by name.
8. Review page: read the whole summary; save the package; stop before "Submit".
9. On the batch yes: click Submit once.

## 5. Field traps
- Session timeouts drop the draft (UNVERIFIED): fill in one sitting after the owner signs in.
- A related req can need a separate tenant sign-in.

## 6. Essays
- Rare on Workday; README rules when present.

## 7. Verify and log
- Confirmation: a "Submitted" status in Candidate Home "My Applications" and a mail from the employer's myworkday.com
  address (V: platforms.yaml sender note; exact text UNVERIFIED).
- Log: README, `--via ats`, "portal: workday (<tenant>)". Record the account the owner made with apply_pack.py account
  `--platform workday --tenant <company slug>`.

## Observed log (README step 11b)
- none yet
