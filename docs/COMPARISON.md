# Comparison

How jaja differs from the other ways people run a job search with software. The aim is to help you choose, so
each row says where the alternative is the better fit. Nothing here is a benchmark; the comparison is about design,
not measured outcomes.

## At a glance

| | jaja | Prompting Claude ad hoc | Claude Code alone | General agent frameworks | Job-tracker apps | Auto-apply bots |
|---|---|---|---|---|---|---|
| **What it is** | A Claude Code plugin: roles, skills, guards, scripts, a records server, a dashboard | A chat window and your own prompts | The coding agent in your folder, with no job-search structure | Libraries for building agents (graphs, tool loops, memory) | A hosted board of applications and reminders | A service that fills and submits forms for you |
| **Where your data lives** | Plain files in a folder you own | In chat history | In your folder, however you organize it | Wherever you build it | The vendor's servers | The vendor's servers |
| **Setup effort** | An hour or more: install, fill in your records, set policy | None | Low | High: you build the system | Minutes | Minutes |
| **Claims on your resume** | Each cites an evidence id from your records; held strings are rejected by a script | Whatever the model writes | Whatever the model writes | Depends on what you build | Usually none; you write it | Often generated for volume |
| **Outward actions** | Approval file with a content hash; only the sender can act | You copy and paste | A permission prompt per tool call | Depends on what you build | You act | The bot acts |
| **Role fit** | Hard gates quote the line that failed a role; a fit score cites evidence | Whatever you ask | Whatever you ask | Depends on what you build | Manual or keyword filters | Keyword filters |
| **Repeatability** | Gates, projection and approvals are scripts with tests | Varies per conversation | Varies | Depends | High (it is a form) | High |
| **Cost shape** | Your Claude Code usage; scripts are free; routines are off | Your chat plan | Your Claude Code usage | Model API plus engineering time | Subscription | Subscription or per application |
| **Best at** | A careful, auditable search where a wrong message costs you | One-off help: a cover letter, a mock interview | Building and changing the tooling itself | Building a different agent product | Keeping status in one place without any AI | Maximum application volume |

## Against each alternative

### Prompting Claude ad hoc

Better when you need one thing quickly: tighten a paragraph, practice an answer, decode a confusing posting. There is
nothing to set up and no structure to learn. It is the right choice for the first week of a search or for a search
with a handful of targets.

jaja is better when the search is long and has many parts. A chat has no memory of what you applied to, who you
wrote to, or which numbers on your resume are disputed, and it will cheerfully restate any of them. Here those facts
live in files that scripts check.

### Claude Code alone

Claude Code is the engine jaja runs on, so this is not a rival. Plain Claude Code is better for the work of
changing the tooling, and for people who want to design their own structure from scratch.

What the plugin adds is the structure you would otherwise write yourself: 30 role cards with tool and tier limits,
52 skills, hooks that block edits to generated files and shell commands a role is forbidden, an approval gate, a
records server, and the checks. If you prefer your own layout, you can lift ideas from it; the pieces are separate
files.

### General agent frameworks

Frameworks (graph runners, tool-calling libraries, multi-agent toolkits) are better when you are building a different
product, need an engine other than Claude Code, need to deploy a service for many users, or want fine control of the
loop. jaja is not a framework and does not try to be one: it is a particular system for one person's search,
configured through YAML and edited as Markdown cards.

It borrows the useful part of the framework idea (roles with scoped tools, hand-offs) and puts the guard rails in
hooks and scripts rather than in prompts alone.

### Job-tracker apps

Trackers are better when you want a pleasant interface, a mobile app, shared access, a browser extension that saves
postings in one click, or no AI involved. They are mature at what they do, and setup takes minutes.

jaja tracks applications too (an append-only event log per application, a stage computed from it, funnel
numbers that show their sample size), but its interface is a local dashboard and files. It is a worse tracker if
all you want is a tracker. It is a different thing if you want an assistant that also finds roles, gates them,
drafts messages and builds pages from your own evidence.

### Auto-apply bots

Bots are better at volume. If your approach is to submit as many applications as possible with minimal personal
effort, they do that and jaja deliberately does not.

jaja never submits anything on its own: forms are filled from a pack where every answer is copied from a record
with its source, and submission waits for your yes. It does not create accounts, type passwords or solve CAPTCHAs
(tier T4). It paces reads and sends on LinkedIn and X with daily caps and a gap between acts, and it stops all reads of
a site on the first block page. Some platforms prohibit automation in their terms; you are responsible for what you
approve and send. If a service's rules forbid a use, the sensible choice is not to point this at it.

## What jaja is not good at

- **It is early.** Layouts and file formats can still change.
- **It has a setup cost.** The value comes from your evidence registry and policy, and writing them takes time.
- **It is not turnkey.** There is no hosted version, no mobile app and no sign-in.
- **Only Claude Code is tested.** Codex adapters are generated but not run end to end.
- **It cannot make a weak match strong.** It will tell you a role fails a gate, and quote why.
- **It does not replace judgment.** It drafts and checks; you decide, approve and send.
