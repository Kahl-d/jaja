# Fit scorer: weights, anchors and caps (the A to H rubric)

Binding for every fit.md. Card: agents/fit-scorer.md, Gate section and Procedure steps 3 and 4. Kept here so the
card stays under its size cap.

**Weights and anchors:**

| Dim | Weight | 5 | 3 | 1 |
|---|--:|---|---|---|
| A role match | 0.20 | title and duties match one lens in `targets/roles.md` | adjacent | not AI/ML |
| B must-have evidence | 0.25 | coverage 0.90+ | 0.60 to 0.74 | under 0.40 |
| C seniority | 0.10 | within your engineer months | over: STRETCH via a person | cold: out; the reject threshold is the gate |
| D comp and wage level | 0.10 | Level III or IV, band at or above market p50 | unknown | below Level I or under p25 |
| E company quality | 0.10 | public or top lab, multi-year runway | Series A or B in 18 months | layoffs over 20%, freeze |
| F narrative angle | 0.10 | a true, specific story maps to their core problem | plausible, transferable | no honest story |
| G legitimacy | 0.05 | posted 7 days or less, live, req id, pay range | 31 to 45 days or reposted once | over 60 days, 3+ reposts |
| H work authorization | 0.10 | explicit sponsorship or cap-exempt | silent JD with 1 to 9 approvals, or a no-sponsorship flag at a startup | a no-sponsorship flag at a big company |

B coverage = (covered + 0.5 × adjacent) / must-haves: 0.90+ = 5, 0.75 to 0.89 = 4, 0.60 to 0.74 = 3, 0.40 to 0.59 = 2,
under 0.40 = 1. D from `wage_level_est`: III or IV = 5 only with a market p50 checked, else 4 (gap under Risks); II = 4,
unknown = 3, I = 2. H: silent JD with 10+ approvals = 4; silent, zero history, under 50 people = 2. C: the `months:`
line of `python3 scripts/experience_months.py` (the one count, by the posting's wording), pasted into fit.md; never hand
arithmetic; `experience_months.py check applications/<slug>/fit.md` exits 0.
**G's date** (hiring posts show who is hiring now): the latest of the first posting date, a hiring post within 7 days
naming this role or its family (record `hiring_signal: {url, date, by}`, quote it) and a relist with a new req id or
URL (`relist`); never updated_at, a board-wide republish or an alert copy's date.
**Caps:** B 2 or less caps the total at 3.4; G 2 or less caps at 3.0; an unresolved `review` gate caps the verdict at
`maybe`. Freshness never changes the score; it orders the queue.
**Worked shape:** A 5, B 4, C 4, D 4, E 3, F 5, G 5, H 4 gives 1.00 + 1.00 + 0.40 + 0.40 + 0.30 + 0.50 + 0.25 + 0.40
= 4.25, rounded 4.3, `apply`. Bad report line: "Strong fit, great company, apply." (no ids, no arithmetic).
