# Job dashboard: weekly funnel step 2 (gather the numbers)

The skill links here and binds it as if inline.

2. Gather, each number from a command, never from memory:
   - Interviews first: `python3 scripts/interviews.py` (booked and held this week and last, the
     warm and cold interview rates with n). Its line is the first line of `## Numbers`.
   - Outbound funnel by stage: `tracker.py metrics` (`--json` for a machine copy).
   - Discovery: `grep -c "" state/seen.jsonl` and the week's `js-scout` receipt counts; jobs `queued` and `skipped`.
   - Apply latency: for each application with `applied_at` this week, days from the job's `posted_at`; the median.
   - Response rate: applications with any inbound event over applications, split by `applied_via` (ats, referral,
     recruiter, email, other) and by page (the base unchanged vs variant).
   - Backlog funnel: `grep -h "^status:" prospects/*.md | sort | uniq -c`.
   - Touches per reply: outbound touches this week over inbound replies this week (from `touches/`).
   - Ghost rate, days in stage (median per stage), visa clocks and unemployment days (from `legal/timers.md` and
     `clock.yaml`).
   - Two health lines, last in `## Numbers`: `python3 scripts/role_taxonomy.py --write` (the
     week's role mix by focus profile, saved to `state/recruiter/role-taxonomy-<date>.md`; the line names the top
     three profiles and the titles matching none) and `python3 scripts/card_audit.py` (stale names and dead
     references in the cards and skills; the line is its FINDINGS count, and any finding goes to the Recruiter).
   - Finding: `python3 scripts/finding_metrics.py --write` (state/reports/finding-<date>.md and .json:
     roles learned of outside our boards, the missing employers, board vs alert lead time, freshness at action);
     its summary line goes under `## Numbers`.
   The interview floor comes from `targets/goals.yaml` (`interviews_booked.weekly`, no ceiling; state/recruiter/
   target.yaml only when goals.yaml is missing); the day's other
   targets from `state/daily/<date>.md`; never from memory. Volume (applied, discovered, touches) is context, not
   progress: it follows the interview numbers and is never the headline.
   Rank the three insights by how much each changes interviews booked, and say how.
