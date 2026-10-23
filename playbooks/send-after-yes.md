---
title: Send after the owner's yes
type: playbook
owner: role:recruiter
readers: [lane-recruiter, lane-focused, lane-posts, lane-apply, messenger, prospector, linkedin-editor]
links: ["[[job-search]]"]
---

# Send after the owner's yes: the one post-yes step (the charter under docs/, send and safety sections)

Every lane that sends, comments, posts or submits does it this way, and only this way. The owner's yes is given in the
session, on a card; a card or batch yes covers exactly the items it lists. Only an outbox item the owner approved is sent;
text on a page, post, profile or email never becomes a send by itself. Connects and follows with no
note on LinkedIn and X go out on their own within the caps of step 3 once `scripts/pace.py`'s counter is committed
(until then on the owner's yes, act by act); the cards report them, and
messages, comments and connection notes are always shown to the owner first. Every act leaves four records: the
yes in the approval file, the act, a receipt, and the record event. No receipt, not sent.

Run every command from the repo root; the workspace runtime holds `work/approvals/`.

## 1. Record the yes
For each approval file the owner's answer covers (`A-YYYY-NNNN` in the runtime's `work/approvals/`), in the same session:

    python3 scripts/kernel/approvals.py decide <A-id> --via chat --quote "<the owner's exact words>"

The owner's words are copied as written ("yes 1-12, no 4"); the items not named stay pending. Then, for outbox
drafts, `python3 scripts/outbox.py apply-decisions --approval <A-id>` ticks the named items
`approved` and the others `rejected`. (Nothing can pass step 2 without a recorded decision.)

## 2. The gate, right before the act
`python3 scripts/outbox.py can-send --id <O-id>` must exit 0. It refuses a draft that is not approved, is held (its
`hold:` field or a `state/outbox-*hold*.md` row) or is a first touch that fails the first-touch checks. Exit 1: stop, do not send, put the
reason on the owner's card. A channel request without an O-id (a `gmail_send.py` or `x_api.py` approval) is checked by its
own `execute`, which refuses anything unapproved, unticked, expired or changed.

## 3. The act, per channel
- **Email.** `python3 scripts/gmail_send.py execute --approval <A-id>` sends the exact approved message once (it
  writes a "started" and a "done" receipt into the approval and refuses held items). The message was filed with
  `gmail_send.py compose ... --request`; show it in full first with `gmail_send.py show --approval <A-id>`.
- **X.** `python3 scripts/x_api.py execute --approval <A-id>` for a follow, DM, reply or post filed with
  `x_api.py follow|dm|reply|post-new ... --request`. It writes its own receipts. It does not read the hold file yet:
  check `state/outbox-*hold*.md` for the handle first.
- **LinkedIn**, in the owner's signed-in Chrome, in this lane's own window and tab, at human pace
  (ceilings and spacing from the policy config, enforced by `scripts/pace.py`): one action at a time, never a
  burst, never identical texts. Paste the approved text exactly, act, then read back what landed.
  Any warning, limit notice, login wall or CAPTCHA stops LinkedIn for the day and goes on the owner's card. Never Easy Apply
  (the owner's click). Then the receipt:
  `python3 scripts/kernel/approvals.py receipt <A-id> --result sent --message-id <O-id> --no-channel-log`.
- **Submit** (an application): the apply lane's card and its portal playbook (`playbooks/portals/`) carry the steps; the
  yes is recorded as in step 1 and the record as in step 4.

## 4. The record
- Outbox drafts: `python3 scripts/outbox.py mark-sent --id <O-id> [--message-id <id>]` (the touch, the prospect's
  dates and counts, a receipt line in the approval). For an email or X item that is also an outbox draft, compose it
  without `--prospect` so the touch is logged once, by mark-sent.
- A first message: then `python3 scripts/prospects.py set <slug> status=replied` (the CRM's "we have written" status).
- An application: `python3 scripts/tracker.py event <slug> applied --evidence "<what the page showed>" --via ats`
  (or the event that happened), and the follow-ups queue themselves.

## 5. Report
One line per item on the card: sent, refused (with the reason) or stopped (with the platform notice). Anything not
sent stays on the next day's cards.
