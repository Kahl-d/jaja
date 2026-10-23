---
type: approval                   # node type in the record graph
id: A-YYYY-NNNN
action: <send-email | post-linkedin | edit-linkedin-profile | edit-portal-profile | submit-form | publish | purchase | other>
requested_by: <role-slug>
ticket: T-YYYY-NNNN
target: <recipient / URL>
draft_ref: <gmail draft id | file path>
diff: <path to diff or "inline below">
risk: <low | medium | high>
status: pending                  # pending | approved | rejected | expired | withdrawn
content_hash: ""                 # sha256:<hex> of the stripped Exact content section; work/approvals/README.md "Content hash"
decided_at:
decided_via:                     # desktop | phone | chat
created: YYYY-MM-DD
expires: YYYY-MM-DD              # created + 7 days; work/approvals/README.md "Expiry"
---

<!-- Template: templates/approval.md · Contract: schemas/approval.yaml · Rules: work/approvals/README.md.
     The Exact content section IS what will go out. content_hash covers that section only:
     python3 scripts/kernel/approvals.py hash <this file>. The sender executes only status: approved,
     unexpired items whose Exact content still hashes to content_hash; it never composes. -->

# <action> → <target>

## Exact content
<the full draft, verbatim>

## Why this is being sent
<one line, link the ticket>

## What changes if approved
<the diff or the external effect, in plain words>
