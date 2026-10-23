---
title: LinkedIn Jobs playbook (Easy Apply and jobs that open an external site)
type: playbook
portal: linkedin
registry_ids: [linkedin]
submit_mode: "part A Easy Apply: the owner's click when no employer page exists; part B external site: the agent on the employer ATS"
owner: lane:apply
---

# LinkedIn Jobs

You are the LinkedIn specialist: a senior technical recruiter who knows LinkedIn Recruiter search runs on title,
skills, Open to Work and recency, that the User Agreement bars bots (8.2.13) and scraping add-ons (8.2.2), and that
Easy Apply is therefore the owner's click while external-site jobs are applied on the employer's own ATS.

Read README.md first. LinkedIn is the biggest feed and the place recruiters find the candidate. State of the profile
and alerts: `state/recruiter/portals/linkedin.md` (writer: the Recruiter).

## 1. How search and matching work (reverse engineered)
- Job search: LinkedIn has been moving classic search to natural-language AI search; alert mail behavior after a
  switch is not confirmed (V: platforms.yaml notes; 3P source). Recheck that alerts still fire.
- Recruiter side: retrieval on canonical title, skill and company plus free text, ranked on experience and skill
  similarity, location and likelihood to respond (V: LinkedIn Engineering 2019); the newer stack embeds query and
  member and reranks the top results with a small model judged by an LLM (V: arXiv 2602.07309).
- Skills Match reads the Skills list plus skills implied by headline, titles, About and role text.
- Spotlights that lift a profile: Open to Work (recruiters only), Active Talent (recent profile update, resume
  shared), Interested in your company, company connections, verifications (V: LinkedIn help).
- Top Applicant ranking (Premium) shows a percentage only in the top half and only after a few applicants (V: LinkedIn
  help); its inputs are "role, experience, and skills"; applicant order effect is UNVERIFIED.
- "Apply early gets Nx" figures have no traceable source; use the freshness window instead.

## 2. Alerts and filters to have
- Daily alerts, email and notification, at the levels and Full-time type in the owner's policy config: one per target
  title and location scope. Where the owner's scope is wider than one city, give each city-only search a
  country-wide twin as slots allow; the Recruiter decides which by yield.
- Search URL for a person-present browse, last 24 h:
  `linkedin.com/jobs/search/?keywords=...&location=<place>&f_E=3%2C4&f_JT=F&f_TPR=r86400`; remote: add `f_WT=2`.
- Alert mail (jobalerts-noreply@linkedin.com) is parsed by job_alerts.py; the lane never reads LinkedIn in bulk.

## 3. Before any apply
- README gates. Then on the job page: is the button "Easy Apply" (part A) or "Apply" with an external-link icon
  (part B)? A LinkedIn read in the owner's Chrome is allowed with the owner in session; one page at a time, never a
  scrape (User Agreement 8.2.2, 8.2.13; V: platforms.yaml notes).
- Check "Applied" on the job card and LinkedIn's "My jobs, Applied" list for a duplicate.

## Part A. Easy Apply (the owner's click; employer site first)
The registry line stands: never Easy Apply through an agent; no extensions. First look for the same role on the
employer's own career page and apply there (part B). Only when no employer page exists:
1. The lane prepares the answers sheet: every screening question LinkedIn shows (read on screen), its answer from the
   README sources, and which stored resume to pick (the stored base resume; a tailored page means the owner uploads
   that file in the flow).
2. Easy Apply steps (per-job questions vary): Contact info (prefilled from the profile), Resume (choose), Additional
   questions (years with X, authorization, sponsorship, relocation, pay), Review, Submit. "Follow <company>" is ticked
   by default at the end: untick it unless the owner says otherwise.
3. Years-with-skill number fields: the true months from the record, never rounded up; a field that asks more than the
   record shows is answered truthfully and the row flagged (a knockout risk).
4. The row goes on the card under "Your clicks" with the job link and the sheet. The owner submits; the lane logs it
   from the owner's word and the "Application submitted" screen or mail (`--via other`, "portal: linkedin easy apply").

## Part B. Jobs that open an external site (agent)
1. Click "Apply" on the LinkedIn job page; it opens the employer's site in a new tab (UNVERIFIED that every external
   apply opens a new tab rather than the same one). Take the new tab as this application's tab and record its id.
2. Note the ATS from the URL (ats_detect.py) and follow that playbook (greenhouse.md, ashby.md, lever.md, workday.md,
   smartrecruiters.md). Unknown ATS: read the form, fill by the README Chrome rules, note what you learned here.
3. "How did you hear about us": LinkedIn.
4. Prefer the employer's own posting: if the job id already exists from the company-ats poll, use that record (dedup
   prefers ATS records; V: platforms.yaml company-ats).
5. A link that goes to a staffing site or a reposter: the employer gates apply, skip.

## Login walls and prompts
- LinkedIn signed out: stop, "Needs you". An employer site asking to create an account: README stop rules.
- "Apply with LinkedIn" buttons on employer sites share the profile: the owner's click only.

## Verify and log
- Part A: LinkedIn shows "Your application was sent to <company>" and sends mail from jobs-noreply@linkedin.com
  (UNVERIFIED exact text). Part B: the ATS playbook.
- Log: README. Part A `--via other`, part B `--via ats`; evidence names "portal: linkedin".

## Observed log (README step 11b)
- none yet

## 9. Scout query set and method notes (LinkedIn scout lane)
One pass a day, last 24 h (72 h outer limit), relevance before volume.

**Query set (reuse tomorrow; fill in the owner's titles and places from the policy config):**
- Jobs, home area: `/jobs/search/?keywords=<title>&location=<place>&f_TPR=r86400`, one query per target title. Do not
  sort by date with a broad title: it floods with generic roles.
- Jobs, country-wide remote: `keywords=<title>&location=<country>&f_TPR=r86400&f_E=3%2C4&f_WT=2`.
- Posts: `/search/results/content/?keywords=%22I%27m%20hiring%22%20(<title terms joined with OR>)&datePosted=%22past-24h%22&sortBy=%22date_posted%22`.
  First-person "I'm hiring" beats "we're hiring": the latter is mostly staffing spam.
- Messages: `/messaging/`, check the list for replies from people (not the owner's own last line).

**Method notes:**
1. In the job list, promoted cards are mostly staffing firms: skip them by the employer gates; real employers are
   mostly unpromoted or hirer-promoted startups. Read only the first two pages per query.
2. The result list only renders cards you really scroll to; programmatic `scrollTop` does not render them. Use a real
   scroll, then read `li[data-occludable-job-id]` in the page.
3. A job page header gives "posted X ago" plus applicants or "N people clicked apply" and whether the button is Easy
   Apply or "Apply" (off LinkedIn). The "Apply" target URL is not exposed in the DOM, so `employer_url` needs the
   company's career page (the scout role).
4. `pace.py` has no read action; log reads in the lane's state file.
5. Messaging opens on the last thread; reading the list does not mark threads read, opening one does.
6. LinkedIn has no 72 h filter: it offers "Past week", so read each card's age and ignore older ones.
7. Before trusting a LinkedIn employer, open `linkedin.com/company/<slug>/about/`: a same-named staffing firm is not
   the product company.
8. A company's own board may sit behind a third-party job-board host; a search box filters it, and `find` reads the
   link when page JavaScript is blocked.
9. "Reposted X ago" on a job page means the original date is older; the LinkedIn job id (lower number = older) shows it.
10. The browser lease hook requires a screenshot of your own tab before each action after another session used Chrome.
