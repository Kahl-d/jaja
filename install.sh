#!/usr/bin/env bash
# install.sh - set up jaja on this machine. Safe to run again: nothing is overwritten.
#
#   ./install.sh [--demo] [--yes] [--data DIR] [--no-hook] [--skip-doctor]
#
#   --demo         use the synthetic demo data (Sam Rivera, Acme Corp, ...) instead of a blank folder
#   --yes          do not ask before installing a missing Python module
#   --data DIR     the folder for YOUR records (default ~/jaja-data; with --demo ~/jaja-demo)
#   --no-hook      do not install the git pre-commit leak guard in this checkout
#   --skip-doctor  stop after setup (doctor is the last step otherwise)
#
# What it does: checks python3 >= 3.11 (and notes whether uv is there), installs PyYAML with `pip --user` when it is
# missing (asks first), creates the data folder with tools/init-data.py, writes ~/.config/jaja/config.yaml
# (data_root only; other keys are kept), installs the pre-commit leak guard when this is a git checkout, prints the two
# plugin commands to run, and finishes with scripts/doctor.py. Exit status is doctor's.
# Bash 3.2 compatible (macOS default).

set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
demo=0; yes=0; data=""; hook=1; run_doctor=1

while [ $# -gt 0 ]; do
    case "$1" in
        --demo) demo=1; shift ;;
        --yes|-y) yes=1; shift ;;
        --data) [ $# -ge 2 ] || { echo "install: --data needs a folder" >&2; exit 2; }; data="$2"; shift 2 ;;
        --no-hook) hook=0; shift ;;
        --skip-doctor) run_doctor=0; shift ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install: unknown option $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

if [ -z "$data" ]; then
    if [ "$demo" = 1 ]; then data="$HOME/jaja-demo"; else data="$HOME/jaja-data"; fi
fi
case "$data" in "~"/*) data="$HOME/${data#\~/}" ;; esac

# ---------------------------------------------------------------- python
step "Python"
py="$(command -v python3 || true)"
if [ -z "$py" ]; then
    say "python3 was not found. Install Python 3.11 or newer (macOS: brew install python@3.12), then run this again." >&2
    exit 1
fi
if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    say "python3 is $("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])'); jaja needs 3.11 or newer." >&2
    say "Install a newer Python (macOS: brew install python@3.12) and run this again." >&2
    exit 1
fi
say "ok: $("$py" --version 2>&1) at $py"

if command -v uv >/dev/null 2>&1; then
    say "ok: uv found ($(command -v uv)); the job-search MCP server will start with it"
else
    say "note: uv not found. The MCP server starts with \`uv run\`; install uv for it:"
    say "      curl -LsSf https://astral.sh/uv/install.sh | sh     (or: brew install uv)"
fi

# ---------------------------------------------------------------- modules
step "Python modules"
if "$py" -c 'import yaml, requests' >/dev/null 2>&1; then
    say "ok: PyYAML and requests are installed"
else
    say "PyYAML or requests is missing (the scripts read YAML and fetch job boards)."
    go=0
    if [ "$yes" = 1 ]; then
        go=1
    elif [ -t 0 ]; then
        printf 'Install it now with: %s -m pip install --user pyyaml requests ? [y/N] ' "$py"
        read -r reply || reply=""
        case "$reply" in y|Y|yes|YES) go=1 ;; esac
    fi
    if [ "$go" = 1 ]; then
        "$py" -m pip install --user pyyaml requests || {
            say "pip could not install PyYAML/requests (an externally managed Python?). Try: brew install pyyaml, or a venv:" >&2
            say "  python3 -m venv ~/.venvs/jaja && ~/.venvs/jaja/bin/pip install pyyaml requests" >&2
            exit 1
        }
    else
        say "Not installed. Run: $py -m pip install --user pyyaml requests    (or re-run with --yes)" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------- data folder
step "Data folder"
init_args=("$data" --set-config)
[ "$demo" = 1 ] && init_args=("$data" --demo --set-config)
"$py" "$here/tools/init-data.py" "${init_args[@]}"

# ---------------------------------------------------------------- leak guard
step "Pre-commit leak guard"
if [ "$hook" = 0 ]; then
    say "skipped (--no-hook)"
elif [ -d "$here/.git" ]; then
    hookfile="$here/.git/hooks/pre-commit"
    marker="# jaja leak guard"
    if [ -e "$hookfile" ] && ! grep -q "$marker" "$hookfile" 2>/dev/null; then
        say "note: $hookfile already exists and is not ours; left alone."
        say "      Add this line to it:  \"$here/tools/pre-commit-leak.sh\""
    else
        mkdir -p "$here/.git/hooks"
        printf '#!/bin/sh\n%s\nexec "%s/tools/pre-commit-leak.sh"\n' "$marker" "$here" > "$hookfile"
        chmod +x "$hookfile"
        say "ok: installed $hookfile (runs tools/leak_check.py --staged before every commit)"
    fi
else
    say "not a git checkout: nothing to install (the repo's .pre-commit-config.yaml works with the pre-commit tool)"
fi

# ---------------------------------------------------------------- plugin
step "Plugin (run these two commands yourself)"
say "  claude plugin marketplace add \"$here\""
say "  claude plugin install jaja@jaja --scope user"
say "Then start a session and try a skill, for example /jaja:doctor."

# ---------------------------------------------------------------- doctor
if [ "$run_doctor" = 1 ]; then
    step "Doctor"
    set +e
    "$py" "$here/scripts/doctor.py"
    rc=$?
    set -e
    if [ "$rc" = 0 ]; then
        say ""
        say "Done. Your data folder: $data"
    else
        say ""
        say "Doctor found problems (see the FAIL lines above). Fix them and run: $py $here/scripts/doctor.py --fix-hints" >&2
    fi
    exit "$rc"
fi
say ""
say "Done (doctor skipped). Your data folder: $data"
