# Funding signals (SEC Form D) · T2 playbook
Route: SEC EDGAR full text search via `scripts/funding_signals.py --dry-run --days 3 --json` (keyless, polite pace; many requests, can exceed 120 s: run in background).
Query set: one query per target company plus AI/ML keyword queries, a short window, then read amounts.
Reading: only a target company or a raise above the threshold in the policy config is a lead ("just raised, hiring before posting"); tiny Form D shells are noise.
Next step on a real signal: the company's own careers/Ashby board by name, then hand up as a find with source funding-signal.
Known fault: EDGAR returns HTTP 500 for single targets at times; retry once, then log it in the RUN line.
