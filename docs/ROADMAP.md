# Roadmap

An honest account of where the project stands. There are no dates. "Next" is work the author has identified from
gaps visible in the repository today; "later" is direction, not commitment. If something here matters to you, say so
in an issue.

## Now: what works

- **The core loop, on Claude Code.** Discovery records, hard gates that quote the failing line, fit reports tied to
  evidence ids, role analysis, tailored resume variants checked against a frozen base, drafted messages, apply packs,
  an append-only application tracker, and an approval gate between every draft and every outward action.
- **30 role agents, 8 lanes and 52 skills**, with tool allowlists, tier limits and denied commands on the role cards.
- **Guards as hooks**: generated files cannot be hand-edited, a role cannot run commands its card forbids, lanes
  cannot overwrite each other's hub files, and one browser has one keyboard.
- **A records MCP server** with 20 read tools and 4 write tools, none of which sends anything.
- **A local dashboard** with nine pages, on the standard library only.
- **Generated adapters with drift checks**, and `scripts/doctor.py` as a one-command health check.
- **A synthetic demo** (Sam Rivera) and a default test suite that passes on it (5,192 tests at the time of writing).
- **A leak guard** with a private deny-list, so the repository stays free of personal records.
- **16 routines, all disabled**, with receipts and a watchdog that flags silence.

It is used by its author for a real search. It is early: layouts and file formats can still change.

## Next: known gaps

These are specific and checkable.

- **A missing role card.** The resume lane's roster names an `ai-screener` role that has no card in `agents/`. Either
  add the card or remove the name.
- **Role-card audit noise.** `doctor` reports one advisory warning: the role-card audit finds 124 items on the demo
  data (bloat, eval, handoff, path, ruling, skill and stale-name findings). Many are references to data files a fresh
  folder does not have yet; some are stale references in the cards themselves. The goal is a clean audit on a clean
  install.
- **Leftover references in script comments.** A few script docstrings still mention paths from the layout this project
  was extracted from (for example `roles/*.md` and a `docs/superpowers` spec) that do not exist here. They should be
  rewritten or removed.
- **Continuous integration.** The repository has issue and pull request templates and a pre-commit leak guard, but no
  workflow that runs the test suite, `sync.py --check` and the leak check on every pull request.
- **Codex end to end.** The Codex agents, hooks and config are generated and drift-checked. They have not been run
  through a full session. Either test them and document the differences or state plainly that they are unsupported.
- **An evals set.** `scripts/kernel/evals.py` is a working harness, and the repository ships an example case file but no eval sets. The
  roles that decide outcomes (fit-scorer, resume-editor, messenger) deserve eval sets of their own, built from
  synthetic cases.
- **Format stability.** Record schemas are validated but not versioned. A schema version field and a migration note
  would let a format change without breaking an existing data folder (`tools/migrate-data.py` and
  `tools/migrate-config-dir.py` exist for earlier moves).

## Later: direction

Ideas, in no order. None is promised.

- A guided first-run skill that builds the evidence registry from a resume and an interview, then runs the first
  gate pass, so the first hour is shorter.
- Windows support, probably through WSL first, with the Keychain and file-mode assumptions made portable.
- More discovery channels that are clearly permitted by their terms, each behind the same gate and pace rules.
- A read-only export of the dashboard for sharing a funnel report without sharing records.
- Support for another engine, if a second engine can be tested end to end rather than only generated for.

## Not planned

- Submitting, sending or posting without a recorded approval.
- Creating accounts, typing passwords or solving CAPTCHAs.
- A hosted service that holds your records.
- Bulk, undifferentiated application volume.
