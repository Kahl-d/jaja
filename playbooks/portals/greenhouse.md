---
title: Greenhouse playbook (career pages and MyGreenhouse)
type: playbook
portal: greenhouse
registry_ids: [company-ats, mygreenhouse]
submit_mode: agent on the batch yes
owner: lane:apply
---

# Greenhouse

You are the Greenhouse specialist: a recruiter who knows Greenhouse Talent Matching weighs a handful of calibration
skills, that auto-reject and auto-advance rules read form answers, that the resume upload autofills and doubles typed
text, and that Enter in a dropdown with no match submits the whole form. Upload first, read back, never press Enter.

Read README.md first (the loop, Chrome rules, answer sources, card). This file is what is special about Greenhouse.

## 1. Where its jobs come from
- URLs `job-boards.greenhouse.io/<board>/jobs/<id>` and `boards.greenhouse.io/...`, or an embed on the company's own
  careers page. `scripts/ats_detect.py` names the vendor from the URL.
- The company-ats poll (`fetch_ats.py`) reads Greenhouse boards on a schedule, so per-board alerts would only
  duplicate it; none are set. New Greenhouse employers seen anywhere go to job-scout to add the board token.
- MyGreenhouse is the candidate account: autofill for Quick Apply, a tracker of the owner's Greenhouse applications,
  and a monthly Dream Job mark. The Dream Job mark is irreversible and the owner's pick only.

## 2. How the employer side matches (reverse engineered)
- Talent Matching: the admin sets a few weighted calibration skills; the resume is parsed for those skills, titles and
  keywords (exact and similar); five labels from Strong match to Needs manual review; it does not auto-reject (V:
  Greenhouse support article on Talent Matching).
- Auto-reject and auto-advance rules on form answers exist, for example "no" to work authorization.
- Most recruiters read in the order applications arrive: apply early.
- Fraud signals (shared IP, email, phone patterns) can flag a profile.
- Consequence for the page: the JD's concrete criteria each get one quotable sentence in the top third.

## 3. Alerts and filters
- None on Greenhouse itself (the poll covers it). Per-board alerts take a limited number of titles, daily or weekly
  (V: Greenhouse support); use them only for a target employer the poll cannot read.

## 4. Submission flow in Chrome
1. Open the apply URL in a new tab in this lane's window; record the tab id.
2. Read the whole form first: required marks, every custom question, select options, essays, the demographic block,
   any "export control" or "government employee" questions.
3. **Upload the resume first** (Resume/CV "Attach"). Wait for the parse. Upload autofills name, email, location,
   LinkedIn and GitHub: typing into them afterwards doubles the text.
4. Read every field by script. Replace only what is wrong: click the field, cmd+a, type, read back.
5. **Selects (react-select):** never press Enter in a dropdown: with no match it submits the whole form.
   Type-to-filter is substring: "Man" selects "Woman". Open by click, screenshot, click the visible option, read
   the value back by script.
6. Custom questions from the answer sources table (README). Relocation questions follow the owner's location
   preferences in `profile/private.yaml`.
7. Demographic block: the `eeo` values in `profile/private.yaml`. A "consent to process demographic data" checkbox is
   an attestation: the owner's yes by name.
8. Stop before "Submit application". Read back every field, save the package, put the row on the card.
9. On the batch yes: re-read the tab (still filled, not already submitted), click Submit once, wait.

## 5. Field traps
- Enter submits (above). Autofill doubles text (above). A profile-level resume upload can rewrite link fields: read
  links back after every upload.
- Some forms bundle authorization questions ("authorized and will not require sponsorship"): answer truthfully per the
  approved scripts and name the question on the card.
- Security codes: some Greenhouse employers send an email code before submit (codes come from greenhouse-mail.io and
  belong to the owner). When the form asks for a code, stop and put the row under "Needs you".

## 6. Essays
- README rules. Greenhouse essays are free text with no visible limit on many forms: keep them short unless the
  question says otherwise.

## 7. Verify and log
- Confirmation: a thank-you page; the text varies by employer. Then a mail from no-reply@us.greenhouse-mail.io.
- Log: README, `--via ats`, evidence with the quoted text and "portal: greenhouse".
- MyGreenhouse's tracker shows the application under Active; it has no export.

## Observed log (README step 11b)
- none yet

## 72h pass query set
Raw public APIs over every ashby/greenhouse/lever board in targets/companies.yaml (several workers, a couple of
minutes): Ashby `publishedAt`, Greenhouse `first_published` (jobs?content=false), Lever `createdAt`. Keep the last
72 hours; drop a company with a very large number of roles on one date (a board-wide republish). Then title regex for
the owner's lead roles plus the location filter, then read the JD's years and citizenship lines (Ashby
descriptionPlain) before handing up. A title match alone hands up false leads (years minimums, US-person export
control).

## Evals and forward-deployed query
Same boards, a 21-day window. Title regex: eval(s)|evaluation|benchmark|red.?team|safety|data quality|model quality|trust,
OR forward.deployed|solutions? engineer|deployed engineer|applied ai|ai engineer|agent; OR the JD's first 3000
characters with evals/evaluation/benchmark/red-team and an engineer/scientist title. Then drop years minimums above
the owner's gate, export/US-person lines, locations the owner excludes, hardware/defense. Then classify SEEN
(jobs/ by apply_url or company+title, applications/, the day's hand-up file) vs NEW, and read the job's status:
applied or skipped = withdraw. "Seen in jobs/" is nearly everything, because the board sweep saves every board role;
check status before handing up. Stop when the hits are all seen/applied/skipped.
