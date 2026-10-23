# mail-triage: Gate (match rule, labels, calendar)

Moved from the mail-triage card to keep it lean; the card binds it as if inline.

**Match:** score each candidate by its strongest signal: same Gmail thread as the application's `thread_ids` 0.95;
its req id or job URL 0.90; sender is a prospect linked to it 0.50; its title named 0.40. Attach automatically only at
0.75+ AND 0.20+ above the runner-up; else `review`. A Greenhouse confirmation quoting a req id, no runner-up: auto. A
recruiter writing from one address about two applications (0.50 each): review. Identity is the exact address or
profile URL, never a guess.
**Labels:** per `scripts/tests/fixtures/comms/labeled_emails.yaml` ("what we offer" is not an offer; "unfortunately"
in a reschedule is not a rejection; a sequenced recruiter mail is still outreach; LinkedIn relays judged by content).
Suspected scams (identity documents, bank details, a fee, an offer with no process) are `suspected_scam`, attached to
nothing, Needs you only.
**Calendar:** holds only on the owner's calendar, tentative, no guests; every invite, change, cancel or RSVP is a
`--request` shown to the owner in full first; `execute` is never yours; the commands:
`role-references/mail-triage-calendar.md` (skill `calendar-hold`).
**Speed:** offer immediately; interview invite: hold request within the hour and the thread flagged for messenger;
rejection: same day, no reply draft unless the owner asks.
