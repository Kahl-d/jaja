# job-scout: Gate (the rubrics you apply)

Moved from the job-scout card to keep it lean; the card binds it as if inline.

**Title scope** (ambiguous titles only; the title classes and their lenses are in `targets/roles.md`, with the
generic shape in `role-references/job-scout-title-scope.md`): titles that match one of the user's lenses are in;
titles the user's role targets exclude (analytics, sales, customer success, interns, new-grad programs, contract or
1099 work when the work authorization needs a W-2) are skipped.
**Ghost signals** (`ghost_score` is the baseline): posted over 45 days, or unchanged 60 days: `ghost-risk`; 3 or more
reposts after the first: `ghost-risk`, `skipped` if also over 60 days; "talent pool", "evergreen", "general
application": skipped; a posting with no pay range where state law requires one: `ghost-risk`; apply URL 404 or ATS
record gone: `closed`.
Score under 0.3 keep; 0.3 to 0.6 keep only after a liveness fetch today (`last_seen` today counts); over 0.6 skipped
unless a warm contact exists at the company (`prospects/` by `company_slug`; name the prospect id).

**Sponsorship** (quote the exact line; the gates script already flags; which evidence is a hard skip follows the
user's policy config):

| Evidence | `sponsorship` | Action |
|---|---|---|
| citizenship or green card only, "U.S. person", security clearance required | no | skipped (hard) |
| employer refuses E-Verify, the I-983, or OPT candidates | no | skipped (hard) |
| "no visa sponsorship", "unable to sponsor", "without sponsorship now or in the future" | no | keep, tag `sponsorship-risk`: a risk fit-scorer weighs, never a skip |
| "sponsorship available", "we sponsor visas" | yes | keep |
| silent JD, `h1b_approvals_3y` 10 or more | unknown | keep |
| silent JD or "authorized to work in the US" alone, `h1b_approvals_3y` under 10 | unknown | keep, tag `sponsorship-ask` |
| university, affiliated nonprofit or government research lab | cap_exempt | keep |

**Location (charter):** the location rules are the user's (`policy.location.*`, `targets/roles.md`). The default is
that a location only fails when the user's policy says so; a home region ranks first only as a tie-break. A move can
trigger a work-authorization filing: legal-visa checks it at the offer, not at the find. Never skip on a rule
`targets/roles.md` does not state.
