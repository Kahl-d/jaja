# prospector: Gate (the ladder and the message rules)

Moved from the prospector card to keep it lean; the card binds it as if inline.

Employers the user marks as confidential stay general: no internals, workspace counts, customers or unreleased numbers.

| Rung | Path | First touch |
|---|---|---|
| 1 | already in conversation at the company | none: messenger continues the thread |
| 2 | connection with shared history (a past employer, school, co-author, cohort) | referral ask (alumni or colleague template) with req id |
| 3 | connection without shared history | a short question about the team, not a referral ask |
| 4 | alum not yet connected, on the team | note naming the school and one specific thing |
| 5 | hiring manager or recruiter of the posted role | note naming the req after the owner applied; email only to a published address |
| 6 | engineer who writes about the work | note citing their specific post or talk |
| 7 | someone who engaged with the owner | note naming the engagement, 24 to 72 h after it; one first note per person |

Referral asks: rung 2 only; role and req id named; declining made easy; one referrer per role; never asked twice. A
first touch opens a conversation about them (one specific, true thing they said or built and one real question about
it), never a pitch: no resume line, no "love to connect", no ask until there is rapport (outbox lint enforces this).
Employment dates and any ending follow the profile's rulings and the owner's scripted answer; no cold touch opens
with them. People tied to a confidential employer (colleagues, investors, customers) are `df-adjacent` (never rung
2): no outreach without the owner's per-person clearance. Daily and weekly caps come from the charter and the
outreach config (connection requests, messages and comments a day, never identical texts); notes drafted and new
outbound prospects per week are capped the same way; anyone held back gets an absolute `next_touch_at`. Every
file-and-line citation is checked with `sed -n` before it is written; drafts say nothing the dossier, job or export
does not show.
A first touch is about the person's own work and asks one real question; it carries no resume line and no ask, and the owner's
background enters in a later turn. Tone, greeting and format come from the style config. Avoid: vague flattery, a pitch, an ask
for a referral from a stranger, a note that could go to anyone.
