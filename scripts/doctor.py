#!/usr/bin/env python3
"""doctor.py - is this install healthy? One line per check, then a summary. Read-only; no network.

    python3 scripts/doctor.py [--data PATH] [--json] [--fix-hints]

  --data PATH    check this data folder instead of the configured one (JAJA_DATA, then
                 ~/.config/jaja/config.yaml `data_root:`; JAJA_DEMO=1 selects examples/demo-data)
  --json         print the checks and the summary as JSON
  --fix-hints    under every warn or fail, also print the command that fixes it

Checks: python and modules, uv, the data root and its folders, config, profile files, records and evidence ids,
role/skill cards, kernel lint, adapter drift (sync --check), hooks (files exist, each runs on an empty payload),
plugin manifests, the MCP server import, connector tokens (names only, never values; all optional), routines (all
disabled by default) and the leak guard on staged files.

States: ok, warn (works, act when convenient), fail (broken), skip (not applicable here). A machine with only the
demo data has no fail. Exit 1 on any fail, else 0.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = CODE_ROOT / "scripts"
MIN_PY = (3, 11)
REQUIRED_DATA_DIRS = ["profile", "resumes", "applications", "prospects", "jobs", "state", "hub", "targets", "config"]
PROFILE_FILES = ["facts.yaml", "evidence.yaml", "PROFILE.md", "VOICE.md", "skills-taxonomy.yaml"]
KNOWN_TOKENS = {
    "gmail_client_secret.json": "Gmail/Calendar OAuth client",
    "gmail_token_read.json": "Gmail read",
    "gmail_token_compose.json": "Gmail drafts",
    "gmail_token_send.json": "Gmail send",
    "calendar_token.json": "Calendar",
}
NOT_TOKENS = {"config.yaml", "denylist.txt", "denylist.txt.example"}


@dataclass
class Check:
    state: str            # ok | warn | fail | skip
    name: str
    message: str
    hint: str = ""        # short pointer shown with warn/fail
    fix: str = ""         # the command, shown with --fix-hints


CHECKS: list[Check] = []


def add(state: str, name: str, message: str, hint: str = "", fix: str = "") -> None:
    CHECKS.append(Check(state, name, message, hint, fix))


def run(cmd: list[str], env: dict[str, str], timeout: int = 180, cwd: Path | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=str(cwd or CODE_ROOT), capture_output=True, text=True, timeout=timeout,
                          input=stdin if stdin is not None else None,
                          stdin=None if stdin is not None else subprocess.DEVNULL)


def tail(text: str, n: int = 2) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[:300]


# ----------------------------------------------------------------------------- environment
def check_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PY:
        add("fail", "python", f"Python {v.major}.{v.minor}.{v.micro}, need {MIN_PY[0]}.{MIN_PY[1]} or newer",
            "install a newer Python", "brew install python@3.12   # or your platform's package manager")
        return
    add("ok", "python", f"Python {v.major}.{v.minor}.{v.micro}")
    try:
        import yaml  # noqa: F401
        add("ok", "python-modules", f"pyyaml {getattr(yaml, '__version__', '')}".strip())
    except ImportError:
        add("fail", "python-modules", "pyyaml is missing", "the scripts read YAML", "python3 -m pip install --user pyyaml")
    import importlib.util
    # find_spec, not import: agency_paths treats "pytest in sys.modules" as "running tests" and would pick the demo data.
    if importlib.util.find_spec("pytest") is not None:
        add("ok", "python-dev-modules", "pytest present")
    else:
        add("warn", "python-dev-modules", "pytest is missing (only needed to run the tests)",
            "optional", "python3 -m pip install --user pytest")
    if importlib.util.find_spec("requests") is not None:
        add("ok", "python-requests", "requests present")
    else:
        add("fail", "python-requests", "requests is missing", "the fetch and API scripts use it", "python3 -m pip install --user requests")
    extras = {"gmail/calendar": "googleapiclient", "resume rendering": "rendercv", "latex templates": "jinja2",
              "pdf checks": "pypdf", "docx check": "docx", "phone lint": "phonenumbers"}
    have = [k for k, m in extras.items() if importlib.util.find_spec(m) is not None]
    missing = [k for k in extras if k not in have]
    add("ok", "optional-extras", "present: " + (", ".join(have) or "none") + ("; not installed: " + ", ".join(missing) if missing else ""),
        "" if not missing else "optional; see requirements-optional.txt")


def check_uv() -> None:
    uv = shutil.which("uv")
    if uv:
        add("ok", "uv", f"uv found ({uv})")
    else:
        add("warn", "uv", "uv not found: the MCP server starts with `uv run`", "install uv for the job-search MCP server",
            "curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv")


# ----------------------------------------------------------------------------- data
def resolve_data(args_data: str | None) -> tuple[Path, Path, dict[str, str]]:
    env = dict(os.environ)
    if args_data:
        env["JAJA_DATA"] = str(Path(args_data).expanduser().resolve())
        os.environ["JAJA_DATA"] = env["JAJA_DATA"]
    sys.path.insert(0, str(SCRIPTS))
    import agency_paths as ap
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return ap.DATA_ROOT, ap.RUNTIME_ROOT, env


def check_data(data: Path, runtime: Path) -> bool:
    import agency_paths as ap
    if data == ap.NO_DATA or not data.exists():
        add("fail", "data-root", "no data folder configured" if data == ap.NO_DATA else f"{data} does not exist",
            "set JAJA_DATA or data_root: in the config file",
            "python3 tools/init-data.py ~/jaja-data --set-config   # or add --demo to try the demo data")
        return False
    demo = data == ap.DEMO_DATA or ap.DEMO_DATA in data.parents
    inside = data == CODE_ROOT or CODE_ROOT in data.parents
    if inside and not demo:
        add("warn", "data-root", f"{data} is inside the repo", "your records must stay out of the repo",
            "python3 tools/init-data.py ~/jaja-data --set-config")
    else:
        add("ok", "data-root", f"{data}" + ("  (demo data)" if demo else ""))
    missing = [d for d in REQUIRED_DATA_DIRS if not (data / d).is_dir()]
    if missing:
        add("fail", "data-folders", "missing: " + ", ".join(missing), "the folder skeleton is incomplete",
            f"python3 tools/init-data.py {data}   # adds what is missing, never overwrites")
    else:
        add("ok", "data-folders", f"{len(REQUIRED_DATA_DIRS)} required folders present")
    if runtime.exists():
        add("ok", "runtime-root", f"{runtime}")
    else:
        add("warn", "runtime-root", f"{runtime} does not exist yet (approvals and receipts go there)",
            "created on first use", f"python3 tools/init-data.py {data}")
    return True


def check_config(data: Path) -> None:
    import agency_paths as ap
    import yaml
    cfg = ap.config_file()
    if cfg.is_file():
        keys = ap.read_config()
        bad = [k for k in ("data_root", "runtime_root") if k in keys and not Path(keys[k]).expanduser().exists()]
        if bad:
            add("fail", "config-file", f"{cfg}: {', '.join(bad)} points at a folder that does not exist",
                "fix the path", f"edit {cfg}")
        else:
            add("ok", "config-file", f"{cfg} ({', '.join(sorted(keys)) or 'no keys'})")
    else:
        add("ok", "config-file", f"no {cfg} (JAJA_DATA or the demo data is in use)")
    cdir = data / "config"
    problems = []
    n = 0
    for f in sorted(cdir.glob("*.yaml")) if cdir.is_dir() else []:
        n += 1
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"{f.name}: {str(exc).splitlines()[0][:80]}")
            continue
        if doc is not None and not isinstance(doc, dict):
            problems.append(f"{f.name}: top level is not a mapping")
        elif isinstance(doc, dict):
            for k, v in doc.items():
                if k in ("identity", "style", "policy") and not isinstance(v, dict):
                    problems.append(f"{f.name}: {k} is not a mapping")
    if problems:
        add("fail", "config-values", "; ".join(problems), "fix the YAML in your config folder", f"edit {cdir}/*.yaml")
    elif n:
        add("ok", "config-values", f"{n} file(s) in {cdir.name}/ parse")
    else:
        add("warn", "config-values", "no config/*.yaml in the data folder (neutral defaults apply)",
            "set your identity and style", f"python3 tools/init-data.py {data}")


def check_profile(data: Path, env: dict[str, str]) -> None:
    prof = data / "profile"
    missing = [f for f in PROFILE_FILES if not (prof / f).is_file()]
    if missing:
        add("fail", "profile-files", "missing: " + ", ".join(missing), "the profile is the source of every resume",
            f"python3 tools/init-data.py {data}")
        return
    add("ok", "profile-files", f"{len(PROFILE_FILES)} profile files present")
    import yaml
    try:
        facts = yaml.safe_load((prof / "facts.yaml").read_text(encoding="utf-8")) or {}
        name = ((facts.get("identity") or {}).get("full_name") or "").strip()
    except (OSError, yaml.YAMLError):
        add("fail", "profile-facts", "facts.yaml does not parse", "fix the YAML", f"python3 scripts/check_records.py {prof / 'facts.yaml'}")
        return
    if name:
        add("ok", "profile-facts", f"facts.yaml names {name}")
    else:
        add("warn", "profile-facts", "facts.yaml has no identity.full_name yet", "fill in your profile",
            f"edit {prof / 'facts.yaml'}")
    r = run([sys.executable, "-B", str(SCRIPTS / "check_evidence.py")], env)
    if r.returncode == 0:
        add("ok", "evidence-ids", tail(r.stdout, 1) or "every cited evidence id exists")
    else:
        add("warn", "evidence-ids", f"{r.returncode} problem(s): {tail(r.stdout or r.stderr)}", "run the check for detail",
            "python3 scripts/check_evidence.py")
    r = run([sys.executable, "-B", str(SCRIPTS / "check_records.py"), "--all", "--root", str(data)], env)
    if r.returncode == 0:
        add("ok", "records", tail(r.stdout, 1) or "every record parses")
    else:
        add("warn", "records", tail(r.stdout or r.stderr), "a hand-written record is invalid",
            f"python3 scripts/check_records.py --all --root {data}")


# ----------------------------------------------------------------------------- code
def check_cards(env: dict[str, str]) -> None:
    r = run([sys.executable, "-B", str(SCRIPTS / "card_audit.py"), "--json"], env)
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        add("warn", "role-cards", f"card_audit gave no JSON: {tail(r.stderr or r.stdout)}", "run it by hand",
            "python3 scripts/card_audit.py")
        return
    files = data.get("files") or []
    kinds: dict[str, int] = {}
    for fr in files:
        for f in fr.get("findings") or []:
            kinds[str(f.get("kind"))] = kinds.get(str(f.get("kind")), 0) + 1
    findings = sum(kinds.values())
    if not findings:
        add("ok", "role-cards", f"{len(files)} cards audited, no findings")
    else:
        # path findings name data files a user creates over time; cards are advisory, so this never fails.
        add("warn", "role-cards", f"{len(files)} cards audited, {findings} finding(s): "
            + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
            "advisory: most are data files you have not created yet", "python3 scripts/card_audit.py")


def check_kernel(env: dict[str, str]) -> None:
    lint = SCRIPTS / "kernel" / "lint.py"
    if lint.is_file():
        r = run([sys.executable, "-B", str(lint)], env)
        if r.returncode == 0:
            add("ok", "lint", tail(r.stdout, 1) or "lint clean")
        else:
            add("fail", "lint", tail(r.stdout or r.stderr, 3), "schemas, skills or routines are invalid",
                "python3 scripts/kernel/lint.py")
    else:
        add("fail", "lint", "scripts/kernel/lint.py is missing", "reinstall the plugin")
    sync = SCRIPTS / "kernel" / "sync.py"
    if sync.is_file():
        r = run([sys.executable, "-B", str(sync), "--check"], env)
        if r.returncode == 0:
            add("ok", "adapters", tail(r.stdout, 1) or "adapters up to date")
        else:
            add("fail", "adapters", tail(r.stdout or r.stderr, 3), "generated adapters drifted from their sources",
                "python3 scripts/kernel/sync.py")
    else:
        add("fail", "adapters", "scripts/kernel/sync.py is missing", "reinstall the plugin")


def check_hooks(env: dict[str, str]) -> None:
    hj = CODE_ROOT / "hooks" / "hooks.json"
    try:
        spec = json.loads(hj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        add("fail", "hooks", f"hooks/hooks.json: {exc}", "fix the JSON")
        return
    cmds: list[tuple[str, str]] = []
    for event, groups in (spec.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                if h.get("type") == "command":
                    cmds.append((event, h["command"]))
    if not cmds:
        add("fail", "hooks", "hooks.json lists no command hooks", "restore hooks/hooks.json")
        return
    missing, failing = [], []
    with tempfile.TemporaryDirectory(prefix="doctor-hooks-") as tmp:
        hook_env = dict(env, HOME=tmp, CLAUDE_PLUGIN_ROOT=str(CODE_ROOT), JAJA_DATA=str(Path(tmp) / "data"),
                        JAJA_RUNTIME=str(Path(tmp) / "runtime"), JAJA_CONFIG=str(Path(tmp) / "none.yaml"))
        hook_env.pop("JAJA_DEMO", None)
        hook_env.pop("JOB_AGENCY_DEMO", None)
        for event, cmd in cmds:
            line = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(CODE_ROOT))
            try:
                import shlex
                argv = shlex.split(line)
            except ValueError:
                failing.append(f"{event}: unparseable command")
                continue
            script = next((a for a in argv if a.endswith((".py", ".sh"))), None)
            if not script or not Path(script).is_file():
                missing.append(Path(script).name if script else cmd[:40])
                continue
            try:
                r = run(argv, hook_env, timeout=30, cwd=Path(tmp), stdin="{}")
            except subprocess.TimeoutExpired:
                failing.append(f"{Path(script).name}: timed out")
                continue
            if r.returncode != 0 or "Traceback" in r.stderr:
                failing.append(f"{Path(script).name}: rc={r.returncode} {tail(r.stderr, 1)}")
    if missing:
        add("fail", "hooks", "hook files missing: " + ", ".join(missing), "hooks.json names a file that is not there")
    elif failing:
        add("fail", "hooks", "; ".join(failing)[:400], "a hook crashes on an empty payload",
            "echo '{}' | CLAUDE_PLUGIN_ROOT=$PWD python3 hooks/<hook>.py")
    else:
        add("ok", "hooks", f"{len(cmds)} hooks: files exist and each runs on an empty payload")


def check_plugin() -> None:
    base = CODE_ROOT / ".claude-plugin"
    problems = []
    docs = {}
    for name in ("plugin.json", "marketplace.json"):
        try:
            docs[name] = json.loads((base / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{name}: {str(exc)[:80]}")
    plugin = docs.get("plugin.json") or {}
    for key in ("name", "version", "description"):
        if docs and not plugin.get(key):
            problems.append(f"plugin.json lacks {key}")
    for key in ("skills", "agents", "hooks", "mcpServers"):
        val = plugin.get(key)
        if isinstance(val, str) and not (CODE_ROOT / val).exists():
            problems.append(f"plugin.json {key} -> {val} does not exist")
    market = docs.get("marketplace.json") or {}
    names = [p.get("name") for p in market.get("plugins", []) if isinstance(p, dict)]
    if docs and plugin.get("name") not in names:
        problems.append("marketplace.json does not list the plugin")
    if problems:
        add("fail", "plugin-manifest", "; ".join(problems), "fix .claude-plugin/*.json")
        return
    msg = f"{plugin.get('name')} {plugin.get('version')}, marketplace {market.get('name')}"
    claude = shutil.which("claude")
    if claude:
        try:
            r = run([claude, "plugin", "validate", str(CODE_ROOT)], dict(os.environ), timeout=60)
            if r.returncode != 0:
                add("warn", "plugin-manifest", f"{msg}; `claude plugin validate`: {tail(r.stdout or r.stderr)}",
                    "the CLI's own validator disagrees", "claude plugin validate .")
                return
            msg += "; claude plugin validate ok"
        except (subprocess.TimeoutExpired, OSError):
            msg += "; claude plugin validate did not finish (skipped)"
    add("ok", "plugin-manifest", msg)


def check_mcp(env: dict[str, str]) -> None:
    mcp_dir = CODE_ROOT / "mcp"
    if not (mcp_dir / "job_search_mcp").is_dir():
        add("fail", "mcp-server", "mcp/job_search_mcp is missing", "reinstall the plugin")
        return
    e = dict(env, PYTHONPATH=str(mcp_dir))
    r = run([sys.executable, "-c", "import job_search_mcp.store"], e)
    if r.returncode != 0:
        add("fail", "mcp-server", f"the data layer does not import: {tail(r.stderr)}", "fix the import error",
            "cd mcp && python3 -c 'import job_search_mcp.store'")
        return
    # The server needs the `mcp` package at the version mcp/uv.lock pins, which `uv run` provides. Try that first
    # (offline, in a throwaway environment so the repo gets no .venv); fall back to this Python's own packages.
    uv = shutil.which("uv")
    if uv:
        with tempfile.TemporaryDirectory(prefix="doctor-mcp-") as tmp:
            ue = dict(env, UV_PROJECT_ENVIRONMENT=str(Path(tmp) / "venv"))
            try:
                r = run([uv, "run", "--offline", "--directory", str(mcp_dir), "python", "-B", "-c",
                         "import job_search_mcp.server"], ue, timeout=120)
            except subprocess.TimeoutExpired:
                r = None
        if r is not None and r.returncode == 0:
            add("ok", "mcp-server", "job_search_mcp imports under `uv run` (data layer and server)")
            return
    r = run([sys.executable, "-c", "import job_search_mcp.server"], e)
    if r.returncode == 0:
        add("ok", "mcp-server", "job_search_mcp imports (data layer and server)")
    else:
        add("warn", "mcp-server", "the data layer imports; the server needs the pinned `mcp` package "
            "(`uv run` installs it, which needs the network the first time)",
            "run the server through uv", "uv run --directory mcp job-search-mcp")


def check_connectors() -> None:
    import agency_paths as ap
    cdir = Path(ap.env("CONFIG_DIR") or ap.config_file().parent).expanduser()
    present = sorted(p.name for p in cdir.iterdir() if p.is_file()) if cdir.is_dir() else []
    tokens = [n for n in present if n not in NOT_TOKENS]
    if not tokens:
        add("ok", "connectors", f"not configured (optional; nothing in {cdir}). Setup: docs/SETUP-CONNECTORS.md")
        return
    loose = []
    for n in tokens:
        try:
            if (cdir / n).stat().st_mode & 0o077:
                loose.append(n)
        except OSError:
            pass
    known = [KNOWN_TOKENS[n] for n in tokens if n in KNOWN_TOKENS]
    other = [n for n in tokens if n not in KNOWN_TOKENS]
    msg = "present (names only): " + ", ".join(tokens)
    if known:
        msg += "  [" + ", ".join(known) + "]"
    if loose:
        add("warn", "connectors", msg + "; readable by others: " + ", ".join(loose), "tokens must be chmod 600",
            "chmod 600 " + " ".join(f"{cdir}/{n}" for n in loose))
    else:
        add("ok", "connectors", msg + (f" (+{len(other)} other)" if other and known else ""))


def check_routines() -> None:
    import yaml
    enabled, bad = [], []
    files = sorted((CODE_ROOT / "routines").glob("*.yaml"))
    for f in files:
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            bad.append(f.name)
            continue
        if isinstance(doc, dict) and doc.get("enabled") is True:
            enabled.append(f.stem)
    if bad:
        add("fail", "routines", "do not parse: " + ", ".join(bad), "fix the YAML")
    elif enabled:
        add("warn", "routines", f"{len(enabled)} of {len(files)} routines enabled: {', '.join(enabled[:6])}",
            "routines ship disabled; keep only what you turned on on purpose", "edit routines/<name>.yaml (enabled: false)")
    else:
        add("ok", "routines", f"all {len(files)} routines disabled")


def check_leaks(env: dict[str, str]) -> None:
    tool = CODE_ROOT / "tools" / "leak_check.py"
    if not (CODE_ROOT / ".git").exists() or not shutil.which("git"):
        add("skip", "leak-guard", "not a git checkout: nothing staged to scan")
        return
    if not tool.is_file():
        add("warn", "leak-guard", "tools/leak_check.py is missing", "restore it")
        return
    r = run([sys.executable, "-B", str(tool), "--staged", "--no-gitleaks", "--root", str(CODE_ROOT)], env)
    summary = tail(r.stdout, 1)
    if r.returncode == 0:
        import agency_paths as ap
        deny = ap.config_dir() / "denylist.txt"
        add("ok", "leak-guard", (summary or "no findings") + ("" if deny.is_file() else "; pattern checks only (no deny-list)"))
    else:
        add("fail", "leak-guard", tail(r.stdout, 3), "something personal is staged", "python3 tools/leak_check.py --staged")


# ----------------------------------------------------------------------------- main
def render(fix_hints: bool) -> None:
    tag = {"ok": "ok  ", "warn": "warn", "fail": "FAIL", "skip": "skip"}
    for c in CHECKS:
        line = f"[{tag[c.state]}] {c.name:<16} {c.message}"
        if c.state in ("warn", "fail") and c.hint:
            line += f"  -> {c.hint}"
        print(line)
        if fix_hints and c.state in ("warn", "fail") and c.fix:
            print(f"        fix: {c.fix}")


def summary() -> dict[str, int]:
    out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for c in CHECKS:
        out[c.state] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", help="data folder to check (default: the configured one)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fix-hints", action="store_true")
    a = ap.parse_args(argv)

    check_python()
    if not any(c.state == "fail" for c in CHECKS):
        check_uv()
        data, runtime, env = resolve_data(a.data)
        if check_data(data, runtime):
            check_config(data)
            check_profile(data, env)
        check_cards(env)
        check_kernel(env)
        check_hooks(env)
        check_plugin()
        check_mcp(env)
        check_connectors()
        check_routines()
        check_leaks(env)
    s = summary()
    if a.json:
        print(json.dumps({"checks": [asdict(c) for c in CHECKS], "summary": s}, indent=1))
    else:
        render(a.fix_hints)
        print(f"\ndoctor: {s['ok']} ok, {s['warn']} warn, {s['fail']} fail" + (f", {s['skip']} skipped" if s["skip"] else ""))
        if s["fail"]:
            print("doctor: fix the FAIL lines first (run with --fix-hints for the commands)")
    return 1 if s["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
