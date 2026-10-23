---
title: Model and effort policy
doc_type: policy
owner: the Lead Recruiter reads it; the orchestrator and every lane that spawns an agent follow it
updated: YYYY-MM-DD
---

# Model and effort policy

The agency decides which model and which effort level each kind of agent and task gets, so cost goes where the
extra reading and calls pay for themselves. Default stance: cheap models label, route, scout, gather and fill; a
stronger model at medium effort writes the owner's words and judges pages; the strongest settings are kept for the
decisive few (offers, a dream page's final pass); the top tier is used only when the owner asks for strategy or
architecture. Cost is context times calls, so a cheap row is cheap because a script or a later gate catches its
errors. Change a row on a measurement or on the owner's word, and say which.

**How it binds.** Every role card's `model:` and `effort:` equal its kind's row below; `python3 scripts/model_policy.py
check` fails on a card that differs without an exception; `python3 scripts/model_policy.py apply` sets the cards from
the policy. Whoever spawns an agent passes the tier's model: `python3 scripts/model_policy.py pick <role> --tier
mass|focused|dream` prints `<model> <effort>` or `none` (do not spawn it at that tier; scripts or a signed page cover
it). The Agent tool takes a `model:` per spawn; effort is the card's. A row moves on a measurement (the `Measured`
table below, produced by the eval runner with `--model` and `--effort`) or on the owner's word, and the change names it.

**Escalation, not default.** A failed or borderline gate escalates one step (Sonnet, then Opus medium, then Opus high
via the dream tier), once, then the item goes to the owner. Keep one model per session (a switch is a cache miss); a
different model runs as a subagent.

## Kinds

```yaml
kinds:
  classify:   {model: haiku,  effort: low,    why: "labels from rules plus a short read"}
  bookkeep:   {model: sonnet, effort: low,    why: "scripts count and render; the model reads results and routes; errors are cheap to catch"}
  scout:      {model: sonnet, effort: low,    why: "script sweeps first; the model judges only the edge cases the script flags"}
  gather:     {model: sonnet, effort: medium, why: "search and read many sources into structured, cited records; breadth over depth"}
  fill:       {model: sonnet, effort: medium, why: "portal forms from records in a live browser; a wrong submit is costly, so not low"}
  coordinate: {model: sonnet, effort: medium, why: "a lane that claims, spawns and hands over; the words come from the agents it spawns"}
  analyze:    {model: sonnet, effort: medium, why: "role lens, coverage matrix and reader model from the JD and a lens digest; Opus at dream tier"}
  write:      {model: opus,   effort: medium, why: "outward words in the owner's voice, every line true to the record; medium ~= high quality at far fewer tokens"}
  judge:      {model: opus,   effort: medium, why: "clean-context gates whose verdict stops or ships a page; borderline verdicts escalate"}
  lead:       {model: opus,   effort: medium, why: "the Recruiter: decides and reviews, but most turns route and read cards"}
  decisive:   {model: opus,   effort: high,   why: "rare and decisive: offers and negotiation"}
  strategy:   {model: fable,  effort: high,   why: "on the owner's ask only: architecture, a new lens, the weekly strategy read"}
  implement:  {model: opus,   effort: high,   why: "BUILD; max only when the owner asks for it on a named task"}
```

## Roles

```yaml
roles:
  engineer: implement
  researcher: gather
  writer: write
  reviewer: judge
  sender: classify
  ops-watchdog: scout
  mail-triage: classify
  tracker-ops: bookkeep
  prospect-triage: bookkeep
  inbox-harvester: bookkeep
  job-scout: scout
  platform-scout: scout
  fit-scorer: gather
  company-researcher: gather
  prospector: gather
  legal-visa: gather
  apply-assist: fill
  role-analyst: analyze
  base-comparer: judge
  resume-editor: judge
  technical-ai-reviewer: judge
  technical-recruiter-reviewer: judge
  hiring-manager-persona: judge
  ai-screener: judge
  resume-tailor: write
  messenger: write
  linkedin-editor: write
  prep-coach: write
  corpus-keeper: write
  profile-editor: write
  negotiator: decisive
lanes:
  recruiter: lead
  research: coordinate
  resume: coordinate
  focused: write
  prep: write
  posts: gather
  apply: fill
  hunt: bookkeep
```

## Tiers (which model a spawner passes, or `none`: do not spawn at that tier)

The pipeline by tier is described in the `resume-for` skill. Mass is cheap because it reuses a
signed family page and script gates, not because a weak model writes it.

```yaml
tiers:
  mass:
    role-analyst: none            # the signed base page and scripts/mass_page.py stand in
    resume-tailor: none           # the signed base; scripts/mass_page.py aligns the Skills row
    resume-editor: none           # every line was signed on the family page; script gates run
    base-comparer: none
    technical-recruiter-reviewer: none
    technical-ai-reviewer: none
    hiring-manager-persona: none  # the charter's readers on a mass page: ats_check (ATS), page-1.png looked at once
    ai-screener: none
    company-researcher: none      # the cached dossier, refreshed at most every 30 days
    legal-visa: none              # cached per company
    messenger: sonnet             # from the answer cache and signed lines
  focused:
    role-analyst: sonnet
    resume-tailor: opus
    base-comparer: opus           # blind (blind_pairs.py); kept separate from the editor so the judge never sees the plan
    resume-editor: opus           # only when a line changed (resume-for)
    hiring-manager-persona: sonnet  # the charter's human reader: a busy reader's one pass
    ai-screener: sonnet           # the AI reader: ranks the page against synthetic peers
    technical-recruiter-reviewer: opus
    technical-ai-reviewer: none   # dream only
  dream:
    role-analyst: opus
    resume-tailor: opus
    resume-editor: opus
    base-comparer: opus
    technical-recruiter-reviewer: opus
    technical-ai-reviewer: opus
    hiring-manager-persona: opus
    ai-screener: opus
```

## Exceptions (each with its reason)

```yaml
exceptions:
  hiring-manager-persona: {effort: medium, why: "one pass, read as a busy reader would; more effort reads more carefully than the person it plays"}
```

## Measured

Each row is a run of the role's eval set on two settings (the eval runner with `--model` and `--effort`), from the
same cases on the same day: pass rate, wall time and tokens. A row changes a kind or an exception only when it is here.

| date | role | setting A | setting B | pass A | pass B | time A | time B | decision |
|---|---|---|---|---|---|---|---|---|
| YYYY-MM-DD | <role, what was run, n cases> | <model/effort> | <model/effort> | <k/n> | <k/n> | <s> | <s> | <kept or changed, and why> |
