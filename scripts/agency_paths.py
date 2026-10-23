"""Where the agency's code and your data live. Stdlib only; imported first by js_common and the entry scripts.

Two roots, never mixed:
  CODE_ROOT     this repo (scripts, agents, skills, schemas, templates, playbooks, defaults).
  DATA_ROOT     your data folder (profile, resumes, applications, prospects, jobs, state, hub, targets overrides ...).
  RUNTIME_ROOT  where the approval queue (work/approvals), run receipts (logs/runs) and the secret pointers
                (vault/) live. Defaults to <DATA_ROOT>/runtime; set it to point at an existing workspace.

Resolution of DATA_ROOT (first hit wins):
  1. env JAJA_DATA (older names still accepted: JOB_AGENCY_DATA, then JOB_SEARCH_ROOT)
  2. under pytest or JAJA_DEMO=1: examples/demo-data (never a configured real folder)
  3. ~/.config/jaja/config.yaml  `data_root:` (the file can be moved with env JAJA_CONFIG)
  else: a path that does not exist, so the failure names the setting.
RUNTIME_ROOT: env JAJA_RUNTIME, then (demo/pytest) a temp folder, then config `runtime_root:`, then <DATA_ROOT>/runtime.
Every JAJA_<NAME> variable also answers to its earlier name JOB_AGENCY_<NAME>; the config folder ~/.config/jaja also
falls back to ~/.config/job-agency when only that one exists. An eval sandbox (a `.eval-sandbox` marker beside this
scripts/ copy) is its own data and runtime root.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = CODE_ROOT / "scripts"
KERNEL_DIR = SCRIPTS_DIR / "kernel"
SCHEMAS_DIR = CODE_ROOT / "schemas"
DEMO_DATA = CODE_ROOT / "examples" / "demo-data"
NO_DATA = CODE_ROOT / "NO-DATA-FOLDER-CONFIGURED--set-JAJA-DATA-or-see-docs-config.md"
EVAL_MARK = CODE_ROOT / ".eval-sandbox"


def env(name: str, default: str | None = None) -> str | None:
    """JAJA_<name>, else the earlier JOB_AGENCY_<name>, else default (empty values count as unset)."""
    for prefix in ("JAJA_", "JOB_AGENCY_"):
        value = os.environ.get(prefix + name)
        if value:
            return value
    return default


def config_dir() -> Path:
    """The folder for your config file, deny-list and connector tokens: JAJA_CONFIG_DIR, else ~/.config/jaja, else (only
    when it exists and the new one does not) the earlier ~/.config/job-agency."""
    explicit = env("CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    new, old = Path.home() / ".config" / "jaja", Path.home() / ".config" / "job-agency"
    return old if (old.is_dir() and not new.exists()) else new


def config_file() -> Path:
    explicit = env("CONFIG")
    return Path(explicit).expanduser() if explicit else config_dir() / "config.yaml"


def read_config() -> dict[str, str]:
    """The flat `key: value` lines of the config file (no YAML dependency)."""
    out: dict[str, str] = {}
    try:
        for line in config_file().read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].rstrip()
            if ":" in line and not line.startswith((" ", "\t")):
                k, _, v = line.partition(":")
                v = v.strip().strip("'\"")
                if v:
                    out[k.strip()] = v
    except OSError:
        pass
    return out


def _resolve_data() -> Path:
    if EVAL_MARK.is_file():
        return CODE_ROOT
    explicit = env("DATA") or os.environ.get("JOB_SEARCH_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if _demo_mode():
        return DEMO_DATA                      # tests and the demo never read a configured real data folder
    cfg = read_config().get("data_root")
    if cfg:
        return Path(cfg).expanduser().resolve()
    return NO_DATA


def _demo_mode() -> bool:
    return env("DEMO") == "1" or "pytest" in sys.modules


def _resolve_runtime(data: Path) -> Path:
    if EVAL_MARK.is_file():
        return CODE_ROOT
    explicit = env("RUNTIME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if _demo_mode() and data == DEMO_DATA:
        import tempfile
        return Path(tempfile.gettempdir()) / "jaja-demo-runtime"      # never write into the repo or a real runtime
    configured = read_config().get("runtime_root")
    return Path(configured).expanduser().resolve() if configured else data / "runtime"


DATA_ROOT = _resolve_data()
RUNTIME_ROOT = _resolve_runtime(DATA_ROOT)


def require_data() -> Path:
    """DATA_ROOT, or exit with a message that says how to set it."""
    if not DATA_ROOT.exists():
        sys.exit("jaja: no data folder. Set JAJA_DATA=<folder> (or data_root: in "
                 f"{config_file()}); `python3 tools/init-data.py --demo <folder>` makes one.")
    return DATA_ROOT
