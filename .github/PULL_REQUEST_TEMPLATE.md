## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `python3 scripts/kernel/sync.py` run if a card, skill, lane or hook changed (regenerated files committed)
- [ ] `python3 scripts/doctor.py` has no failures
- [ ] `python3 -m pytest scripts/tests -m "not real_records and not workspace" -q` passes on the demo data
- [ ] `python3 tools/leak_check.py --staged --no-denylist` prints no findings
- [ ] No personal data in code, tests or fixtures (synthetic people and companies only); personal values go through `agency_config`
- [ ] Docs and CHANGELOG updated where behaviour changed
