---
title: Outreach message library
type: playbook
owner: role:messenger
readers: [messenger, prospector, linkedin-editor, mail-triage, lane-recruiter, lane-posts, lane-focused]
links: ["[[job-search]]"]
---

# Outreach playbook: the method

Every message to a recruiter, founder, hiring manager, alum or former colleague is built with this
method, filed through `scripts/outbox.py`, approved by the owner on a card (a card or batch yes covers
exactly the items listed), and sent after the yes by the lane holding the card
(`playbooks/send-after-yes.md`). The messenger owns this page; prospector and linkedin-editor use it.
This page is method only. Tone, wording rules, banned phrases, sign-offs, date formats and anything else
about how the owner sounds: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`)
and voice guide (`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped.
Policy values (work-authorization wording, windows, ceilings) live in the policy config (`policy.*`)
and `profile/`.

## 1. Rules for every message

- **True and traceable.** Every claim traces to `profile/` with an evidence id. Open conflicts in
  `profile/facts.yaml` are used only in their conservative form. Unknown means ask the owner, never estimate.
- **A first touch starts a conversation.** Turn 1 is about them: one specific, true thing they said,
  built or posted, and one real question about it they would enjoy answering. No resume line, no bridge
  to the owner's work, no generic "happy to connect", no ask. The owner's work enters in turn 2, when
  they reply, and only as the conversation needs; an ask comes once there is rapport. A package carries a
  conversation plan (opener, two likely replies, the owner's turn 2), not a single note. Connection
  requests may go with no note. Replies to a thread they opened keep the reply framework (section 2).
- **Voice.** Follow the style config and voice guide. Line 1 names one concrete thing from their message
  or company. One ask, and never in a first touch.
- **Proof over claims.** Name an artifact and what it does, never a tool you merely use (section 8).
- **Measure, never estimate.** `sed '1,/^---$/d' <draft> | wc -w` for email, `| wc -m` for LinkedIn;
  `python3 scripts/outbox.py lint --batch <batch>` prints `{}` (section 9; `create` runs the same checks).
- **From the right account.** The account named in `profile/`; never a work address.

## 2. The reply framework (four beats, in order)

| Beat | What it does |
|---|---|
| 1. Their note, then the delay | shows you read it; owns the silence once |
| 2. Fit, one fact | answers what they asked about with one cited fact |
| 3. Work authorization, positive | removes the question before it becomes a filter (section 3) |
| 4. One small ask, two windows | makes yes easy |

Agencies get qualifying questions in beat 4 instead of windows (T4). Windows come from the recruiter brief
or `state/daily/<date>.md`; each window is offered to at most a few people in a batch and starts at least
two business days after the send; add the other time zone for recipients elsewhere.

Channel limits are enforced by `scripts/outbox.py` from `policy.outreach.*` (`linkedin_max_chars`,
`linkedin_ask_chars`, `first_email_words`, `touch_cap`; defaults in `config/defaults.d/04-outreach.yaml`),
and `outbox.py` refuses a connection note longer than LinkedIn allows for the account type. A comment is
the playbook line, lightly adapted (`linkedin-comment-playbook.md`). A post carries its point in the first
two lines and has no link in the body. Replies go in the recipient's thread: keep `Re: <their subject>`.

## 3. Work-authorization phrasing

The owner keeps approved wording for sponsorship and work-authorization questions in the policy config
and `profile/`, with a variant per situation (default, short, early-stage founder, a yes/no form field,
agency or contract role). The messenger copies the approved variant verbatim and adds a runway date only
when `legal/timers.md` states it. Never write a legal conclusion about the employer, anything about
processing times or odds, or a claim the approved variants do not make. Anything beyond them goes to the
legal-visa role. These lines state facts; they are not legal advice.

## 4. Subject lines

- Replies: keep their subject exactly (`Re: ...`) so Gmail threads the reply.
- New email: a plain, specific subject that names the role or the shared ground and the company.
- Bad: "Quick question", "Opportunity", "Hello!", capitals, emoji.

## 5. Templates

Slots: `{first_name}`, `{when}` (their date or month), `{role}`, `{company}`, `{their_detail}`,
`{fit_fact}` (one cited fact), `{auth}` (the approved work-authorization variant), `{window_1}`,
`{window_2}`, `{req}`. Wording, greeting and sign-off come from the style config.

**T1. Backlog reply to an in-house recruiter:** thank them for the note of `{when}` about `{role}` at
`{company}`, own the delay once, `{fit_fact}` answering what they asked, `{auth}`, ask whether the role is
still open and offer `{window_1}` or `{window_2}`.

**T2. Backlog reply to a founder:** their product or role in line 1; fit tied to what they are building;
the early-stage variant of `{auth}` when it applies; ask for a short call.

**T3. Backlog reply to a hiring manager:** name their team; fit tied to the team's problem; `{auth}`;
"if the role is still open, could we talk `{window_1}` or `{window_2}`?"

**T4. Backlog reply to an agency recruiter:** no windows; a few qualifying questions (who is the client,
what employment arrangement, the location or schedule question); the contract-role variant of `{auth}`.

**T5. Referral ask to an alum with shared history:** the shared history in one clause, the role and req on
the team at `{company}`, whether they would be comfortable referring or pointing to the right person,
`{fit_fact}`, no pressure. Email adds a pasteable blurb (T6); on LinkedIn send the blurb after a yes.
Alumni with no shared history get a connection note first (prospector rung 4), never a referral ask.

**T6. Referral ask to a former colleague (email with a blurb):** acknowledge the gap, name the role, team
and req, ask whether they would be comfortable referring, summarize the work since you overlapped in two
or three lines, then a blurb they can paste: current work, previous work, degree, the role applied for.

**T7. First touch to an engineer you do not know (connection note):** never a referral ask. Cite their
specific post or talk and why it was useful; one clause of shared ground at most. After they accept and
reply with interest: a LinkedIn message about the team; a referral ask only after a real exchange, with
the blurb offered.

**T8. Follow-up 1** (4 business days, same thread, short): add one thing you left out and offer windows.

**T9. Follow-up 2, the last** (9 business days, shorter): make it easier (questions by email, or an
introduction to whoever owns the hire).

**T10. Retired:** two nudges, then stop; silence after T9 makes the prospect `dormant`, with no closing note.

**T11. Thank-you after a call or interview** (within 24 hours): one specific moment from the
conversation, one line of relevance, no new ask.

**T12. "Send me your resume":** attach only an approved variant (`resumes/variants/<slug>/review.md`
approved) or, with the owner's OK, the closest approved base. Say which role the version is for.

**T13. Declining politely:** thank them, a light reason, the door stays open.

## 6. Follow-up cadence

| Touch | When | Content |
|---|---|---|
| First message | day 0 | T1 to T7 (a first touch asks nothing) |
| Follow-up 1 | 4 business days | T8: one new fact, windows |
| Follow-up 2 (last) | 9 business days | T9: an easier path |
| none | 14 business days, still silent | prospect becomes `dormant`; never a third follow-up |
| Silent application | 5 business days after applying | find a person at the company (prospector), then the person cadence; never a nudge to a careers inbox |

Stop at once on any reply, do-not-contact, closed, or an active application through another channel.
An out-of-office moves the next date past their return and counts as neither a reply nor a
follow-up. `scripts/prospects.py due` computes the dates; nobody counts days by hand.

## 7. LinkedIn pacing

Daily ceilings for connection requests, messages and comments, and the spacing between actions, are
configured in the policy config and enforced by `scripts/pace.py`. Texts are never identical (rotate
wording). Messages, comments and notes go out only after the owner's yes in the session; any warning or
CAPTCHA stops LinkedIn for the day. Counts include approved, pending and sent items for that day and week
(`python3 scripts/prospects.py stats`). Forbidden: bots, browser extensions, scraping, auto-connect,
Easy Apply automation, mass sequencing tools, WebFetch on linkedin.com. Free accounts may be limited in
personalized invitation notes: when the composer refuses a note, the fallback (with the owner's OK) is an
invitation without a note and a message after acceptance, or email to a published address. Stop at any
login, CAPTCHA, security check, limit notice or warning, and do not retry that day.

## 8. Confidentiality and proof

- **Search confidentiality.** What the owner may say about their current employer, and that nothing public
  signals a search (no search-sounding headline, no public badge, no posts or comments about leaving), is
  set in the policy config (`policy.outreach.confidential_patterns`) and checked by the gate. Never a word
  against a current or former employer. Employer internals only at the level the profile marks resume-ok.
  Never contact current colleagues, investors or customers about the search without the owner's
  per-person clearance. Public comments on hiring posts are job-search signals: see
  `linkedin-comment-playbook.md`.
- **Proof over claims.** A claim of using coding assistants proves nothing. When a note mentions AI
  tooling or agents, the fit line names one artifact with an evidence id from `profile/corpus/`, and only
  items the corpus marks resume-ok.

## 9. The voice gate (`outbox.py lint`)

Banned phrases, dash rules, AI-tell words, pitch phrases and the other wording checks are read from the
style config: `style.banned_phrases`, `style.no_en_dash`, `style.ai_tell_words`, `style.pitch_phrases`
(regex fragments; empty until the user fills them in). This page does not carry the list. Check a batch
with `python3 scripts/outbox.py lint --batch <batch>`, which prints `{}` and exits 0 when clean;
`outbox.py create` runs the same checks and files nothing while any draft fails. Comment drafts are exempt
from the phrase list only, so the comment playbook lines keep their wording; official letters (DSO,
attorney, HR) keep their formal courtesy.

**Structural tells** (every channel; the gate names the pattern and the fix). Adapted from the
`linkedin-humanizer` rules of sergebulaev/linkedin-skills v1.1.14 (MIT); the patterns are this project's own.

| Tell | Fix |
|---|---|
| Reveal bridge ("The result?", "Here's what I learned:", "Plot twist:") | delete the bridge; the next sentence is the point |
| Negative parallelism ("it's not about X, it's about Y") | state the positive claim plainly |
| Staccato stack ("No hype. No slides. Just a demo.") | full sentences |
| Sincerity announcement ("Let me be honest:", "Real talk,") | state the fact flat |
| Dense paragraph (several AI-tell words in one paragraph) | rewrite the paragraph, not word by word |
| Model leakage (placeholder names, "as of my last knowledge update", citation tokens) | delete |

**How to fix, not just what to find.** Never swap a flagged word for its synonym. Never add a hedge, a
confession or a number the input did not have: a missing detail is asked of the owner or left out. Fix
only what is flagged; a clean draft gets no edits. Then re-read once for over-correction: a scrub that
leaves every sentence the same length, strips the owner's own reactions, or stacks short fragments has
made the draft worse, so dial it back.

**Structure.** No openers that lead with the owner's title; no stacks of three adjectives or nouns; no
compliment that could go to anyone; no "I believe I would be a great fit" (show the fit with a fact); no
rhetorical questions; no restating their message back to them; at most one number per message, with its
evidence id.

## 10. Message predicates

`outbox.py lint --batch <batch>` and `create` run these on every staged draft to a person (email, LinkedIn
message, connection note; never official letters, posts, comments or profile edits) and refuse the batch
on any issue, which reads `M<n>: ...`. A first touch is kind `intro` or a connection note.

| Id | Script check | Where |
|---|---|---|
| M1 | LinkedIn message under the configured maximum; a first ask (`intro`) under the configured shorter limit | `message_issues` |
| M2 | first email (`intro`) within the configured word range; replies keep the section 2 limit | `message_issues` |
| M4 | a first email or LinkedIn message carries exactly one `?`; a connection note or follow-up at most one | `message_issues` |
| M5 | no link and no attachment on a first touch; the resume is offered, then sent after a yes | `message_issues` |
| M7 | the prospect's first name (from `prospects/<slug>.md`) appears in the text | `message_issues` |
| M9 | the configured number of outbound touches since the last reply (`touches/*.jsonl`, the record's `last_reply_at` or `last_inbound_at`) is the cap; a further intro or follow-up is refused; replies, scheduling and thank-yous are not touches | `message_issues` |
| M13 | not word-for-word the same as an outbox item sent recently | `message_issues` |
| M15 | nothing under NDA: any term in `policy.nda.always_terms` or in the `# nda` block of the gitignored `profile/private-holds.txt`; every channel, public ones included, and the send paths (`gmail_send.py`, `x_api.py`) | `lint_draft` (`nda_issues`) |
| M17 | a first touch (intro or connection note with text) carries exactly one question, about their work | `first_touch_issues` |
| M18 | a first touch never opens with the owner ("I ...", "My ...") | `first_touch_issues` |
| M19 | no pitch formula in a first touch (`style.pitch_phrases`) | `first_touch_issues` |

Judged, not scripted (the messenger writes to them, the reviewer checks them): M3 a first line that fits
only this person; M6 shared ground in one clause; M8 send on a weekday, in their morning (the lane sends
after the yes); M10 a referral ask only at shared history, after a real exchange, with the req ids; M11
nothing about leaving (public text is also caught by the confidential-search check); M12 evidence ids in
the frontmatter and at most one number; M14 the person VERIFIED by `browser_research.py verify`.
