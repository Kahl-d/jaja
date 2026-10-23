# resume-editor: Procedure step 2 detail (ruled NOTEs, one-base checks)

Moved from the resume-editor card to keep it lean; the card links here and binds it as if inline.

   A lint `NOTE long-bullet-allowed` (a claim at 3 or 4 lines) or `NOTE labeled-link-allowed` (a link with a short label) is no finding when the ruling it names is the owner's, recorded in `profile/facts.yaml` `rules:` by the corpus-keeper or in `resumes/templates/rulings.yaml` with a `source:` that holds their words (`git log -p` on the file); a ruling nobody can trace to the owner is Critical.
   **One-base checks** (a finding when broken): `sidecar.base_of` is `general` (lint `unknown-base` otherwise;
   there are no per-lens bases); `(cd resumes/bases/general && shasum -a 256 -c SHA256.txt)` passes; V's
   template copies equal the base's (`cmp`), since a variant changes emphasis, never the design; the
   timeline arc of the base (the order its sections and roles tell the career in) still reads in that order; the lens
   appears nowhere on the page or in the PDF title.
