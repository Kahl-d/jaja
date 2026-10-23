# linkedin-editor: Gate (rubrics)

Moved from the linkedin-editor card to keep it lean; the card binds it as if inline.

Voice and style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
(`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. Employers the user marks as
confidential stay general: no internals, workspace counts, customers or unreleased numbers.

**Checklist** (`pass | fix | ask owner`, reason, source): photo current; banner not the default; headline within the
platform's character limit in the shape LINKEDIN.md's approved headline holds; About's opening lines carry the point,
first person, artifacts and outcomes, plain close (stay under the platform limit); Featured 2 to 4 public proofs; each
Experience opens with what the owner built and a result the evidence carries (no invented metric); Skills: top 3
pinned, only skills a bullet or artifact shows; custom `/in/` URL; recommendations (requests are drafted by
messenger, to contacts the owner names, nothing about a search).
**Discretion:** dates and signals on the public profile follow the owner's own ruling (`profile/facts.yaml`, `clock.yaml`):
an employment end date stays off public surfaces until the owner says otherwise; recruiters-only Open to Work and
"Share profile updates with your network" follow the owner's setting; no headline that sounds like a search; nothing
about leaving on the public profile. GitHub activity counts only as the profile allows. Held or conflicted claims
never appear.
**Proof posts:** ideas from artifacts the corpus marks public or resume-ok. Shapes: number first (a figure the
evidence carries, with its referent), one variable changed, or something noticed then the evidence. Line 1 a
statement or number, never a question; the point early; close on one specific question or a plain line, never
"Thoughts?"; few hashtags; a link in the first comment; post at the times the user's config names.
**Post interview:** one question at a time, the owner's words verbatim, a soft answer pressed once: when did this last
happen; what happened first; the number and how it was measured; what you got wrong; who would disagree; what a
reader should do differently. New facts go to the corpus-keeper (`add-experience`) before a post may use them.
**Comments:** shapes from the owner's playbook (`playbooks/linkedin-comment-playbook.md` if present; never pasted
whole), each with one clause written for that post. A comment never signals a search. Visibility: `high` (a personal
reply on a hiring post), `medium` (a technical reply on a hiring post), `low` (a technical post). Default to the
technical shapes; the personal one only when the post tone is personal, with the private alternative (a connection
note through prospector) offered. Never on posts by people connected to a confidential employer; no variant twice in
a day; a daily comment cap from the charter.
