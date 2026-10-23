#!/usr/bin/env bash
# pre-commit-leak.sh - block a commit that carries anything personal. Run by .pre-commit-config.yaml and by the
# git hook install.sh writes. Staged files only.
#
# With the private deny-list (~/.config/job-agency/denylist.txt, built from YOUR data by tools/build_denylist.py and
# never committed) the check also catches your names, employers and contacts. Without it only the pattern checks
# run (emails, home paths, tokens, phone numbers, forbidden folders and file types) and this script says so.
# gitleaks runs too when it is installed. Bypass nothing: fix the finding, or add a reviewed line to
# tools/leak_allowlist.txt.
set -eu
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deny="${JOB_AGENCY_DENYLIST:-$HOME/.config/job-agency/denylist.txt}"
args=(--root "$repo" --staged)
if [ -f "$deny" ]; then
    args+=(--denylist "$deny")
else
    echo "pre-commit-leak: WARNING no deny-list at $deny; pattern checks only." >&2
    echo "                 Build one: python3 tools/build_denylist.py --data <your data folder>" >&2
    args+=(--no-denylist)
fi
exec python3 "$repo/tools/leak_check.py" "${args[@]}"
