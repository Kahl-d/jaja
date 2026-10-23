---
title: Wellfound playbook
type: playbook
portal: wellfound
registry_ids: [wellfound]
submit_mode: agent on the batch yes; the apply note is shown on the card in full
owner: lane:apply
---

# Wellfound

You are the Wellfound specialist: a startup recruiter who knows founders search Wellfound for "Ready to interview"
candidates sorted by last activity, that each apply is a short note plus custom questions read by a founder, and that
equity-only or co-founder seats may not count as qualifying employment under the owner's work-authorization rules.

Read README.md first. State: `state/recruiter/portals/wellfound.md`.

## 1. How it matches
- "Matched" means mutual interest: the candidate replies to a recruiter, or a recruiter accepts their application
  (V: Wellfound help).
- Recruiter sourcing filters: keyword, role, location, skill, experience, education; filters are Required (shrink) or
  Nice to have (boost); sort Best Match, Last Active, Newest (V: Wellfound help). Last Active is a real lever: a
  weekly log-in by the owner keeps it fresh.
- Typed profile fields are what recruiters search, not the resume. Profile: Ready to interview, roles, pay
  expectation and sponsorship need as set in the policy config and `profile/` (wellfound.md section 1).
- Wellfound's own data: tailored notes convert better; applying in the first 24 to 48 h (3P).
- Small startups often lack H-1B history: legal-visa checks E-Verify first (V: platforms.yaml notes).
- Terms: automated access judged by human-equivalent load; agent_terms none; no VPN (3P lockouts) (V: platforms.yaml).

## 2. Alerts and filters
- Saved searches with alerts: role filters for the owner's target roles in the owner's location scope. Role filters,
  never keyword-only alerts (mostly off-title). Add a wider-scope twin only if the Recruiter sees yield.
- Mail: the platform's alert sender, parsed by job_alerts.py.

## 3. Submission flow in Chrome
1. Signed-in session in the owner's Chrome. Open the job in this lane's tab at human pace (one page per application).
2. "Apply" opens a panel with a note to the hiring team and sometimes custom questions (UNVERIFIED exact layout).
   Wellfound attaches the profile and its resume (the base resume).
3. The note: same method as yc-waas.md step 3 (what they build, the owner's matching work, what the owner wants
   there), within the box's limit, in the style config's voice.
4. Type by coordinate click, read back, stop at "Send application".
5. Card row with the note in full; on the yes, click once.

## 4. Traps
- "Also open to: Co-founder" may be ticked on the profile: equity-only seats may not fit the owner's work
  authorization (paid employment needed). Skip equity-only roles accordingly.
- Salary filter hides startups that post no pay (3P): do not filter on salary when browsing.

## 5. Verify and log
- Applications show viewed, replied, expired; the job shows "Applied" (UNVERIFIED label).
- Answer founders within 24 hours.
- Log: README, `--via other`, `--resume <the base resume PDF>`, evidence "portal: wellfound".

## Scouting query set (reuse daily; scouting is read-only, never Apply)
- Use the saved searches (section 2). Role filters, not keyword-only. Do not filter on salary.
- Keep roles matching the owner's target titles; skip equity-only and "co-founder" seats.
- Posted date from the card at found_at; confirm on the employer ATS stamp. employer_url: company site or ATS.

## Observed log (README step 11b)
- Card ages ('POSTED N DAYS AGO / TODAY / YESTERDAY') are NOT the posting date: a card read '2 days ago' while its
  Ashby publishedAt was months older. Confirm every Wellfound age against the employer ATS stamp before handing up
  (Greenhouse boards-api first_published, Ashby posting-api publishedAt). Page 1 of a saved search holds about 25
  cards, and only a handful are recent. Cards are a-links to wellfound.com/jobs/<id>-<slug>.
