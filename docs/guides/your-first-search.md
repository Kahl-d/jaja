# Your first search

This guide starts from the blank data folder that [getting started](getting-started.md) created and ends with scored
roles and a first draft waiting for your approval. The order matters: the first hour goes into your records, because
every other step reads them.

Work in your data folder (`~/jaja-data` in these examples) and run the commands from the repository
(`~/jaja`) unless stated otherwise. Scripts find the data folder through `JAJA_DATA` or
`~/.config/jaja/config.yaml`.

## What the blank folder contains

`tools/init-data.py` creates, among others:

```
profile/       facts.yaml  evidence.yaml  PROFILE.md  VOICE.md  skills-taxonomy.yaml  corpus/  answers/
resumes/       bases/general/template-classic.yaml  template-inline.yaml   (starting points)
targets/       roles.md  fit-gates.yaml  platforms.yaml  goals.yaml  mass.yaml  x-watch.yaml ...
config/        identity.yaml  style.yaml  policy.yaml   (every key listed, commented out)
hub/           programme.json  registry.json  rulings.yaml  events.jsonl  lanes/  briefs/
runtime/       work/approvals/  logs/runs/  vault/
sources.yaml   the discovery channels the scout can use
```

Everything is plain text. Open the files; there is no hidden state.

## 1. Tell the system who you are (about an hour)

The agents write about you only from records, so build the records first.

1. **`profile/facts.yaml`**: your identity, education, employment, projects and skills. Each value cites an evidence
   id. A new folder has empty sections and a `conflicts: []` list for facts you have not settled.
2. **`profile/evidence.yaml`**: one id per provable claim, with a source (a letter, a diploma, a repository), a
   locator and a capture date. Ids look like `ev-sam-003` in the demo.
3. The quick route is a session: start `claude` in your data folder and run `/jaja:add-experience`. It interviews
   you, one item at a time, and writes the intake note, the evidence ids and the corpus entry.
4. **`profile/PROFILE-HOLDS.md`** (create it when you need it): strings that exist in some source but are disputed or
   unconfirmed, which agents must never add. Figures too private to print go in `profile/private-holds.txt`.

Check your work:

```bash
python3 scripts/check_evidence.py
python3 scripts/check_records.py --all
```

`check_evidence.py` prints, for the demo, `check_evidence: 7 registry ids, 7 cited, 0 problem(s)`. A cited id that is
not in the registry is a problem and is named.

## 2. Say what you want and what rules you out

1. **`targets/roles.md`**: the role families you accept, the titles you exclude, the seniority band and the hard gates
   (location, work authorization, clearance). It is the definition the fit-scorer applies. The blank file has numbered
   sections to fill.
2. **`config/policy.yaml`**: the numbers behind the gates. For example `policy.fit_gates.cold_max_years` (a posting
   asking for up to this many years is applied to cold; more only through a person), `policy.fit_gates.reject_years`,
   `policy.location.home_cities` and `policy.employer.staffing_names`. A key left empty or null turns its check off.
   See [configuration](configuration.md).
3. **`config/identity.yaml`** and **`config/style.yaml`**: your name, signature, time zone, and writing rules (banned
   phrases, dash rules, greeting and sign-off). An empty style file means no style checks.
4. **`profile/VOICE.md`**: a few paragraphs about how you write, which the drafting agents read.

Then run the health check, with your folder named explicitly:

```bash
python3 scripts/doctor.py --data ~/jaja-data
```

On a blank folder it warns that `facts.yaml` has no `identity.full_name` yet. That clears when you fill it in.

## 3. Find roles

Discovery reads public sources into `jobs/<id>.md`, one record per posting, each with its gate result.

1. `sources.yaml` lists the channels (ATS boards, a monthly forum thread, aggregator APIs, alert mail). Review it and
   switch off what you do not want. The ATS boards come from `targets/companies.yaml`, the list of companies you
   want to watch, which you write.
2. In a session, run `/jaja:scout-jobs`. It runs the discovery pipeline, judges the edge cases the scripts cannot
   (title, ghost postings, sponsorship wording), and checks that postings are still live.
3. A posting you found yourself can be added by hand; `scripts/jobs_manual.py --help` shows how.

Nothing is scheduled. Routines are off, so discovery runs only when you ask.

## 4. Apply the hard gates

```bash
python3 scripts/fit_gates.py --all
```

For each job it writes `gate: pass`, `review` or `fail`, with `gate_reasons` and `gate_evidence` (the quoted lines).
The summary looks like `gates: evaluated 7, pass 4, review 1, fail 2; 5 records updated`. Read the failures and the
reviews once: a gate that is wrong for you is fixed in `config/policy.yaml` or `targets/fit-gates.yaml`, not by
overriding a single record. `--dry-run` shows the counts without writing.

## 5. Score what survives

In a session, run `/jaja:score-fit` on a role that passed (or on one the gates sent to review). The
`fit-scorer` writes `applications/<slug>/fit.md`: a report tied to your evidence ids and a weighted score from 1.0 to
5.0. Roles at or above your queue threshold become candidates for a tailored page; the rest follow the base-resume
path. `scripts/effort.py` maps a role to an effort level from E1 to E5.

## 6. Understand one role, then draft

Pick one role you care about.

1. `/jaja:role-package` decodes the posting into a ranked coverage matrix and a reader model: what this team
   wants most, and which of your real projects answers each want.
2. `/jaja:resume-for` produces the page at the depth you choose (dream, focused or mass) from your base, then the
   gates run: evidence ids, holds, rubric check, ATS check, a blind comparison against the base, and the readers.
   Nothing reaches you until it passes.
3. For a person path, `/jaja:research-person` finds and verifies the person, and the `messenger` drafts the note
   in your voice from your records.

## 7. Approve a draft

A drafted message becomes `outbox/O-YYYY-NNNN.md` and an approval request appears in
`runtime/work/approvals/A-YYYY-NNNN.md`.

1. Read the **Exact content** section. That is what will be sent.
2. If it is right, set `status: approved` in the frontmatter and fill `decided_at` (a date). If not, set `rejected`
   or ask the drafter for a new version. Do not edit the exact content: any change voids the approval.
3. Check whether the draft may go: `python3 scripts/outbox.py can-send --id O-YYYY-NNNN`.
4. The `sender` role performs approved actions, one file at a time. Approvals expire seven days after creation.

Until you do step 2, nothing goes anywhere.

## 8. Keep the numbers true

Log what happens with `scripts/tracker.py event <slug> <type> ...` or the `tracker_event` MCP tool. The stage is
computed from the append-only event log. `python3 scripts/tracker.py metrics` prints the funnel with sample sizes.
Open the dashboard to see it ([demo walkthrough](demo-walkthrough.md) shows how).

## When you are comfortable

- Run lanes for parallel work: [lanes](lanes.md).
- Turn on connectors you need: [SETUP-CONNECTORS.md](../SETUP-CONNECTORS.md).
- Enable a routine only after running its work by hand and reading its receipt.
