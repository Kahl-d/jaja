# Fit scorer: reading the years ask and the reader's screen

Why: a role whose posted bar the record cannot meet, or whose application form asks first for something the record
does not hold, is rejected fast. Read the ask and the form before scoring. Card: agents/fit-scorer.md, Procedure
steps 2 to 4.

## Row C: the years ask against the right count

1. Read the ask with `python3 -c "import sys; sys.path.insert(0,'scripts'); import fit_gates; print(fit_gates.required_years(open('applications/SLUG/jd.md').read()))"`.
   It reads digits and spelled numbers ("Two years", "three+ years").
2. Count with the one function: `python3 scripts/experience_months.py` prints
   `months: industry <a> · exclusive of internships <b> · engineer titles <c> (experience_months.py <date>)`, the
   count the apply pack answers forms with; never month arithmetic by hand. Pick the number the posting's wording
   asks for (never a total of years on any page):
   - "full-time industry" or "professional": industry;
   - "exclusive of internships" or "post-graduate": exclusive of internships;
   - "as a software engineer", "as an ML engineer", "engineering experience" or an engineer title: exclusive of
     internships (this is the count `fit_gates.ENGINEER_MONTHS` reads); engineer titles is the count only where the
     owner's own answer asks for titles.
3. Paste the `months:` line into fit.md, then row C: `C: asks <N> years <the posting's words>; count <M> months
   (<which>)`. `python3 scripts/experience_months.py check applications/SLUG/fit.md` exits 0.
   Score C by the card's anchors on that comparison: an ask within the count is in; above it there is no cold
   application: a STRETCH only through a person inside (a warm path in the CRM), else out of the fresh list; the
   reject threshold (`policy.fit_gates.reject_years`) is the gate.

## The reader's screen (a fit.md section, before the score)

`## Reader's screen`, one line each:
- the form's required questions and their stated preference order (from the application form when it is visible,
  else the JD's "we prefer" or "bonus" order), quoted;
- the team's advertised bar (team or careers page: "Olympiad medalists", "top-tier publications"), quoted with URL;
- the employer's H-1B approval count (`python3 scripts/h1b_lookup.py <company>`), or "unknown";
- the posted band against the owner's expectation (the user's policy config).

**The cap:** when the form's first preference (its first required question, or its first "we prefer") is absent from
the record, the score is capped at 3.9 (verdict `maybe`), with the line `cap: first preference "<words>" not in the
record`. The owner can lift it with a fact the corpus-keeper records.

**Held, never dropped:** a form that asks a test score or a contest rank (an Olympiad or competition bar) is held
under Needs you with `hold: test score or contest rank, owner's fact`; the owner may have one to give. The cap
stands until the corpus-keeper records it.
