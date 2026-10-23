# FAQ

## Is my data private?

Your records live in a folder of your own (`<data>`, for example `~/jaja-data`), outside this repository, and
the repository contains no personal data. The scripts are local; the dashboard binds to `127.0.0.1` only. What
leaves your machine is what Claude Code sends to the model provider while you work (the text of the files an agent
reads), plus any connector you turn on yourself, and those are off by default. Read your Claude plan's data terms
before you put sensitive records in a session.

A pre-commit leak guard (`tools/leak_check.py`) stops a commit to this repository if it would add a name, email,
employer, home path or token from your records. It uses a private deny-list built from your own data
(`tools/build_denylist.py`) that is kept outside the repository. It is a safety net, not a guarantee.

## What does it cost, in tokens and money?

There is no price for the plugin. The cost is your Claude Code usage, which depends on how much you run. Some
things that shape it, all visible in the files:

- Each role card sets a `model` and `effort`. Across the 30 cards, 15 use opus, 13 use sonnet and 2 use haiku, and
  each has a `budget` of tokens and runs per day.
- Scripts do the counting and gating, so they cost nothing in tokens. The `mass` depth makes no model call per row.
- Routines are all off, so nothing runs in the background until you enable it.
- `python3 scripts/kernel/token_ledger.py` reports measured token use per session, subagent type and model from your
  Claude Code transcripts, and `python3 scripts/kernel/context_budget.py` measures what a session loads before it does
  any work. There is no benchmark in this repository for what a search costs; measure your own.

## Which engine does it run on?

Claude Code. That is the only engine tested end to end. The canonical files (role cards, skills, hooks, MCP config)
are engine-neutral, and `sync.py` generates Codex agent files, hooks and config from them, with drift checks. Those
outputs are not run end to end, so treat Codex support as untested.

## Can it apply for me?

It can prepare and fill; it does not submit on its own. For a role you choose, `apply-assist` builds an apply pack
where every answer is copied from one of your records with its source, lints it, and can fill an employer-site form
from the pack in a browser tab. Submitting waits for your yes, for exactly the rows you name. It never creates
accounts, types passwords or solves CAPTCHAs. Some one-click flows (for example LinkedIn Easy Apply) are left to you
by design.

## What if a platform bans bots?

Check the platform's terms before you use any automation against it, and do not use it where the terms forbid it. You
are responsible for what you approve and send. The project's defaults lean conservative: `platform-scout` records
each platform's allowed channel (API, alert mail, your own browser with you present, or manual) in
`targets/platforms.yaml` and does not scrape; reads and sends on LinkedIn and X have daily caps and a minimum gap
(`scripts/pace.py`, defaults in `config/defaults.d/09-pace.yaml`); browser reads of a careers site are paced, and the
first block page stops all reads of that host (`scripts/browser_pace.py`). These reduce risk; they do not remove it.

## How are claims kept true?

Every claim about you must cite an evidence id from `profile/evidence.yaml`. `check_evidence.py` rejects ids that do
not exist, and `check_holds.py` rejects strings you have marked as disputed or unconfirmed. A tailored resume ships
only if a blind comparison against your base says it wins, and a reviewer in a clean context signs a verdict. The
checks confirm that evidence exists and that held text is absent; they do not prove that a source says what a sentence
claims, which is why a reviewer reads each page and you approve it.

## How do I add an agent?

Run `/jaja:new-role` in a session. It interviews you, creates the role card from `templates/role-card.md`,
adds an evals folder, and runs `sync.py`. By hand: write `agents/<name>.md` with at least `tools_allow` and
`tier_max`, run `python3 scripts/kernel/sync.py`, then `python3 scripts/kernel/lint.py`. A card with no `tools_allow`
is refused. See [guides/customizing-agents-and-skills.md](guides/customizing-agents-and-skills.md).

## Does it work without Gmail?

Yes. Gmail, Calendar, Firecrawl, X and a logged-in browser are all optional and off until you set them up
([SETUP-CONNECTORS.md](SETUP-CONNECTORS.md)). Without Gmail you lose the mail triage that records replies and events
automatically; you log events with `scripts/tracker.py event` or the `tracker_event` MCP tool instead, and approved
drafts are copied out by hand.

## Does it work on Windows?

It has not been tried. `install.sh` is a bash script (macOS bash 3.2 compatible), the documented requirements are
macOS or Linux with Python 3.11 or newer, and some helpers use the macOS Keychain or POSIX file modes. Windows through
WSL is the likely route, but it is untested.

## How long until it is useful?

The demo works in minutes. Your own search is useful once your facts and evidence are in place, which is the
longest step; `/jaja:add-experience` interviews you and writes the records. See
[guides/your-first-search.md](guides/your-first-search.md).

## Will it write in my voice?

It reads `config/style.yaml` and `profile/VOICE.md`. The repository ships the mechanism (mechanical checks in
`scripts/outbox.py`, `letters_check.py` and `send_preflight.py`) and no voice of its own. An empty style file means no
style checks. Mechanical rules (banned phrases, dash rules, length limits) are enforced by scripts; "sounds like you"
is a judgment you make when you read the draft.

## What happens if an agent misbehaves?

The layers, from the outside in: a hook refuses edits to generated files and shell commands a role is forbidden;
tier limits cap what a role may do; nothing outward leaves without a verified approval file; receipts and event logs
are append-only, so you can see what happened; and `python3 scripts/doctor.py` checks the install. Hooks fail open
when a session's identity is unknown, so they are guard rails for mistakes rather than a security boundary against a
hostile model.

## Can I run it for someone else, or for a team?

It is built for one person's search, with one data folder. You can run separate data folders for separate people by
pointing `JAJA_DATA` at each. There is no multi-user mode, sign-in or shared server.

## Is it affiliated with Anthropic or any job platform?

No.
