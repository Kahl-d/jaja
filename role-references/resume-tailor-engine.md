# resume-tailor: Procedure: the engine steps and the truthfulness pre-flight

Moved from the resume-tailor card to keep it lean; the card links here and binds it as if inline.

## Procedure: the engine steps (this department's paths in brackets)
1. **Intake the target.** For a specific job: save the JD to `target-roles/<company-role>.md` from
   the template [`applications/SLUG/jd.md`, written by fit-scorer]. Extract role, seniority,
   must-haves, nice-to-haves, ATS keywords, domain, and what the team clearly values. For an
   umbrella role: note the role archetype and what top candidates show [the lens in `research/lenses.md`].
2. **Angle from the package, questions before drafting.** The angle comes from the Role Package
   (`research/targets/SLUG.md` sections 1, 5, 6 and 7, and the lens file in `profile/lenses/`). The focus profile's
   section 7 (`profile/lenses/<family>.md`) gives the best foot for this focus: headline, Skills row order, project
   order and the wording each bullet takes; start from it. Section order: when the JD asks years or the title carries
   seniority, move Education after Experience in the variant (the rest keeps the house order; render_resume's lint
   accepts it); the base is unchanged. Section 6 is the Reader model: each top want is met by its answer-table line,
   in the reader's words from "Their words", placed where that reader looks first. When the true line for the top
   want sits low in the fixed order, the entry's context line and the first bullet's wording carry its result in the
   reader's words, so the want is met in the first 30 seconds without moving a bullet. Use the Reader model's "Their
   words" terms in those lines, and carry the top want whole (every part of it, not half in the lead line and half
   in a later bullet). Only true record lines; a want with no line stays a question. Before the editor: `python3
   scripts/reader_model.py check research/targets/SLUG.md --resume V/resume.yaml` and every W1 to W3 warning
   answered in NOTES. The only stop before drafting is the package's questions (at most five), which the Recruiter
   asks the owner; the plan and the page then reach them together (one role at a time). Write `NOTES.md` `## Plan`
   (angle; lead, support, cut against the base; each change with the base line beside it and one "why"). A change to
   the base itself is its own job, on the owner's word.
3. **Select & map.** Pull experiences + specific bullets from the **confirmed** experience knowledge
   that ladder to the target. Map the owner's real work onto the dimensions the role cares about: the JD is a
   *lens* to surface and emphasize real work, never to import claims. [The selection algorithm is in
   `role-references/resume-tailor-selection.md`.]
4. **Draft.** Produce the resume in `resumes/<target-role-or-company>/` [`resumes/variants/SLUG/`,
   copied from the general base]: the resume source (markdown) [RenderCV YAML], plus a short `NOTES.md`
   recording what it was tailored for (target, JD highlights, experiences/bullets featured, angle);
   length/format to target (1 page default; adjust per role norms).
5. **Review & iterate** with the owner. Sharpen wording, ordering, emphasis. [resume-editor first,
   then the owner in the approval batch.]
6. **Fact change: fix the source first.** If drafting reveals a fact needs to change, update the
   **experience** file first, then regenerate the resume from it. Commit both. [You do not edit
   `profile/`: file the fact, its evidence and the proposed wording in `issues.md` for the Recruiter;
   regenerate after profile work or the owner lands it.]

**Truthfulness pre-flight (run before any resume is "done"):**
- [ ] Every claim traces to confirmed experience knowledge (no new facts here).
- [ ] No fabricated metrics, tools, scope, titles, or dates.
- [ ] Consistent with the canonical facts (titles, project names, dates, metrics): no reintroducing
      old cross-version contradictions.
- [ ] Domain gaps handled honestly (transferable framing, not false claims).
- [ ] Keywords/ATS terms present *only* where they reflect real work.
- [ ] Length and format fit the target.
