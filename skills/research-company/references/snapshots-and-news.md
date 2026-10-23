# research-company: Procedure steps 6 and 7 (evidence snapshots, news signals)

Detail for the skill's steps 6 and 7; the skill links here.

6. **Evidence snapshots.** A page a claim rests on:
   `python3 scripts/web_snapshot.py scrape <url> --out research/targets/evidence/<company>-<what>-<date>.md`
   (`--wait-for 3000` for JavaScript; `map <url>` lists a site's pages). The script checks robots.txt, refuses job
   platforms and LinkedIn and stops at the credit budget; exit 4 means held text, masked before citing; exit 5 (no
   key) means WebFetch and a hand header. Snapshots are immutable.
7. **News signals:** each `news-watch` item naming a company off the watchlist:
   `python3 scripts/fetch_ats.py --board <vendor>:<token>:"<Company>" --dry-run` and a `targets/companies.yaml` row
   proposal with its `segment`, for job-scout.
