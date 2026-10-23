---
title: Portal playbooks for mass applications
type: playbook
owner: lane:apply
sources: the charter under docs/ (apply lane and safety sections); research/portals/; state/recruiter/portals/*.md; targets/platforms.yaml
---

# Portal playbooks

One specialist file per portal (the charter's apply lane: "a specialist agent per portal knows it completely"). The
apply lane (`lanes/apply/AGENT.md`) reads this README once per session, then the file for the portal of the item it
claimed.

Tags used in every file: **(V: source)** means a record in the user's data folder says it (a read-back, a submit
made, a platform's own page quoted in research); **UNVERIFIED** means no record proves it yet: do it, then write what
you saw back into the file with the date. Every other claim says UNVERIFIED. Each playbook opens with a "You are the
<portal> specialist" persona (a senior recruiter who knows that portal's matching, form quirks, terms rule and alert
wiring) and ends with its Observed log (step 11b).

## Index

| Portal | File | Submit mode | Why that mode |
|---|---|---|---|
| Greenhouse (career pages, MyGreenhouse) | greenhouse.md | agent, on the batch yes | employer ATS |
| Ashby (career pages) | ashby.md | agent, on the batch yes | employer ATS |
| Lever (career pages) | lever.md | agent, on the batch yes | employer ATS (flow UNVERIFIED until a submit is on record) |
| Workday (career pages) | workday.md | saved package, the owner signs in | a candidate account and password per employer |
| SmartRecruiters (career pages) | smartrecruiters.md | saved package, the owner uploads | upload sits in shadow DOM |
| YC Work at a Startup | yc-waas.md | agent, on the batch yes, message shown in full | an apply is a message to the founder |
| Wellfound | wellfound.md | agent, on the batch yes | agent_terms none, load-based clause (platforms.yaml) |
| LinkedIn, external-site jobs | linkedin.md part B | agent on the employer ATS | the apply happens on the ATS, not on LinkedIn |
| LinkedIn Easy Apply | linkedin.md part A | the owner's click (employer site first) | agent_terms bars-bots: User Agreement 8.2.13 |
| Indeed Apply | indeed.md | the owner's click (employer site first) | agent_terms bars-bots: terms A.3.5 |
| ZipRecruiter 1-Click | ziprecruiter.md | the owner's click (employer site first) | terms unreadable to agents, so agent_terms stays names-agents |
| Glassdoor | glassdoor.md | link-out to the ATS, else the owner's click | agent_terms names-agents |
| Google Jobs | google-jobs.md | link-out to the destination | Google is a search box; the apply is on the destination |
| HiringCafe | hiringcafe.md | link-out to the employer ATS | it indexes career pages |
| Welcome to the Jungle | wttj.md | agent on the batch yes (WTTJ apply), else the employer ATS | agent_terms none; alert links sit behind trackers |
| Handshake | handshake.md | employer ATS by the agent; a Handshake-hosted apply is the owner's click for now | terms bar bulk collection; applying by agent not yet read in the terms (UNVERIFIED) |
| hackajob | hackajob.md | no apply: interview requests, each accepted on the owner's yes | candidate terms have no automation clause |
| Built In | builtin.md | no apply by the hunt lane; role applied on the employer ATS | agent_terms none, probation, no account |
| Jobright | jobright.md | link-out to the employer ATS | terms bar any automated system; Agent and Autofill stay off |
| Dice (and connector) | dice.md | direct-employer roles to the employer ATS; never Dice Easy Apply | terms name agents; the official MCP is the agent route |
| VC networks (Getro and others) | getro.md | network profile on the owner's sign-in; jobs on each company's ATS | Getro bars crawling |
| X (hiring posts, people) | x.md | no apply; drafts for the owner's yes; the role on the employer ATS | official API only |
| Jack & Jill | jack-and-jill.md | no apply; introductions on the owner's yes | bot-registered accounts are removed |
| iCIMS (career pages) | icims.md | saved package, the owner signs in per employer | an account per employer |
| Workable (career pages) | workable.md | agent, on the batch yes | no account needed |
| Oracle Recruiting Cloud, Taleo | oracle-taleo.md | the email code is the owner's, then the agent | 6-digit email code; Taleo account per employer (UNVERIFIED) |
| Eightfold (AI career sites) | eightfold.md | saved package where an account is needed, else the agent | matching scores the resume 0 to 5 |
| Avature (enterprise tenants) | avature.md | saved package, the owner signs in per employer | per-company login |
| Jobvite (career pages) | jobvite.md | agent, on the batch yes | applying needs no account |
| SAP SuccessFactors (career pages) | successfactors.md | saved package, the owner creates the account | account = talent community |
| Large-employer careers sites | large-employer-careers-sites.md | the owner's clicks until the terms are read; referral first; above-gate seats via a person | sitemap and JSON-LD reads, one page at a time; rate limits |
| Apply by email | no file: outbox | a message: outbox.py and its approval | an email apply is an outward message |

**Platform terms.** LinkedIn (User Agreement 8.2.13), Indeed (A.3.5: no "automation, scripting, or bots to automate
the Indeed Apply process") and ZipRecruiter 1-Click (names-agents) bar agent applies. For every job found on any
portal: first look for the same role on the employer's own career page (`python3 scripts/ats_detect.py`, the company's
board in targets/companies.yaml, or the career site) and apply there by its ATS playbook; that is the agent's to
submit on the owner's batch yes. Only a role that exists solely as Easy Apply, Indeed Apply or 1-Click goes on the
card under "Your clicks": resume and answers sheet ready, the job open in a tab, the owner presses the portal's own
button. Sign-in walls: a new account per employer, or an upload the tools cannot do, goes under "Needs you" with the
package ready; once the owner has signed in there the agent continues in the owner's session. "Follow this company"
boxes: untick unless the owner says otherwise.

## One presence record and the sign-in method

- **Source of truth:** `state/recruiter/portal-inventory.yaml` (writer: the Recruiter) holds each portal's setup state:
  account, resume (with the file's sha256), alerts and the setup fields, plus `sign_in` and `registry`.
  `targets/platforms.yaml` `presence:` (read by portal_report.py and the job-search MCP's portals_list and portal_get)
  must agree with it: `python3 scripts/check_portals.py` fails on any disagreement (account done means presence
  exists; alerts done means on or arriving; a resume held there carries the same sha256, and its text names the same
  base), on a registry presence with an account but no inventory row, and on a row without `sign_in` or `registry`.
  Fix the stale side; never edit one file without the other.
- **sign_in:** google-sso, email, linkedin, indeed-login, unknown or none (a one-time form, no account). The method only:
  never a password, code or token anywhere. Codes and passwords are always the owner's. A portal whose method is not
  proven stays `unknown` until a person-present read of the account settings or one question to the owner.
- **Unknown is a state:** a setup field not proven yet is `unknown` (open), never left blank and never guessed done.

## Alert mail: sender decisions

Every alert sender gets a decision recorded in the registry (`ignored_senders`, mapped senders). Decision kinds:

| Decision | Meaning |
|---|---|
| job-alert: <platform id> | the sender sends job cards; parsed by job_alerts.py under that platform |
| job-alert, ignored | a re-aggregator of other boards, mostly off-target; the owner unsubscribes |
| noise, ignored | sourcing pings or blasts with no job links |
| marketing, ignored | newsletters with no job cards |
| recruiter requests, not alerts | a marketplace that sends interview requests, never job lists (job-scout maps it) |

Every ignored sender carries `kind: job-alert | marketing | noise` (platform_yield.py --check). Hiring systems whose
mail is read as ATS mail (mail_classify ATS_DOMAINS) include Oracle Recruiting Cloud (oraclecloud.com), Eightfold,
Avature and Gem besides Greenhouse, Lever, Ashby, Workday, iCIMS, SmartRecruiters, Jobvite, Workable, Taleo,
SuccessFactors and the rest.

## The common apply loop (every portal)

1. **Claim.** `python3 scripts/day_queue.py claim --lane <your lane name> --kind apply` (or `--kind portal`). One item
   at a time. The item names the job id or slug, the effort level and the Recruiter's notes.
2. **Gates before any work.**
   - Applied before? Read `applications/<slug>/application.md`: a stage of applied or later is a duplicate; stop and
     hand it back. Search Gmail for a confirmation from that company too (late confirmations exist), and the portal's
     own "applied" list where it has one.
   - Personal path? A company on the owner's personal list or a person carrying it: not mass. Hand it to the focused
     lane (`kind: focused`).
   - Real employer: a staffing firm, body shop or unnamed client fails. Years gate (policy config): a years ask over
     the owner's engineering months is a STRETCH and goes only through a person, never cold. Location: the owner's
     location preferences in the policy config; locations outside them fail.
   - Already super aligned at a strong company (a high fit score, a dream company, a warm post; thresholds in
     `policy.tiers`)? Transfer to focused (`kind: focused`) with one line why. Mass is for the rest.
3. **Effort ("Effort follows importance", charter).**
   - **E2 quick apply (default for mass):** the base resume unchanged (the base of record under `resumes/bases/`)
     when `python3 scripts/apply_batch.py --tier mass <slug>` gives READY. The base still passes the role's matrix and
     rubric (apply_batch gate 4); a mass role that fails is promoted, not sent.
   - **E3 tailored:** a big company, heavy alignment, a high fit score, a dream company or a warm path: a role package
     from the research lane and a page from the resume lane (the charter's review stages and the company recruiter
     APPROVE). The apply lane never writes a page itself.
   - Research stays real in both: even an E2 row gets the 5-minute reverse-engineering pass below.
4. **Reverse engineer the posting, five minutes for E2.** Identify the ATS from the URL; list the hard filters
   (years, location, authorization, degree, clearance); take the 4 to 6 concrete criteria a recruiter would type into
   the ATS (Ashby criteria, Greenhouse calibration skills) and check the page has one quotable sentence for each
   (research/portals/portal-matching-algorithms). Write the senior recruiter's one-line answer to "who is the ideal
   candidate" at the top of the package. Missing a sentence for a must-have: promote to E3 or skip.
5. **Pack.** `python3 scripts/apply_pack.py build <slug> --today $(date +%F)` then `python3 scripts/apply_pack.py lint
   <slug>` until `lint: clean` (a tailored page); for a base row, the answers come from `python3 scripts/apply_pack.py
   answers`. Batch: `python3 scripts/apply_batch.py --tier mass --file <list>`.
6. **Open and read the form** in your own tab (Chrome rules below). Read every field, required mark, select option and
   essay before typing anything. Record them in the package file.
7. **Fill** from the package only (answer sources below), by the portal file's typing method. Upload the page first.
8. **Read back** every field by script after the last change. Save the package file
   `state/apply-batches/<date>-<lane>/<slug>.md` with the form readback table (field, answer, source), the essays, the
   page path and sha256, the tab id and "stopped at Submit".
9. **Ready card** (format below): the record of the batch. Mass rows already carry the owner's recorded yes from the
   morning cards (the queue item's `approval:`, `apply_batch.py --approval` "submit on A-..."): one yes, never a
   second. A row with no recorded yes (a focused row, or a mass row whose item has no approval) goes to the Recruiter,
   who puts the card in front of the owner and records the answer with `approvals.py decide`.
10. **Submit on the batch yes** (charter): only the rows the recorded yes names, each re-checked (tab still filled, not
    already submitted) right before its click; then the approval receipt (`approvals.py receipt ... --no-channel-log`)
    and the tracker event.
11. **Verify** the confirmation screen text, then the confirmation mail when it comes. **Log** at once (below).
11b. **Observed log (the specialist learns).** Append one dated line to the portal's playbook under "## Observed log":
    `- <YYYY-MM-DD from date +%F> <company> (<tenant or host>): <field or step>; <quirk seen>; <what worked>`, for every
    apply, held or submitted. A line is a fact seen on screen, never a guess and never a value the owner typed (no codes,
    passwords or personal answers). When the same quirk shows twice, move it into the playbook's own sections with its
    (V: ...) evidence and mark the old UNVERIFIED line resolved. The lane that applied is the writer for that line;
    the playbook's owner (lane:apply) folds lines into the sections at the weekly review.
12. **Results card** to the Recruiter. Release the claim with the result.

## Chrome and tabs (every portal)

- One tab per application, opened by this lane, in this lane's own window (browser lease: mode `windows`, each lane in
  its own window, focusing its own tab once per turn). Record the tab id in the package.
- Never touch, reload or close a tab you did not open in this turn. A tab that shows submitted without your click is
  the owner's work: ask, never close.
- Never press Enter in any application form (some forms submit on Enter). Move between fields by click or Tab.
- Text fields: click the field by screenshot coordinate (ref clicks can leave React fields empty), cmd+a, type the
  value, then read the value back by `javascript_tool`. A batch log is not proof.
- After any upload: wait for the parse and the re-render, re-read every field (autofill fills and can double text or
  rewrite a link), then fix by cmd+a and type.
- Dropdowns: open by click, screenshot, click the visible option; never type-to-filter and accept (substring match:
  "Man" can pick "Woman"). Read the chosen value back by script.
- Yes/No buttons and radios: click, read back; if Submit later says "Missing entry", click another option and then the
  right one again.
- Careers sites: `python3 scripts/browser_pace.py check <url>` before a page read and `mark` after; on a 429, an
  "access denied" page or a CAPTCHA, `browser_pace.py blocked <url> --note "<what it said>"` and stop that host.
- Never read an ATS's private API (Ashby /api/ is robots-disallowed); read forms on screen.

## Answer sources (which file answers which question)

| Question | Source | Rule |
|---|---|---|
| Name, email, phone, location, LinkedIn, GitHub | profile/facts.yaml identity (through apply_pack) | website stays empty while the profile says so |
| Legal name | profile/private.yaml legal_name | the owner's file; never copied into a repo file |
| Authorized to work in the US | legal/timers.md "## Answer scripts" | the approved script |
| Sponsorship now or in future | legal/timers.md "## Answer scripts" | the approved script; an employer whose form forces a different answer is flagged to legal-visa the same day |
| Work authorization in words | legal/timers.md Written scripts | a one-line field takes the same sentence |
| Sponsorship type, deadlines, detail fields | legal/timers.md, the detailed script | |
| Salary expectation | the policy config (`policy.*`) and profile/private.yaml | a posted range: answer inside it; a lower range than the owner's floor: the owner's call on the card ("pay under floor"); a number field takes the floor of the answer; never an unasked number |
| Relocation | profile/private.yaml relocation | match what the portals already show |
| Remote, hybrid, on site | profile/private.yaml remote_preference and the policy config | an unusual on-site ask goes on the card as the owner's call |
| Start date, notice | clock.yaml; private.yaml notice_period | |
| Current employer | profile/facts.yaml (current employment) | per the confidentiality policy in the policy config |
| Years of experience | the engineering-months count in the profile | never round up past the record |
| EEO (gender, race, veteran, disability) | profile/private.yaml eeo | consent checkboxes on demographic data: the owner's call, on the card |
| How did you hear | the job's platform or source | |
| Common screening questions | `python3 scripts/apply_pack.py answers` (the bank) | |
| Essays and short answers | profile/answers/<family>.md (draft bank, evidence-tagged) + the role's Reader model | rules below |
| Candidate AI policy (before any essay) | targets/companies.yaml `ai_policy` (or companies/<slug>.md frontmatter): {binds, rule, url, quote, read_on}; apply_pack.py shows it in every pack that carries an essay | when it binds, the owner writes the first draft, the agent edits, and the pack stays BLOCKED until the application records `first_draft: owner`; not recorded: read the employer's guidance and record it before the essay is used |
| Attestations (arbitration, accuracy, e-signature, AI-use policy, privacy "I agree") | none: the owner's | named one by one on the card; never made untrue |

A question no row answers truthfully is a question for the owner through the Recruiter: put it on the card as an open
field, never guess.

## Essays and free-text answers

- Reverse engineer first: the company's product, the team's words, the JD's first three duties, the senior
  recruiter's "ideal candidate" line. The answer is written to that need.
- Start from profile/answers/<family>.md (the focus family of the role) and the owner's earlier approved answers in
  state/apply-batches/*/; every claim is a record with an evidence id (profile/evidence.yaml), nothing new.
- Voice: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
  (`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. Never copy the JD's words back.
- A good shape: one sentence on what their product needs, two or three sentences of the owner's real work that answer
  it, one sentence on what the owner wants to do there. Keep within the field's limit.
- A must-have the record does not show (a GAP) is never implied: the row goes on the card with the question for the
  owner, through the Recruiter. A new claim not on record also waits for the owner.
- Gate: check_holds on the text (`python3 scripts/check_holds.py <file>`), the style gate, and the Recruiter's readback
  APPROVE on the exact form before the card.

## Login walls, CAPTCHA, new accounts

Agents never create an account, type a password, read a one-time code, or solve a CAPTCHA (safety lines). Sign-in only
through a session already open in the owner's Chrome or an existing Google sign-in that asks for no password. On any
of these: stop, save the package (every field, answer and essay, the link, the page path), put the row on the card
under "Needs you" with the one action the owner must take, and move to the next item. Record an account the owner
makes with `python3 scripts/apply_pack.py account --platform <p> --company <c> --exists yes --email <the job-search
email>` (never a password).

## Verify and log

- **Verify:** the confirmation screen text (quote it in the package), then the confirmation mail when the harvest
  brings it (`python3 scripts/tracker.py reconcile --today $(date +%F)`). A blocked Submit sent nothing: read the error,
  fix, re-read every field, submit again on the same yes.
- **Log at once, same turn:**
  ```
  python3 scripts/tracker.py event <slug> applied --via <ats|email|other> \
    --resume <the PDF that went, path under resumes/> --at "$(date +%FT%T%z)" \
    --evidence "<confirmation text>; portal: <portal id>; tab <id>" --source manual
  python3 scripts/tracker.py path <slug> --path cold --source "mass apply, <portal id>, $(date +%F)"
  python3 scripts/tracker.py project --dry-run
  ```
  `--via ats` for any employer ATS (also when reached from LinkedIn, Indeed, Google or HiringCafe); `--via other` for
  an in-portal apply (Easy Apply, Indeed Apply, 1-Click, WaaS, Wellfound) with the portal named in the evidence;
  `--via email` for an email apply. No folder yet: `python3 scripts/tracker.py new --job <job id>` first. A person
  carried it: `--path person --via <prospect id>`.

## The ready card (one card, one yes)

The apply lane writes `state/apply-batches/<date>-<lane>/00-READY.md`. Rows covered by a recorded yes (the morning
batch card) are submitted and the card is their record and receipt; only rows with no recorded yes are shown to the
owner for one yes, which the Recruiter records (`approvals.py decide`).

```
# Ready to submit · <date> · <lane> · <N> applications
Reply "yes 1-N" (or "yes 1-4, 6") to submit. Rows in "Your clicks" and "Needs you" are not covered by a yes.

## Agent submits on your yes
| # | company · role | link | resume (file, sha8) | effort | answers summary | risks |
|---|---|---|---|---|---|---|
| 1 | Acme Corp · Applied AI Engineer | https://example.test/jobs/123 | base (d4f6da04) | E2 | auth and sponsorship per the approved scripts, pay in range, relocate yes, EEO from private.yaml, 1 essay (why us, 90 words) | none |
| 2 | ... | ... | variant (sha8) | E3 | ... | pay under floor |

## Attestations in these rows (each needs your yes by name)
- Row 2: arbitration agreement; accuracy attestation.

## Open fields (your words needed; the row waits)
- Row 5: a question the record cannot answer truthfully.

## Your clicks (portal terms: employer site first, else your click)
| # | portal · company · role | link | resume to choose | answers sheet |

## Needs you
| # | company · role | portal | the one action | package file |

Gates on every agent row: applied-before checked, real employer, years gate, page gates or base READY, company
recruiter APPROVE on the exact sha, Recruiter readback APPROVE on the exact form.
```

Rules: a row is listed only when every field is filled and read back; the yes covers exactly the rows it names; rows
with an open field, an attestation not named in the yes, or pay under the floor without the owner's word stay at
Submit. With each card send the PDF of each tailored row so the owner can read it while deciding.

## The results card

```
# Submitted · <date> · <lane> · <n> of <N>
| # | company · role | result | confirmation (quoted) | logged |
|---|---|---|---|---|
| 1 | Acme Corp · Applied AI Engineer | SUBMITTED 16:09 | "Your application was successfully submitted." | tracker applied + path cold |
| 3 | ... | HELD | Submit blocked: "Missing entry for required field" fixed and re-sent | ... |
| 4 | ... | NOT SENT | the role closed (page 404) | tracker note |
Still waiting on you: <rows>. Next: <the next claim>.
```

## Sources that never ran: run or retire

Review periodically which discovery sources have never run or yield nothing. Evidence: jobs/ counted by `source` and
newest `first_seen` (read only); state/hn_state.json; targets/platforms.yaml yield and notes; sources.yaml; the
routines audit. Do not change a status in targets/platforms.yaml without evidence clear enough to retire it; most
unread sources are already `manual` (no agent reads them). Record one row per source:

| Source | Last ran, yield | Recommendation | Why | Who |
|---|---|---|---|---|
| <source id> | <date of the last run; jobs found, queued, applied> | RUN, RETIRE, keep manual, or the owner decides | the registry rule or the terms | the owner, platform-scout or job-scout |

Typical outcomes: a monthly thread or API that already queued roles RUNs (update the hunt routine's prompt if its stage
is missing); an API whose keys are absent is the owner's decision (get keys or retire); a feed that stays stale past
its registry rule retires on that date; a worldwide-remote feed retires when the owner's location scope makes remote
not the bottleneck; sources whose terms name agents stay manual; a registry entry with no reader but covered by the
company-ats poll is already running; where the registry and a poll contradict each other on terms, the owner decides
and platform-scout records why.
