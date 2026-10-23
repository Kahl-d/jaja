# Track an application: step 2 details (applied, page approval, backlog records)

Detail for the skill's step 2; the skill links here and binds it as if inline. It
continues step 2's command: `python3 scripts/tracker.py event <slug> <type> --source manual --evidence "<the owner's words
with date>"` (`--dry-run` to preview).

   For `applied`, pass `--resume <the PDF that went: its path under resumes/, or its sha256>` (or
   `--resume none --reason "<why>"`; without it the script refuses), `--at <ISO>` with the owner's stated submission time and
   `--via ats|referral|recruiter|email|other` as the owner applied; without `--at` the script records `time: clock`, so ask for the time and append `applied` again with
   `--at` and evidence naming the line it corrects. The script sets the job's `status: applied` and its `application`
   link. Never record `applied` from a plan or a draft, only from the owner's confirmation or the ATS email. Hand every applied
   SLUG to prospector. On a page approval: the `tailored` event with evidence
   `resumes/variants/SLUG/review.md approved <hash>`.
   **Backlog record:** `tracker.py new --job <id> --discovered-at <ISO>`, then each past event with its own `--at`;
   the stage projects by stated time, not log order (`applied --at <earlier time>` then `rejected --at <later time>`
   reads `rejected`).
