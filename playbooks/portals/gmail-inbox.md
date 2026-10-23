---
title: Gmail inbox (inbox lane playbook)
channel: gmail
owner: INBOX
---

# Gmail inbox: how the inbox lane reads the mail

## Query set (one pass, since the last pull)
- `python3 scripts/gmail_auth.py status` first: read token, `reauth_due`. A failed read goes under "Needs you" at once.
- Last pull = `state/harvest/gmail.state.json` (`last_internal_date`, `last_history_id`) and the newest `state/harvest/gmail-<date>.jsonl` mtime. Notes and dashboards drift; measure it.
- Pull: `python3 scripts/gmail_pull.py harvest --today <date>` (incremental by history id; read token only).
- Classify: `python3 scripts/mail_classify.py classify --in state/harvest/gmail-<date>.jsonl --today <date>`.
- Parse alerts read-only: `job_alerts.parse_alert(row, platforms, now, title_filter)` returns `.records` per alert; never `job_alerts.py` without `--dry-run` (it writes jobs/, which the scout role owns).
- Tracker events (confirmations, rejections): `mail_intake.py --since <date> --no-pull` with `_alerts` and `_people` patched to no-ops (they write job and prospect records); check with `--dry-run --json` first.
- Full message, read-only: `gmail_auth.build_service('read')` then `users().messages().get(format='full')`. The harvest holds only text/plain; HTML-only mail (scheduling tools, ATS) arrives as "No Text Available", and calendar mail keeps its `invite.ics` as an attachment (DTSTART, TZID in it).
- Searches that answer "did the owner agree?": `q=<sender domain> OR "<name>"`, then `to:`/`from:` both directions; absence in the mailbox means the agreement was spoken.

## Method notes (each changes what to do next time)
1. The harvest body of HTML-only mail is empty: open the full message before saying "no text" or "no time". Every invite, screening call and recruiter mail with a very short body gets a full fetch.
2. Interview invites carry the time only in invite.ics. Map the TZID to a real zone (some tools use Windows zone names). Read the ICS and report DTSTART/DTEND/TZID/link/passcode verbatim, and say how many hours away it is (`date`).
3. The classifier can label LinkedIn "Your application to X" mail as newsletter, but the mail type is `application_rejected`. Read the mail's `trk=eml-email_jobs_application_rejected` marker; those are rejections.
4. LinkedIn digests ("X just messaged you") carry no message text. Flag them to the focused lane; reading needs LinkedIn.
5. Recruiter staffing mail is common: flag `staffing?` against the employer gates, tag the focused lane when a person is in conversation; the owner sends every reply.
6. LinkedIn alert cards have no posting date: age against the freshness window is unverifiable; record `posted_at: null` with the mail date as evidence. Some boards' cards do carry a date.
7. A sender may first write to a different address of the owner's; threads can sit on an address the search does not cover. Search by sender domain, not only the main address.
8. Run the pass twice a day (mid-morning and mid-afternoon), people mail first.
9. The owner often answers recruiters quickly: before flagging "needs reply", check the thread for the owner's own latest message (`from` = the owner's address in the harvest) so the card shows the true last turn.
10. Security notices about the owner's own connector grants or new devices are not threats and not finds.
11. Job-digest mail with tracked redirect links only: roles are readable from the text, the employer URL is not.

## Connector query set (Gmail connector, read only)
When the Gmail connector (`mcp__claude_ai_Gmail__search_threads`, `get_thread`, `get_message`) is loaded in the session, use it instead of the script pass; never send, reply, forward, trash or mark spam. Add your own connector ids per `docs/SETUP-CONNECTORS.md`.
- New since last pull: `after:<unix of last internal_date> -in:chats -in:spam -in:trash`
- People mail: `-category:promotions -from:(<alert and digest sender domains>)` then `get_thread` for each
- Invites: `filename:ics OR subject:(interview OR "screening call" OR "intro call" OR assessment)`
- Rejections/confirmations: `subject:("application" OR "thank you for your interest" OR "moving forward")`
- Recruiter threads: `from:<domain>` both directions, then `to:<domain>` to see the owner's side
