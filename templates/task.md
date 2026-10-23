---
id: T-YYYY-NNNN                  # immutable; allocated by python3 scripts/tasks.py new
title: <what must be true when this is done>
type: <task | reminder | waiting | recurring | approval>
status: <open | in-progress | waiting | needs-human | blocked | done | cancelled>
owner: <owner | role:<name>>
priority: <P0 | P1 | P2 | P3>
tier:                            # T0-T4 (the autonomy policy); T3 = outward, needs an approval
domain: <domain-slug>
project: "[[project-slug]]"
scheduled: YYYY-MM-DD            # start
due: YYYY-MM-DD                  # soft target; roles may move
hard_deadline:                   # legal/external; roles never move
window_opens:
derived_from: ""                 # provenance for any deadline: anchor + rule [[page]] + last_verified
blocked_by: []
recurrence: ""                   # RRULE; one file per obligation, never per occurrence
recurrence_anchor: scheduled     # scheduled | completion
complete_instances: []
waiting_on: ""                   # type: waiting
thread: ""                       # gmail thread id for follow-ups
follow_up:
follow_up_every:
max_nudges: 3
remind: []                       # ["-P14D", "-P7D", "-P1D"] relative to hard_deadline else due
reminder_refs: {}                # written by the sweep only
evidence: ""                     # required for done (receipt #, confirmation, test run)
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

<!-- Template: templates/task.md · Contract: schemas/task.yaml. Filename: T-YYYY-NNNN-<slug>.md.
     Write tickets with scripts/tasks.py (new / set / done), not by hand.
     Rules: the stricter clock wins; `waiting` rolls follow_up until max_nudges then flips to
     needs-human; done requires evidence; a hard_deadline requires derived_from. -->

# <title>

## Why
<one or two lines; link the project or goal>

## Acceptance check
`<runnable command>` or <observable fact the reviewer can confirm>

## Notes
- YYYY-MM-DD — <dated note>
