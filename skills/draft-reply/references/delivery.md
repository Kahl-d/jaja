# draft-reply: Procedure step 9 (delivery)

The skill links here for the delivery commands (the agent sends after the owner's yes).

9. Delivery, after the owner's yes in the session, by the lane holding the card, through `playbooks/send-after-yes.md`:
   - the yes recorded in the approval, then `python3 scripts/outbox.py can-send --id <O-id>` exits 0;
   - email: the message filed with `python3 scripts/gmail_send.py compose --harvest <file> --message-id <id>
     --body-file <f> [--attach <pdf>] --request` (or `--mode draft` for a Gmail draft), shown to the owner in full with
     `python3 scripts/gmail_send.py show --approval <A-id>`, then `python3 scripts/gmail_send.py execute
     --approval <A-id>`; an outbox item composed this way leaves out `--prospect` so `mark-sent` logs the touch once;
   - LinkedIn: sent in the owner's Chrome at human pace, or handed to the owner with `/linkedin-assist` when the owner
     prefers to send personally;
   - then `python3 scripts/outbox.py mark-sent --id <O-id> [--message-id <id>]`.
   `python3 scripts/gmail_pull.py draft --approval <A-path>` still puts approved email items in Gmail Drafts, threaded,
   when the owner wants to send from Gmail personally.
