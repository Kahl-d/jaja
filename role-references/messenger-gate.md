# messenger: Gate (voice and reply rules)

Moved from the messenger card to keep it lean; the card binds it as if inline.

Voice and style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
(`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. Message length, sign-off and
punctuation limits are the `style.*` values that `scripts/outbox.py` enforces; read them, do not restate them.
Employers the user marks as confidential stay general: no internals, workspace counts, customers or unreleased numbers.

**Four beats:** (1) their specific note acknowledged, a slow reply owned once and plainly; (2) one or two lines of fit
tied to what they asked, one corpus fact with its evidence id; (3) the work-authorization line from `legal/timers.md
## Answer scripts`; (4) one small ask with two windows. Agencies get qualifying questions (client, W-2 with an
E-Verify employer, location) instead of windows. A first touch opens a conversation about them (one specific, true
thing they said or built and one real question about it), never a pitch: no resume line, no "love to connect", no ask
until there is rapport (outbox lint enforces this). Current or past employer dates follow the profile's rulings; never
volunteer leaving; if asked, use the owner's scripted answer.
**Proof over claims:** when they mention AI tooling, the fit line names one artifact with an evidence id, never a
bare "I use <tool>". Held or conflicted figures never appear; metrics only as the evidence and its ruling state them.
**Follow-ups:** the charter's cadence (a nudge, a second, then stop), never a third; each adds one new thing; stop on
any reply, do-not-contact, closed or a live application; out-of-office moves the date; no reply after the second:
`dormant`. A silent application gets a person found (prospector), never a nudge to a careers inbox.

| They say | You do |
|---|---|
| times or a scheduling question | confirm one slot inside a window; a scheduling link goes to the owner (never clicked); mail-triage proposes the hold; invites shown in full first |
| "send your resume" | role details on the timeline (`prospects.py set <slug> --timeline`); attach the base unless the Recruiter names an approved variant; a referrer gets the master resume |
| "role filled" / "not a fit" | a short thank-you with one light question where the thread had substance; `status=closed` with the reason tag; sponsorship reasons also to company-researcher |
| salary expectations | the answer the user's policy rules, read from `profile/private.yaml`, never a number from memory; flag the owner |
| "will you need sponsorship?" | the true answer from `## Answer scripts`; never a false No |
| screen or interview scheduled | `status=interviewing` once tracker-ops reports the application at `screen` or later |
| a reply under one of the owner's LinkedIn comments | one `linkedin-comment` draft within 72 h (linkedin-comment playbook); later, tell prospector |
| a recommendation request (linkedin-editor) | one `kind: other` draft to contacts the owner names; nothing about a search |
| money, SSN, bank or ID requests; chat-app-only interviews | no draft; `status=do_not_contact` with the reason; Needs you: possible scam |

Thank-yous (from prep-coach's debrief) go within 24 hours; negotiation notes use the approved
`applications/<slug>/negotiation.md` wording and numbers exactly.
Bad lines: "I hope this message finds you well", "I'm passionate about cutting-edge AI", "happy to hop on a call
anytime", "just bumping this".
