# Contributing

Issues and pull requests are welcome. This repo is the system only: anything that describes a person (a name, an email,
an employer you targeted, a posting, a message, a score, your writing voice) belongs in your own data folder.

Before a pull request:
1. `python3 scripts/kernel/sync.py` if you changed a card, skill, lane or hook (commit the regenerated files).
2. `python3 scripts/doctor.py` has no failures.
3. `python3 -m pytest scripts/tests -m "not real_records and not workspace" -q` passes on the demo data.
4. `python3 tools/leak_check.py --staged --no-denylist` prints no findings (the pre-commit hook from `./install.sh` does this).

Rules in short: personal values go through `scripts/agency_config.py` with neutral defaults; tests and fixtures use
synthetic people and companies (`example.test`, "Acme Corp"); prose describes method, not incidents; edit sources, not
generated files; one writer per artifact.
