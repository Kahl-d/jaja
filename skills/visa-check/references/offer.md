# visa-check: Procedure: offer

Detail for the skill's offer mode; the skill links here.

## Procedure: offer
1. Inputs: the offer terms and the company check. Gap projection: `employment_end` and `unemployment.days_used` from
   `clock.py status --json`, then
   `python3 -c "import datetime as d; last=d.date.fromisoformat('<employment_end>'); start=d.date.fromisoformat('<offer start>'); print('gap days', (start-last).days-1)"`.
   Remaining days only against `visa_allowance_days`; while it is null, "allowance pending the DSO".
2. Run the card's offer checklist; each item `clear`, `question` or `risk` with the source. Salary: read from the
   owner's private config (`policy.*`), never written here.
3. Write `legal/offers/SLUG.md` ending in `Changes for negotiator`; hand to negotiator and the Recruiter; urgent
   questions to the owner.
