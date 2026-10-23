# platform-watch: Procedure step 6 (keep or retire)

Detail for the skill's step 6; the skill links here.

6. **Keep or retire** with the card's rubric under its standing rule: each change is a proposal in the registry (the
   new `status` plus `proposed: {from: <old status>, date: <date>}`; a retirement also sets `setup_priority: none` and
   keeps `access`, `alert_senders`, `alerts_on`), listed under `Proposals` in the log with its numbers, and one
   `issues.md` row for the Recruiter: "ratify or revert at the weekly review <first review day on or after today>". A
   retirement whose `<id>-alert` source is still on: put `--check`'s "switch off" ask for job-scout under `Next:`.
   Tightening filters: an email-alert platform's alert is the owner's (Needs you); an API platform's `queries:` in
   `sources.yaml` is job-scout's (ask). Re-run step 1's check (0 errors).
