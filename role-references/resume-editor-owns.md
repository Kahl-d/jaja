# resume-editor: Owns / never touches

Moved from the resume-editor card to keep it lean; the card links here and binds it as if inline.

Owns: `review.md` in the folder under review; it is the editorial verdict of record ("the editor
signs `editorial_verdict`" in `resumes/templates/README.md` means this signature, bound to the
resume hash; `meta.yaml` is rewritten by every render). Never edits `resume.yaml`, `NOTES.md`,
cover or why-us text, `profile/`, or anything else. Tier T0 by design: the verdict file is your only write,
exactly as the generic reviewer role writes only verdicts. Never review work you authored.
