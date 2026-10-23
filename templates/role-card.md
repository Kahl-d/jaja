---
name: <role-slug>
title: <Human title>
department: <agency | domain:<slug> | project:<slug>>
purpose: <one sentence: what this role owns>
inputs: []
outputs: []
tools_allow: [Read]              # engine tool names; never empty: Claude Code reads an empty list as ALL tools (lint: role-tools-empty; sync refuses)
connectors: []                   # names from the connectors file
tier_max: <T0|T1|T2|T3>
model: <opus|sonnet|haiku>
effort: <low|medium|high>
budget: {tokens_per_day: 0, runs_per_day: 0}
skills: []                       # plugin skill names this role uses
reports_to: <chief-of-staff | owner | role-slug>
status: probation                # probation | active | stale | archived
kpis: []
stop_conditions: []
version: 0.1.0
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

<!-- Template: templates/role-card.md · Contract: schemas/role.yaml.
     Interview (/agency:new-role): which recurring ticket type or distinct tool/auth justifies a
     role rather than a skill? Dependency test: if this role disappeared tomorrow, what breaks?
     Then: inputs, outputs, tool allowlist, tier, model, budget, KPIs, stop conditions,
     reports-to, 10–20 eval cases from real tickets. Body ≤ 80 lines. -->

# <Human title>

## Charter
What this role owns and does not own, in a short paragraph.

## Inputs
- <ticket type / file / feed>

## Outputs
- <artifact, where it is written>

## How I work
1. Recall first (the profile, the records, the ticket).
2. Do the work inside the tool allowlist.
3. Capture after; report states, not fractions.

## Boundaries
Autonomy tier `<T?>` per the autonomy policy: <what is automatic, what needs an approval file, what is forbidden>.

## Stop conditions
- <condition that ends the run and hands back to the chief of staff or the owner>

## KPIs
- <measurable, reviewed weekly>
