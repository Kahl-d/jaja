# Releasing

How a release of jaja is cut. Nothing here is automated; a maintainer runs the steps.

## v0.1.0 plan

1. Everything on `main`; `git status` is clean.
2. `python3 scripts/kernel/sync.py --check`, `python3 scripts/doctor.py` (0 failures), the default test command on the demo data (0 failed), and `python3 tools/leak_check.py` (0 findings, with your private deny-list) all pass.
3. Bump the version in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`; run `python3 scripts/kernel/sync.py --check` (it verifies the two manifests agree).
4. In `CHANGELOG.md` rename `[0.1.0] - planned` to the release date and open a fresh `[Unreleased]`.
5. Commit ("Release 0.1.0"), then tag: `git tag -a v0.1.0 -m "jaja 0.1.0"` and push the tag.
6. Create the GitHub release from the tag; paste the changelog section and attach nothing else (the repo is the artifact).
7. Set the repository topics and the social preview image (`docs/assets/social-preview.png`, 1280x640) in the repository settings.

## Versioning

0.x: a minor bump may change formats; say so under "Changed" with the migration step (see `tools/migrate-data.py` for the pattern).
