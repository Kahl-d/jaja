# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/):
while the major version is 0, a minor release may change file formats or layouts, and the notes will say so.

## [Unreleased]

## [0.1.0] - planned

First public release of the jaja system: the agency, extracted from a private workspace and made generic.

### Added
- 30 role agents, 52 skills, 8 lane definitions and 16 routines (all disabled), with their rubrics and playbooks.
- Claude Code plugin (`/jaja:<skill>`), 9 guard hooks, a records MCP server (24 tools, never sends) and a local dashboard.
- Three-root design: code (this repo), your data folder, a runtime folder; identity, style and policy read through
  `agency_config` with neutral defaults.
- `install.sh`, `scripts/doctor.py`, `tools/init-data.py`, a synthetic demo data folder and `tools/leak_check.py` with a
  pre-commit hook.
- Generated adapters with a drift check (`scripts/kernel/sync.py --check`).
- About 5,200 tests that pass on the bundled demo data without any real data or network.

### Known limits
- Early software used by its author; file formats may still change.
- Only Claude Code is tested; the Codex adapters are generated but not run end to end.
- Optional connectors (Gmail, Calendar, X, browser) need your own credentials; see `docs/SETUP-CONNECTORS.md`.
