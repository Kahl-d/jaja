# scout-jobs: On-demand targeted search

Detail for the skill's on-demand section; the skill links here.

## On-demand targeted search (the owner or the Recruiter names a company or a title)
Writes nothing.
1. On the watchlist: `python3 scripts/fetch_ats.py --company <slug> --dry-run` (`--all-titles` for every opening).
2. Not on it: find the ATS from its own careers page (the apply URL names vendor and token), then
   `python3 scripts/fetch_ats.py --board <vendor>:<token>:"<Company>" --dry-run`.
3. A title across the watchlist: `python3 scripts/fetch_ats.py --dry-run`, filtered for the title; a site with no API
   (LinkedIn, Wellfound, Glassdoor) goes to `browser-job-search` with the owner.
4. Reply with the openings (title, location, posted date, URL) and the command's last line. Writing any to `jobs/` is
   the Recruiter's or the owner's call; then the procedure above applies.
