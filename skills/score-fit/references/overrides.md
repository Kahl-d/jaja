# score-fit: Overrides and calibration

Detail for the skill's calibration section; the skill links here.

## Overrides and calibration
- Each override from the owner (through the Recruiter): append one JSON line to `evals/fit-overrides.jsonl` in the card's
  shape; a skipped job overridden to apply goes to `queued` and step 9.
- Weekly, or after a batch of new overrides: agreement = decisions without an override / decisions reviewed (above the
  card's minimum sample). Below the card's agreement floor, or repeated overrides one way on one dimension: a proposal in
  `evals/fit-calibration.md` (dimension, current value, a small change per weight within the card's step limit, the
  override rows quoted). Never edit the weights.
