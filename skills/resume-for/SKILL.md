---
name: resume-for
description: "Use when the owner, the Recruiter or a lane wants the page for one role, at one of three depths (charter): dream (full pipeline), focused (package, reorder from the base, rubric, blind compare), mass (scripts/mass_page.py: no model call). Any session, by hand or end to end."
---

# Resume for one role (pipeline v2)

Today is !`date +%F`. Argument: `$ARGUMENTS` = `<slug> [--tier dream|focused|mass]` (and optionally the JD path and
the posted title). The base (`resumes/bases/general/`, whichever iteration is current) is the default page. Every step
is a command you can also run by hand; stop at any step the owner asks you to.

Voice and style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
(`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped.

## The three depths (who gets which: the charter, effort by importance)

| tier | when | steps run | time |
|---|---|---|---|
| **dream** (fit at the dream threshold in the policy config, a company marked tier dream in `targets/companies.yaml`, or a warm path) | dream companies, any role with a person inside | 1 to 9, all | 25 to 45 min |
| **focused** (fit in the focused band of the policy config) | career sites and ATS boards, strong-fit startups, founding roles | 1, 2 (matrix filled), 4 (order and at most one swapped line), 5, 7 (blind compare), 9; the editor and readers only when a line changed | 10 to 15 min |
| **mass** (every other source, no daily cap; needs no fit.md) | a normal role the base fits | `python3 scripts/mass_page.py <slug>`: step 4m, every gate of step 5, NOTES; then one look at page-1.png; 9 | about 1 min, no model call |

**Models per tier** (the model policy): before spawning any role below, run
`python3 scripts/model_policy.py pick <role> --tier <tier>` and pass that model to the Agent call; `none` means do not
spawn it at that tier. Mass spawns nothing: a mass page is the signed base or its signed Experience-first copy with
the Skills rows reordered, so every line on it was signed already. **Context:** no agent reads `corpus/**`,
`evidence.yaml` or `PROFILE.md` whole: `python3 scripts/evidence_slice.py --slug <slug> --out V/slice.md` writes the
part of the record this role needs (about 15 KB, copied, never reworded); `--ids <ev-...>` prints any other record.

Every tier keeps every gate: evidence ids on every claim, `check_holds` clean (held terms and NDA terms included),
`rubric_check` exit 0 (the NDA predicate is never spared), `ats_check` exit 0. A mass page that fails a gate is not
sent; it is promoted to focused. A focused page whose compare shows no clear win ships as the base.

**Step 4m (mass only): Skills alignment.** `scripts/mass_page.py` does this step and step 5 deterministically (it
exits 1 when a gate fails: the role is promoted to focused). By hand, the rule it follows: copy the base unchanged
(Education-first to start); the Experience-first copy (same content, per the charter) alternates in only after the
owner approves that page; record the order used. In the Skills block, reorder the rows so the row
that answers the JD's rank-3 need comes first, and add at most three terms from `ats_check`'s "missing but evidenced"
list, each placed on the row whose evidence backs it (never a "must NOT add" term, never a term the taxonomy lacks).
No bullet changes. NOTES `## Plan` lists the Skills changes with their ev- ids.

## Owner-ordered checks on every page (any role, any lane, any depth)
Run in this order inside the pipeline; record each in NOTES `## Owner checks` (score, keywords, red flags, skipped parts, what changed).
- **S1 Senior-recruiter score** (after step 1, before drafting; a clean-context subagent given only the JD and the current page):
  "Act as a senior recruiter for this exact company" -> match score /100, the top 5 missing keywords, the 3 red flags a hiring
  manager spots in under ten seconds.
- **S2 Rewrite Experience** (inside step 4): weave those keywords in naturally and remove the red flags, every bullet in the
  XYZ shape: accomplished X, measured by Y, by doing Z. Every claim true; a metric or detail the record lacks is a
  question to the owner (step 3), never invented. Sentences, not keyword lists; the role's title and function present.
- **S3 Stop the scroll** (after step 6, before step 8): an ATS filter + a senior recruiter skimming 200 resumes for this role
  (how recruiters actually read: top third, titles, first words of bullets, numbers) marks each section skipped or blending in;
  cut what gets skipped, rewrite, re-run step 5 gates, and that is the final version.
- Then the AI-screener check: a clean agent asked the way the hiring person would ask an AI assistant ("is this
  candidate a great match?"); target verdict: a clear "no better person for this job".
- Mass depth (scripts/mass_page.py): S1 and S3 still run as one subagent pass on the scripted page before APPLY submits;
  a red flag that needs a rewrite promotes the row to CRAFT (kind resume).

1. **Understand the role.** First `python3 scripts/stale_check.py <slug>`: STALE means the live JD names skills the
package's JD did not, or the package is old; rebuild the matrix (`jd_matrix.py build`) and refresh the changed rows
before anyone drafts. A `YEARS GAP` in the package's section 3 stops here until the Recruiter (or the owner) says go
(thresholds per the policy config, `policy.*`). A years ask or a senior title: Education after Experience. Record the
tier first: `python3 scripts/tracker.py tier <slug> --infer` (the rule from the fit, the dream board and a warm path).
The owner's own call: `tracker.py tier <slug> <tier> --source "<the owner's words>"`. If `research/targets/<slug>.md`
lacks `## 7 Angle and lead bullet`, run `/role-package <slug>`.
   Read sections 1, 6 and 7, and the lens file it names in `profile/lenses/`.
2. **Fill the matrix.** In `research/targets/<slug>.matrix.yaml`, for every R, Q and P row write `answer:` as
   `line: <bullet id or quote>`, `Q<n>: <question>`, or `GAP: <reason>`, and `ceiling:` from the evidence. Mine the
   record through the slice, not only the base: `python3 scripts/evidence_slice.py --slug <slug> --out V/slice.md`;
   a requirement the slice does not answer is looked up by id (`--ids`) or by `grep` in profile/, never by reading
   the corpus whole. Write each answer with `python3 scripts/jd_matrix.py set --matrix <it> --row <id> --answer ... --ceiling ...`; `python3 scripts/jd_matrix.py check --matrix <it>` must exit 0.
3. **Questions.** At most five, specific (what the owner did, scope, where the proof lives). Ask the owner in chat now, before
   drafting. Answers go to the corpus-keeper (an ev- id) before they reach a page; if the owner is away, draft without them.
4. **Draft** (resume-tailor, as a subagent with the slug, the package, the matrix and `V/slice.md`; model from
   `model_policy.py pick resume-tailor --tier <tier>`): copy the base into
   `resumes/variants/<slug>/` (`sidecar: {lens: <lens>, base_of: general, job: <job file>}`); change only what the
   angle needs: order, and rewrites within each row's ceiling. Write NOTES `## Plan`: every change with the base line
   beside it and one "why" each. No change is a valid outcome.
5. **Scripts**: `python3 scripts/render_resume.py lint` and `render` (both templates, one page),
   `python3 scripts/ats_check.py check` on both PDFs with `--jd`, `python3 scripts/check_holds.py` on the YAML and the
   PDF text, `python3 scripts/rubric_check.py ... > V/rubric-report.txt` (exit 0; the editor reads it). NOTES `## Gates` starts with the line `python3 scripts/gate_versions.py` prints. `python3 scripts/base_drift.py V/` exits 0 (no line from an older base; a base promoted mid-review means a rebase first). Fix and re-run; never edit a script output by hand.
6. **Editor**: `/editorial-review <slug>` (clean context, at most 2 rounds), now checking the rubric predicates
   the card lists as well as truth.
7. **Base compare**: `/base-compare <slug>`; apply every REVERT, re-run step 5. If every line reverts, the page is
   the base unchanged: say so.
8. **Readers**: `/page-review <slug>` (four readers); the page goes on only with the recruiter's APPROVE and the screener's PASS (or the screener NOT RUN recorded while the role does not exist). High findings and a screener REVISE go back to step 4 once; a third loop goes to the owner as a choice.
9. **Present** (per the charter and the owner's review policy: which pages the owner sees before submit, which ship on the
   gates): the apply link, the PDF (SendUserFile), the diff against the base with one "why" per changed line, the
   matrix summary, open questions. The submit runs on the owner's batch yes in the apply or focused lane (employer
   site first; Easy Apply, Indeed Apply and 1-Click are the owner's click); tracker-ops logs it.
