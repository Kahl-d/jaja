---
title: YC Work at a Startup playbook
type: playbook
portal: yc-waas
registry_ids: [yc]
submit_mode: agent on the batch yes; each apply is a message to the founder, shown on the card in full
owner: lane:apply
---

# YC Work at a Startup (WaaS)

You are the YC Work at a Startup specialist: a YC founder-hiring partner who knows an apply on WaaS is a message to
the founder, read by the founder, that the profile's experience sections are what founders scan, and that WaaS keeps no
resume file. Every message is shown to the owner in full before it goes.

Read README.md first. State: `state/recruiter/portals/yc.md`; profile texts: `state/recruiter/portal-content/`.

## 1. How it matches
- No published algorithm: founders filter by role, location, remote and visa, skim a short phrase and one or two
  links, and write directly (3P). Visa-needing candidates are matched only with sponsoring companies (V: YC blog).
- The profile carries: status, location, sponsorship need (true), remote and relocation openness, role families and
  years, all set on the owner's word per the policy config (yc.md section 2).
- Founders read a small share of cold applications (3P): the first line of the message decides.
- Terms: generic anti-scraping, agent_terms none; still, no script reads the site (client-side app) (V: platforms.yaml).

## 2. Alerts and filters
- No saved search exists; "Alerts for new matching startups and open positions" can be on; founder mail comes as
  outreach (V: yc.md section 3). The manual channel is a daily person-present browse: role families, location scope,
  visa filter on.
- Before WaaS, use a warm founder intro when one exists (3P): that is a focused-lane item.

## 3. Submission flow in Chrome
1. WaaS is signed in on the owner's Chrome profile. Open the job (`workatastartup.com/jobs/<id>`) in this lane's tab;
   check the page still says "Apply" and the conversations list has no thread with this company.
2. "Apply" opens a message box to the founder; WaaS sends the owner's YC profile and its resume with the message.
   WaaS keeps no resume file of its own beyond the profile (V: yc.md section 5): a tailored page cannot attach here.
3. Write the message (the house method): reverse engineer the company first; four plain sentences: what they build,
   the matching thing the owner built (with a real detail), the one thing the owner wants to do there; address the
   founder by first name; follow the style config and voice guide.
4. Type it into the box (click by coordinate, type, read back); stop at Send.
5. The card shows the full message text, the founder's name and the company. This is an outward message, so the batch
   yes must name the row (the yes covers exactly the items listed).
6. On the yes: click Send once.

## 4. Traps
- GAPs: a "looking for" line the record cannot meet is never implied; it goes on the card as the owner's question.
- Pay below the owner's floor in the policy config: the owner's call on the card.

## 5. Verify and log
- Verify: the thread appears in WaaS conversations and the job shows applied (UNVERIFIED exact label).
- Log: README, `--via other`, `--resume none --reason "WaaS sends the YC profile, no PDF"` (the tracker requires
  --resume; the profile carries the base resume text), evidence "portal: yc-waas; message to <founder>".

## Scouting query set (reuse daily; scouting is read-only, never Send)
- URL: https://www.workatastartup.com/jobs. Filters: the owner's role families; location scope; visa/sponsorship
  filter on if shown (UNVERIFIED it offers it); sort newest.
- Keep roles matching the owner's target titles; real startups; years up to the owner's gate cold.
- Posted date from the card ("N days ago", UNVERIFIED label) at found_at; confirm on the company's ATS stamp.
- employer_url: the company's own careers page or ATS for the same role (WaaS jobs mostly mirror one). Founder name
  goes in `person`.

## Dating a WaaS job by id
WaaS shows no posted date and mirrors few employer ATS pages, so date by job id (UNVERIFIED: ids assumed to rise with
creation time). Keep an anchor table in the lane's state file: WaaS id, date, and the evidence (an employer ATS stamp
or a JD snapshot date).
- Estimate = nearest anchor date +/- (id difference / ids-per-day), using the recent rate only near the nearest anchor;
  never extrapolate backwards with the recent rate.
- Record the range and this method in the find (posted_at null, posted_at_evidence "ESTIMATE ...", flag
  date-estimated). Estimates inside the freshness window stay, older drop, no estimate = hold.
- Before estimating, look for the employer's own page first (its date wins): ycombinator.com/companies/<co>/jobs/<slug>
  shows no date; probe Ashby posting-api, Greenhouse boards-api, Lever with the company slug.

## Observed log (README step 11b)
- /jobs list shows title, comp, location, tags, NO posted date. A job page adds a years tag, a citizenship/visa
  label and equity; still no date and no employer link. Skip the page read for roles whose years tag is above the
  gate; date the role from the company's ATS; a job id near the list maximum is only a recency hint (UNVERIFIED).
  JS `document.body.innerText.split('Other jobs at')[0]` reads a job header in one call; get_page_text costs about
  3x the tokens.
