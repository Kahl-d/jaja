---
name: letters
description: "Use when an application asks for a cover letter, a 'why are you interested in <company>' answer or an 'anything else you'd like to share' field: the owner's own ideas in plain words, real work as the proof, the company reverse-engineered first. Drafts only, for the owner's edit."
---

# Letters and open questions

Today is !`date +%F`. Arguments: `$ARGUMENTS` = `<slug> [cover|why|else|all]`.

Voice and style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`, including
`style.letters.*`) and voice guide (`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped.

The principle: a letter presents the owner's ideas and how the owner thinks about the work, then uses real experience as
the proof. It is not a list of facts the resume already holds. For "why us", reverse-engineer the whole organization
(what are they creating, why, what might the founders believe) and answer what the reader is really asking through the
owner's own view, never in the company's own words. For "anything else", be professionally informal and specific.
Always present real experience.

## Inputs (read all before writing)
- The Role Package `research/targets/<slug>.md`: the company signal, the Reader model (wants in the reader's words, fears,
  proof they trust) and the angle. If the Reader model is missing, write it first (`/role-package`), because "why us"
  starts from the reverse-engineered company.
- The focus profile `profile/lenses/<family>.md` (`python3 scripts/focus_profiles.py pick "<title>"`): the vision line,
  the why-this-role answer, the story.
- The answer bank, gated first: `python3 scripts/answers_check.py profile/answers/<family>.md`. Exit 0:
  draw sentences with their ev- ids, never paste whole. Exit 1: do not draw; the findings go to the Recruiter. Exit 2
  (`missing:`): this family has no bank; say so and write from the other inputs.
- The page `resumes/variants/<slug>/resume.yaml` (the letter never contradicts it) and `profile/PROFILE-HOLDS.md`.
- The owner's own ideas in the owner's words: the rulings in `hub/rulings.yaml` that record them, and the corpus
  statements (`owner-statement` evidence).

## 1 · Cover letter (`V/cover.md`, within the form's cap or the length in `style.letters.*`)
Short paragraphs, each an idea first and an experience as its proof:
1. **The idea that meets their problem.** One belief the owner holds about the work that this company's output makes
   real (from the Reader model's top want and the company signal), said plainly. No filler enthusiasm, no restating
   the job title.
2. **How the owner thinks.** How the owner approaches the work, then one real experience that shows the thinking, told
   as a story, never a list.
3. **What was built and how people took it.** Ownership end to end of a real piece of work, tied to what this company is
   building. A second project only when it is the better proof.
4. **What the owner wants to build with them.** One or two plain sentences, then the owner's name.

## 2 · "Why are you interested in <company>?" (`V/why-us.md`, the form's cap)
Reverse-engineer first, in `NOTES.md ## Why-us research` (cited, dated): what they build, why they build it, what the
founders likely believe (their posts, talks, launch notes), what the technology needs to be true. Then answer the
question they are really asking ("will this person care about what we care about, and add to it") through the owner's
view: the problem seen in their space, why it matters, and the real work that shows it has been lived. Their ideas
in the owner's own words: never quote their copy back, never flatter.

## 3 · "Anything else you'd like to share?" (`V/else.md`, the form's cap)
Professionally informal: a short note in the owner's plain voice about how the owner sees the field moving, how the owner
actually works day to day, and one belief. One real example. No resume repeat, no sponsorship or salary (those come from
their files through apply_pack).

## Rules (all three)
- Truth: every factual sentence is true of the record; each paragraph ends with an HTML comment `<!-- ev:... -->`
  carrying its ids. Numbers only from a measured, released row of the numbers ledger, sparingly (`letters_check.py` runs
  `numbers_ledger.py`); employer-confidential material stays general (the kind of system and how it is built, never
  internals, customers or the roadmap); nothing covered by an NDA (`policy.nda_terms`); no held text (`check_holds`).
- Voice: simple words, short sentences, first person, per the style config and voice guide above; the editor's banned and
  review-flag lists apply.
- Ideas, not lists: no bullet lists, no run of three or more tools in one sentence; one proof per paragraph.
- Never quote the company's copy, blog titles or slogans back (all three).
- Write only the pieces the form asks for.
- Run `python3 scripts/letters_check.py V/` and fix every finding. Then the editor reviews (S12a to S12d).
- The owner approves each before it goes in a form on focused pages, and wherever a company's AI policy binds (the owner
  drafts first); mass answers ship on the gates (see the charter).
