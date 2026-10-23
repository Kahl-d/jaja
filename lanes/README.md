# Lanes: session types that load themselves

> **The charter:** AGENCY-CHARTER.md is the standing charter every lane reads first. Lanes are personas the owner
> starts each day, as many of each as they like; they claim work from the Recruiter's day queue
> (`python3 scripts/day_queue.py claim --lane <address> --kind <kind>`) and hand results back to the Recruiter.

> **The base:** "the base" in the cards and skills means the current general base in `resumes/bases/general/`; its
> README.md names the iteration and its SHA256.txt pins it. Earlier iterations are archived in
> resumes/bases/archive/general-iter<N>/.


A lane is a kind of session, not a role. Each folder here is a bundle: `LANE.yaml` (type, roster, skills, what it may
write) and `AGENT.md` (the lane's persona and procedure). the workspace's sync tool renders, inside each folder,
a complete `.claude/` (settings that make the session run as `lane-<type>`, the roster's agents, the skills, the hooks)
and a `CLAUDE.md` that imports the department rules. Never edit those generated files; edit the two sources.

**Start a lane:** open its folder in the Claude Desktop app (Code tab), or run
`claude --agent lane-<type> --name JS-<TYPE>` from the folder. The first turn runs itself (`initialPrompt`); standing
rulings arrive through the `lane_rules` hook; each role subagent gets its rulings through `role_rules`.

**Flexible by construction:** every step a lane runs is also a skill or a script you can run by
hand in any session. Lanes bundle; they do not own the steps.

| lane | for | roster (from its LANE.yaml) |
|---|---|---|
| recruiter | the head: the sweep, the cards, the day queue, reviews, rulings, approvals, the evening wrap (charter sections 1 and 3) | role-analyst, fit-scorer, tracker-ops, corpus-keeper, legal-visa, messenger, prospector, job-scout, company-researcher |
| posts | the morning posts and people sweep on LinkedIn and X; follows, connections, comments and notes on the owner's yes (charter section 5) | prospector, messenger |
| focused | personal contacts, referrers, people in conversation, the dream companies, warm post leads, super-aligned roles (charter section 6) | role-analyst, company-researcher, prospector, messenger, resume-tailor, resume-editor, base-comparer, technical-recruiter-reviewer, technical-ai-reviewer, hiring-manager-persona, legal-visa, tracker-ops, apply-assist |
| research | understand a role: Role Package v2 (charter sections 2 and 9) | role-analyst, company-researcher, prospector, fit-scorer, legal-visa |
| resume | the page for a role from the base (charter section 8) | resume-tailor, resume-editor, base-comparer, hiring-manager-persona, technical-ai-reviewer, technical-recruiter-reviewer, ai-screener (card pending `/os:new-role`), corpus-keeper, role-analyst |
| apply | mass applications: employer site first, the ready list on one card, one batch yes submits it (charter section 7) | apply-assist, tracker-ops, messenger, legal-visa |
| prep | interviews: pack, mock, logistics, debrief (charter section 11) | prep-coach |
| hunt | optional; the listing sweep and the finding mandate are otherwise the Recruiter's, with job-scout | job-scout, platform-scout, prospector, company-researcher, fit-scorer, legal-visa, messenger |

## Effort follows importance (charter sections 6 and 7)

| kind | what | what runs | command |
|---|---|---|---|
| focused | the people and roles of charter section 6 | the full pipeline (package, matrix, draft, scripts, editor, blind compare, every reader) | the focused lane, `/resume-for <slug>` |
| mass | every other source and portal, no daily cap | the base for a normal fit (E2, no fit.md); more research and a tailored page as importance rises (E3 and up, `scripts/effort.py`) | the apply lane with playbooks/portals/ |

Every page keeps every gate (evidence ids, holds and NDA, rubric_check, ats_check, the rendered page looked at, the
diff against the base). The agent submits on employer sites after the owner's batch yes; LinkedIn Easy Apply, Indeed Apply
and ZipRecruiter 1-Click stay the owner's click. A mass page that fails a gate is promoted to focused; a focused page
with no clear win ships as the base.

## Day to day

**Talk to the Recruiter.** It is the one agent the owner talks to by default; it runs the other lanes and specialists.
Open any other lane only when you want to work on that part yourself.

**Start a lane.** Desktop app, Code tab: open the lane's folder,
`<data folder>/lanes/<type>` (recruiter, posts, focused, research, resume, apply, prep), as many of each as the day
needs (charter section 4). The session runs as `lane-<type>` and its first turn runs itself: it finds its address,
writes its identity into `hub/lanes/<TYPE>.md`, reads its orders and says what is next. From a terminal:
`cd` into that folder and run `claude --agent lane-<type> --name JS-<TYPE>`. If a Desktop session does not introduce itself as the lane, use the terminal command.

**Log an application you sent by hand.** No lane needs to be open. From the department folder:
```bash
python3 scripts/tracker.py event <slug> applied --resume resumes/variants/<variant>/inline/<file>.pdf --at "YYYY-MM-DDTHH:MM:SS-07:00" --evidence "Owner: submitted on Greenhouse" --via ats
```
- `<slug>` is the folder name under `applications/`. A folder that only has a package (jd.md, fit.md) gets its
  application record created on this first event. A role with no folder at all: `python3 scripts/tracker.py new
  --job <job id>` first, or tell the Recruiter the link and it does both.
- `--at` is when you submitted, with the timezone. Leave it out and the event is stamped now and marked
  "time: clock".
- `--via` is one of ats, referral, recruiter, email, other.
- Check it landed: `python3 scripts/tracker.py project --dry-run` lists the slug at `applied`.
- Later events use the same command: `confirmation_received`, `screen`, `interview`, `rejected`, `offer`.
  Follow-ups (charter section 6): a person gets a nudge a few business days after a message and a second later, then
  stop; a silent application gets a person found at the company after the charter's wait. Each is drafted for your yes.

**See where things stand.** `python3 scripts/dashboard.py --status` prints the job clock and the status: the funnel,
today's queue, and what is waiting on you, on companies and on people. The same section opens `DASHBOARD.md`, and
HOME carries one line for it. The CRM is `python3 dashboard/serve.py`. The one number is interviews booked and held,
reported daily with no ceiling (charter section 0; `python3 scripts/interviews.py`; floors in targets/goals.yaml).

**What the Recruiter shows you each morning** (charter section 3): the flashcards in `state/recruiter/cards-<date>.md`,
after the posts and listings sweep. Each card has why it matters, one action, an effort level and, for a quick action,
the draft. You answer "yes 1-12, no 4, change 7: ...".
1. Quick actions run at once on your yes: posts, comments, messages, connections and mass submits, each through
   `playbooks/send-after-yes.md` (your yes recorded, can-send, the act, the receipt, the record).
2. Deep work goes into the day queue; the lanes you start claim it and the Recruiter reviews every result.
3. In the first week you see every non-base page; after that only focused pages.
4. The evening wrap: what went out, what came back, what the numbers say, what changed.
Nothing is sent, posted or submitted without your yes in that session; a card or batch yes covers exactly the listed
items.

**Every lane learns.** Each lane and role closes a working turn with its Learned lines
(`python3 scripts/learn.py add --role <role> --observation "..." --evidence "..."`); the Recruiter reviews them in the
evening wrap and changes the system when about 15 tries or one clear cause support it.

**When an interview lands.** The prep lane (or `/prep-pack <slug>` in any session) writes
`prep/packs/<slug>/<round>.md` from the question bank and your STAR stories, then offers a mock the day before.
After the round, `/debrief` records it.
