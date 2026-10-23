---
title: Profile holds (demo)
type: profile-holds
status: active
updated: 2026-10-12
owner: role:profile-editor
---

# Profile holds: disputed text that never enters PROFILE.md

The must-not-add list for the resume, messenger and LinkedIn roles. Each row is a figure, identifier or wording that
exists in some source but is in conflict, unmeasured or unconfirmed. `PROFILE.md` refers to a row only by its id and
renders the conservative form. A row leaves this file only when the owner rules on its conflict in `facts.yaml`.
Fictional data for the demo candidate.

A figure too private to print in a tracked file goes in the gitignored `profile/private-holds.txt` beside this file
(one string per line; `profile/private-holds.txt.example` explains the format). The row here describes it in words.

## Conflicts (one row per held item)

| Conflict | Held text (exact) | Where it came from | Why held | What unlocks it |
|---|---|---|---|---|
| C01 | "Principal Wizard" | an old CV draft | a title no employer used | never |
| C02 | "Data Analyst (CV says 'Data Associate' - to confirm)"; "Lab Technician - proposed" | CV drafts | title variants in conflict with the employment letter | an owner ruling |
| C03 | "Cache-Aware Retrieval for Support Agents: A Study of Staleness" | a preprint draft | not yet accepted anywhere | the venue's acceptance |
| C04 | "cutting lookup time by 80%" | an old resume | unmeasured; the runbook says 9 to 4 minutes | a measurement |
| C05 | "Deep Learning"; "Azure"; "AI Lead" | an old resume | skill and title claims without evidence | a project that used them |
| C06 | "United States" | an old resume | location variant; the profile writes the city and state | an owner ruling |
| C07 | "Sam R. Rivera"; "sam.rivera@old-mail.example.test" | an old resume | name and mail variants | never |

## Skills removed

| Term | Why held | What unlocks it |
|---|---|---|
| RLHF/DPO | no corpus narrative | a project that used it |
| LangSmith | unconfirmed | an owner yes |
