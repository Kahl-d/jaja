#!/usr/bin/env bash
# new-programme.sh -- scaffold an orchestrator/lane hub for any programme (a build or a life programme).
#
#   scripts/kernel/new-programme.sh --programme "Example Apply Sprint" --prefix EX --branch main \
#        --root <your data folder> [--ticket EX-12] [--base origin/main] [--author "Name <email>"] [--force]
#
# Creates <root>/{hub/,TRACKER.md,DIVISION.md,classify.py} from templates/orchestration/ (in this repo),
# substituting the {{TOKENS}}, and writes <root>/hub/programme.json (the pointer the lane_* hooks
# read). Refuses to overwrite an existing hub unless --force. NEVER scaffold a second hub beside a
# live one.
#
# The hooks look for the hub at <data folder>/hub/. --root is the folder the hub/ goes under: normally
# your data folder (JOB_AGENCY_DATA).
#
# Notes on the port from PowerShell are in skills/lane-orchestration/PORT-NOTES.md section 2.
#
# ASCII ONLY in this file. Bash 3.2 compatible (macOS default): no associative arrays.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
os_root="$(cd "$here/../.." && pwd)"
tpl="$os_root/templates/orchestration"

programme=""
prefix=""
ticket=""
branch=""
base="origin/main"
root=""
author=""
force=0

usage() {
    cat <<EOF
usage: new-programme.sh --programme <name> --root <dir> [--prefix <PFX>] [--ticket <T>]
                        [--branch <shipping-branch>] [--base <ref>] [--author "<Name <email>>"] [--force]

  --programme  the programme's human name (required)                 e.g. "Example Apply Sprint"
  --root       the project directory the hub/ goes under (required)  e.g. ~/job-agency-data
  --prefix     one UPPERCASE prefix per programme; derived from the name's initials if omitted
  --ticket     the ticket / id the shipping branch carries            e.g. EX-12   (default: empty)
  --branch     the shipping branch                                    (default: empty -- fill later)
  --base       the base ref the division diffs against               (default: origin/main)
  --author     the one permitted git identity                         (default: git config of --root, then global)
  --force      overwrite an existing hub (dangerous; refuse by default)
EOF
}

die() { printf 'new-programme.sh: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --programme) programme="${2:-}"; shift 2 ;;
        --prefix)    prefix="${2:-}";    shift 2 ;;
        --ticket)    ticket="${2:-}";    shift 2 ;;
        --branch)    branch="${2:-}";    shift 2 ;;
        --base)      base="${2:-}";      shift 2 ;;
        --root)      root="${2:-}";      shift 2 ;;
        --author)    author="${2:-}";    shift 2 ;;
        --force)     force=1;            shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           usage >&2; die "unknown argument: $1" ;;
    esac
done

[ -n "$programme" ] || { usage >&2; die "--programme is required"; }
[ -n "$root" ]      || { usage >&2; die "--root <dir> is required (the project directory the hub/ goes under)"; }
[ -d "$tpl" ]       || die "templates not found: $tpl"

# ---- resolve the destination -------------------------------------------------
mkdir -p "$root"
dest="$(cd "$root" && pwd)"

if [ -e "$dest/hub" ] && [ "$force" -eq 0 ]; then
    die "a hub already exists at $dest. Edit it in place, or pass --force. NEVER scaffold a second hub beside a live one."
fi

# ---- the prefix: initials of the programme name, uppercase, at most 4 ----------
if [ -z "$prefix" ]; then
    prefix="$(printf '%s' "$programme" | tr -cs 'A-Za-z0-9' '\n' \
              | awk 'NF { printf "%s", toupper(substr($0, 1, 1)) }' | cut -c1-4)"
fi
[ -n "$prefix" ] || die "could not derive a prefix from \"$programme\"; pass --prefix"

# ---- the author: the one permitted identity ----------------------------------
if [ -z "$author" ]; then
    an="$(git -C "$dest" config user.name  2>/dev/null || git config --global user.name  2>/dev/null || true)"
    ae="$(git -C "$dest" config user.email 2>/dev/null || git config --global user.email 2>/dev/null || true)"
    if [ -n "$an" ] && [ -n "$ae" ]; then
        author="$an <$ae>"
    else
        author="<the one permitted identity -- set with --author, verify with git var GIT_AUTHOR_IDENT>"
    fi
fi

today="$(date +%F)"

# ---- the token map (parallel arrays: bash 3.2 has no associative arrays) -------
keys=(
    "{{PROGRAMME}}"
    "{{PREFIX}}"
    "{{TICKET}}"
    "{{BRANCH}}"
    "{{BASE}}"
    "{{AUTHOR}}"
    "{{DATE}}"
    "{{HUB}}"
    "{{SCOPE}}"
    "{{ORCH_ADDRESS}}"
    "{{ORCH_WORKTREE}}"
    "{{ACCOUNTS}}"
    "{{MISSION}}"
    "{{LANE_TREE}}"
    "{{DELIVERABLE}}"
    "{{ENTRY_CONDITIONS}}"
    "{{LAUNCH_CMD}}"
    "{{HEALTH_CHECKS}}"
    "{{RELEASE_CMD}}"
    "{{DATA_STORE}}"
    "{{SERIALISED_RESOURCE}}"
    "{{PROVISIONING}}"
)
vals=(
    "$programme"
    "$prefix"
    "$ticket"
    "$branch"
    "$base"
    "$author"
    "$today"
    "hub"
    "<SCOPE>"
    "${prefix}-ORCH  (the orchestrator's registry name -- launched with: claude --name ${prefix}-ORCH)"
    "<the shared worktree>"
    "<UNMEASURED -- run the five-minute probe in references/messaging.md section 5 and record the result>"
    "<one paragraph: what is shipping, and what a person gets from it>"
    "<scope> ... <one line each>"
    "<the PR / the release / the artifact>"
    "- <the standing facts that bite in THIS programme>"
    "<how a lane launches the shared resource on ITS OWN worktree>"
    "<the probes that prove every plane, incl. the feature itself>"
    "<how to kill it by PID and release the slot>"
    "<the machine-global store, if there is one>"
    "<anything per-machine that must be serialised>"
    "- <the steps a fresh worktree needs that the installer does NOT do>"
)

printf 'scaffolding hub -> %s\n' "$dest"

# ---- copy the templates, substituting tokens (.py files are copied untouched) ---
while IFS= read -r f; do
    rel="${f#$tpl/}"
    out="$dest/$rel"
    mkdir -p "$(dirname "$out")"
    case "$f" in
        *.py)
            cp "$f" "$out"
            ;;
        *)
            text="$(cat "$f")"
            i=0
            while [ "$i" -lt "${#keys[@]}" ]; do
                k="${keys[$i]}"; v="${vals[$i]}"
                # bash 3.2 keeps literal quotes around a QUOTED replacement; leave $v unquoted
                # (the whole expansion is already inside double quotes, so no splitting/globbing)
                text="${text//"$k"/$v}"
                i=$((i + 1))
            done
            printf '%s\n' "$text" > "$out"
            ;;
    esac
    printf '  + %s\n' "$rel"
done < <(find "$tpl" -type f | sort)

# ---- a machine-readable pointer the lane_* hooks read ---------------------------
PROGRAMME="$programme" PREFIX="$prefix" TICKET="$ticket" BRANCH="$branch" BASE="$base" \
AUTHOR="$author" TODAY="$today" \
python3 - "$dest/hub/programme.json" <<'PY'
import json, os, sys
meta = {
    "programme": os.environ["PROGRAMME"],
    "prefix": os.environ["PREFIX"],
    "ticket": os.environ["TICKET"],
    "shipping_branch": os.environ["BRANCH"],
    "base": os.environ["BASE"],
    "author": os.environ["AUTHOR"],
    "created": os.environ["TODAY"],
    "hub": "hub",
    "lanes": [],   # [{"scope": "PAYMENTS", "branch": "ar-payments", "parent": null, "session_ids": []}, ...]
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2)
    fh.write("\n")
PY
printf '  + hub/programme.json\n'

cat <<EOF

NEXT -- in this order, and none of it is optional:
  1. Launch this session as the orchestrator: claude --name ${prefix}-ORCH (that sets the addressable
     registry name). The SessionStart hook publishes your address to hub/registry.json; write it into
     hub/LANES-LIVE.md section B with a UTC timestamp. Run the five-minute probe
     (references/messaging.md section 5) before asserting messaging works or fails.
  2. Fill classify.py's GROUPS + ALSO tables, run it, and do not accept the division until
     UNASSIGNED is 0. Then run the adversarial pass in DIVISION.md section 6.
  3. Fill hub/README.md's {{...}} that the scaffolder could not know.
  4. Fill hub/RESEARCH-MAP.md so no lane re-derives what is on disk.
  5. Write hub/briefs/<scope>.md + hub/lanes/<scope>-ORDERS.md (section 0 is standing), and add each
     lane to hub/programme.json "lanes" ({"scope", "branch", "parent"}) so the hooks can derive its role.
     A lane launched from the Desktop app cannot set OS_LANE: add its session id to "session_ids".
  6. Hand the owner hub/starters/_LANE-STARTER.md filled in. THEY start the lane: claude --name ${prefix}-<SCOPE>.

Doctrine: skills/lane-orchestration/ (SKILL.md + references/)
EOF
