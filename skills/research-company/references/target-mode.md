# research-company: Target mode

Detail for the skill's target mode; the skill links here.

## Target mode (the owner names a role, or a person who can open a door)
1. **Role-first:** the Role Package `research/targets/<SLUG>.md` is role-analyst's (skill `role-package`); this skill
   supplies the dossier and, when asked, its company-signal section. SLUG from
   `python3 scripts/tracker.py new --job <job-id> --dry-run` (`application would create: <slug>`), never minted by hand.
2. **Person-first** (a referral or contact the owner names): a case file `research/targets/case-<person-slug>-<yyyymm>.md`
   (`status: case-ready`): the person (a VERIFIED `research-person` brief), the post or message that started it, the
   connection to the owner, the dossier and the `legal/checks/<company>.md` verdict, the real openings from the company's
   own careers site each with its `score-fit` result, one to three recommended roles with the reach-out plan, and the
   people map. Then one Role Package per role the owner picks, one per research lane at a time (see the charter's research section).
3. Paths and angles are a plan, never message text; nothing outward.
