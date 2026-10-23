# visa-check: Procedure: company

Detail for the skill's company mode; the skill links here.

## Procedure: company
1. `companies/<company>.md` older than 30 days: refresh through company-researcher (skill `research-company`).
2. JD lines: `grep -n -i -E "sponsor|visa|authori[sz]|citizen|clearance|e-verify|h-1b|h1b|opt|w-2|contract" applications/SLUG/jd.md`.
3. Decide `e_verify`, stance, `cap_exempt`, employment type and remote fit for the I-983; verdict per the card's table.
   A no-sponsorship line is `clear-with-questions`, never `blocked`, unless the policy config makes it a gate: name the
   risk ("<company> says it does not sponsor; the current route covers the start, a future sponsorship is the open
   question") and list the question for the first recruiter contact. `blocked` is only what the card's table lists
   (for example E-Verify no, contract, C2C or 1099 only, refusal of the I-983 or the work authorization, or
   citizenship, green card or clearance required).
4. Write `legal/checks/<company>.md` (frontmatter in the card); questions to `legal/questions.md` (question, attorney or
   DSO, needed-by date). An E-Verify lookup the owner made is recorded with `python3 scripts/everify_note.py record`.
