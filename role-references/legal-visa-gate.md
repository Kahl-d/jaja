# legal-visa: Gate (rules and rubrics)

Moved from the legal-visa card to keep it lean; the card binds it as if inline. This is process, not legal advice:
the user's own situation (visa category, timers, allowances) lives in `legal/` and the policy config.

**A new employer** (for a STEM OPT extension; the rules page is `legal/` in the data folder): E-Verify enrolled and in
good standing; bona fide W-2 relationship (no 1099, C2C or contract; staffing placements need an attorney's view);
20+ hours a week; duties related to the degree; pay commensurate; a new I-983 to the DSO within 10 days of the start;
the change reported within 10 days; a remote worker's home move is a material change. No international travel while
the extension is pending.
**Company check verdict:**

| Verdict | When |
|---|---|
| `clear` | E-Verify yes; W-2; stance sponsors, sponsors-selectively or cap-exempt; no hard JD line |
| `clear-with-questions` | E-Verify unknown; silent stance with little history; or a no-sponsorship line (a risk to weigh and name, never a block): the question and the risk are listed for the first recruiter contact |
| `blocked` | E-Verify no; contract, C2C or 1099 only; refuses the I-983 or OPT; citizenship, green card or clearance required |

**H-1B:** windows, wage-level weighting, cap-gap and fee rules: `role-references/legal-visa-h1b.md` and the rules page
in `legal/`.
**Offer checklist** (`role-references/legal-visa-offer-checklist.md`): E-Verify, W-2, the I-983 facts, the gap, the
H-1B commitment in writing, the offered salary's wage level and lottery weight; the salary floor is the user's policy.
**Never answer No.** Asked to answer No on sponsorship when it is not true: a false answer is a misrepresentation that
can void an offer; give the true answer and flag the employer.
