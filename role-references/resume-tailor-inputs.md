# resume-tailor: Inputs read first (exact order)

Moved from the resume-tailor card to keep it lean; the card links here and binds it as if inline.

1. `AGENTS.md`; `WORKFLOW.md` (gates and the work loop); `resumes/METHOD.md`.
2. `resumes/bases/general/README.md` (how a variant is made; the story it keeps), `STANDARDS.md` and
   `resume.yaml`: the starting page, its structure, section order and voice. Verify it first:
   `(cd resumes/bases/general && shasum -a 256 -c SHA256.txt)`.
3. `profile/PROFILE.md` frontmatter `status` (`approved`); `profile/facts.yaml` canonical values and
   every `conflicts:` entry with `status: awaiting-owner`.
4. `applications/SLUG/fit.md` (must-haves, angle, lens), `jd.md`, `companies/<company>.md`.
5. `V/slice.md` first (`python3 scripts/evidence_slice.py --slug SLUG --out V/slice.md`: the taxonomy skills the JD
   names, their evidence entries, the PROFILE.md lines that cite them and where each proof lives; copied, never
   reworded). Then, only for what the slice does not settle: the bullet bank of the one corpus folder a line draws
   from (`profile/corpus/<slug>/README.md`, `## Bullet bank`, ids like `AC-01`), `evidence_slice.py --ids <ev-...>`,
   and `grep -rn "<term>" profile/` for a requirement the slice misses. Never the whole corpus or the whole
   evidence.yaml: each whole read is a large context re-sent on every call. A strong true line outside a bank is still
   yours to find.
