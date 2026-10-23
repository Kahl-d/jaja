# resume-tailor: Procedure: the mechanics (the commands)

Moved from the resume-tailor card to keep it lean; the card links here and binds it as if inline.

## Procedure: the mechanics
Work from the data folder. V = `resumes/variants/SLUG`. T = `--template V/template-classic.yaml --density one-page`
(the base's design and density; the house renderer carries it).
1. Set the job `status: tailoring`; `mkdir -p V` and copy `resumes/bases/general/resume.yaml`,
   `template-classic.yaml` and `template-inline.yaml` into V; set
   `sidecar: {lens: <lens>, base_of: general, job: jobs/<job id>.md}` per the YAML contract in
   `resumes/templates/README.md`. Never edit the template copies (the renderer refuses one that breaks
   an ATS rule; the editor rejects any design change).
2. Reorder, select and reword with the algorithm; write bullets in the evidence-id convention of
   `resumes/templates/README.md`: every rendered claim ends with `<!-- ev:ID[,ID...] -->`, entry
   header facts carry `evidence: [ids]`; quote any bullet containing ": ".
3. `python3 scripts/render_resume.py lint --input V/resume.yaml T` until it exits 0
   (missing-evidence, unknown-evidence, em-dash, bullet-too-long, hidden-link, sidecar and heading codes).
4. `python3 scripts/render_resume.py render --input V/resume.yaml --out V T --basename <Name>_Resume`, then the inline copy for
   portals whose parser detaches right-aligned dates:
   `python3 scripts/render_resume.py render --input V/resume.yaml --out V/inline --template V/template-inline.yaml --density one-page --basename <Name>_Resume_inline-dates`
   (add `--docx` when the job's `ats_vendor` is `workday`; `--engine latex` only if RenderCV fails).
   A "page budget" failure lists the longest sections: cut the lowest-relevance bullets there, never
   the arc.
5. `python3 scripts/ats_check.py check --pdf V/<pdf named in V/meta.yaml> --jd applications/SLUG/jd.md --vendor <ats_vendor> --contact-email <owner email> --taxonomy profile/skills-taxonomy.yaml --source V/resume.yaml --out V`
   must exit 0, and the same on the inline copy (`--pdf V/inline/<pdf> --out V/inline`). Keywords: `missing_evidenced` may be added by selecting or rewording an evidenced
   bullet; `missing_unevidenced` ("must NOT add" in `V/ats-report.md`) is off-limits, always.
6. Only the pieces the application asks for: `V/cover.md` if it asks for a cover letter, `V/why-us.md` if it
   asks why this company, `V/else.md` if it has an anything-else field; never a file the form did not ask for. Run the pre-flight; record `shasum -a 256 V/resume.yaml`
   in `NOTES.md` `## Handoff` and hand V to resume-editor (a fresh context: files only).
7. Answer each editor finding in `NOTES.md` `## Responses to review`:
   `E-03 FIXED: <change>` · `E-05 NO-FIX: <argument>` · `E-07 REFUTED: <evidence>`; re-run steps
   3 to 5; new hash; hand back. After approval, add the approved hash to `## Handoff` and tell
   tracker-ops (event `tailored`) and the Recruiter.
