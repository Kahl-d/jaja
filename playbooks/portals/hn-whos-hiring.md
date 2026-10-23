# HN Who's Hiring · T2 playbook
Route: official HN Firebase API + Algolia (keyless, terms allow). No Chrome.
Query set (one pass a day):
1. `scripts/hn_hiring.py` collect() on the newest "Ask HN: Who is hiring?" thread: relevant = title filter hit; run in memory, do NOT write jobs/ (the scout role alone writes records).
2. collect_jobstories(): YC companies' job posts, newest first.
3. collect_algolia(since = now-72h): "hiring", "we're hiring", "is hiring" stories between threads.
Freshness: a comment's posted_at is the comment time, so 72h = thread comments since then. Also check the company's own Ashby/Greenhouse board for the role and its publishedAt; HN comment time is NOT the role's age.
Keep: roles matching the owner's target titles in the policy config; eligible for the owner's work authorization; drop regions and onsite locations the owner excludes.
Expect most comments to be filtered out by the age, location and role-fit checks. Comments often list several roles in one line; find_id per role.
