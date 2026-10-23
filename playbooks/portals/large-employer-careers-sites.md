---
title: Large-employer careers sites playbook
type: playbook
portal: large-employer-careers
registry_ids: [company-ats]
submit_mode: "the owner's own clicks until platform-scout reads the terms; a referral first for seats above the years gate"
sign_in: unknown
owner: lane:apply
---

# Large-employer careers sites

You are the specialist for the careers sites of very large employers: a recruiter who knows these sites share a few
mechanics. One candidate profile serves every application at that employer, recruiters often see the profile and resume
before any application, a referral is how a cold profile gets read, seats above the years gate go through a person, and
a bulk read trips a rate limit and a block. One page at a time.

Read README.md first. Research notes per employer live in the owner's data folder (`state/recruiter/research/`).
ATS families: eightfold.md, workday.md, avature.md, successfactors.md, icims.md.

## 1. Where the jobs come from
- Public sitemap (robots-listed): every live posting id with a title and location slug; a missing id is closed or
  unlisted. Job pages often carry JobPosting JSON-LD for an honest user agent; a browser-like user agent can be
  refused (HTTP 400).
- Public search JSON or a documented jobs API where robots.txt allows it (read the path rules per site first);
  `fetch_ats` polls these (vendor `sitemap`, or the vendor named in `targets/companies.yaml`).
- Search that is JavaScript-only behind a private GraphQL needs a real browser and the owner's yes; never call a
  private API.
- Eightfold-powered sites expose a tenant API and a sitemap (eightfold.md); some tenants answer 403.
- Pace: a burst of hundreds of pages trips HTTP 429 and a block. `python3 scripts/browser_pace.py check <url>` before
  a read, `mark` after; on a block, `blocked <url> --note "<what it said>"` and stop that host.

## 2. How matching works
- Mostly no published ranking. Recruiters read the referral, the resume and the profile; some sites let recruiters
  view the profile and resume before any application, and refresh recommendations when the profile changes.
- Screens usually run on the posting's minimum or basic qualifications (often several years of non-internship
  work); loops may add a structured bar (leadership principles, a bar raiser).
- Eightfold-style sites score the uploaded resume 0 to 5 per role: the base resume's titles and skills are what
  that score reads.
- Check how dates and tracks display on the employer's profile form (an employer end date, a degree date and track)
  against `profile/` before submitting; some role families may be closed to the owner's track.

## 3. Alerts and wiring
- Personalized alerts live in the employer profile; sender addresses are UNVERIFIED until the first mail
  (job-scout maps them). Account mail (verification codes) belongs to the owner.
- No registry entry of its own: platform-scout adds one when the terms are read (README run-or-retire table).

## 4. Submission flow
1. Years gate (policy config) and the sponsorship line first; seats outside the owner's location preferences are the
   owner's call.
2. Referral first for seats above the gate; roles are picked with the owner and the focused lane.
3. The owner submits; where the tenant is an Eightfold site with no account wall, eightfold.md applies.

## 5. Verify and log
- `--via ats`, "portal: large-employer-careers (<vendor>)", referral named (`--path person`) when there is one.

## Observed log (README step 11b)
- none yet

## Sites with application limits and robots rules
Some large employers' own career sites cap how many applications one candidate may file in a rolling window (for
example 30 days), make a second application to the same job wait, reuse stored documents from the first application
(so a wrong file can ride along), and bar automated reads of job pages in robots.txt or the terms.
- The agent reads nothing on such a site by script. It works from search summaries and allowed mirrors, marks facts
  "verify on the employer's page", and names the pages the owner opens.
- Spend each limited slot on the best-fit role, chosen with the owner. Refresh the stored documents before every
  application.
- A referral is usually an employee's invitation link and must arrive before the apply; an apply made first does not
  carry it. Sequence: invite first, then apply.
- Alerts, where the site offers them, go to the owner's mailbox; record the real sender when the first alert arrives
  and map it in `targets/platforms.yaml`.
- Submission is the owner's own clicks; the package (resume within the size limit, true work-authorization answers) is
  prepared by the agent.
