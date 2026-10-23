"""Where the agency's code and your data live. Stdlib only; imported first by js_common and the entry scripts.

Two roots, never mixed:
  CODE_ROOT     this repo (scripts, agents, skills, schemas, templates, playbooks, defaults).
  DATA_ROOT     your data folder (profile, resumes, applications, prospects, jobs, state, hub, targets overrides ...).
  RUNTIME_ROOT  where the approval queue (work/approvals), run receipts (logs/runs) and the secret pointers
                (vault/) live. Defaults to <DATA_ROOT>/runtime; set it to point at an existing workspace.

Resolution of DATA_ROOT (first hit wins):
  1. env JOB_AGENCY_DATA
  2. env JOB_SEARCH_ROOT (legacy name)
  3. under pytest or JOB_AGENCY_DEMO=1: examples/demo-data (never a configured real folder)
  4. ~/.config/job-agency/config.yaml  `data_root:` (the file can be moved with env JOB_AGENCY_CONFIG)
  else: a path that does not exist, so the failure names the setting.
RUNTIME_ROOT: env JOB_AGENCY_RUNTIME, then (demo/pytest) a temp folder, then config `runtime_root:`, then <DATA_ROOT>/runtime.
An eval sandbox (a `.eval-sandbox` marker beside this scripts/ copy) is its own data and runtime root.
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
NO_DATA = CODE_ROOT / "NO-DATA-FOLDER-CONFIGURED--set-JOB_AGENCY-DATA-or-see-docs-config.md"
EVAL_MARK = CODE_ROOT / ".eval-sandbox"


def config_file() -> Path:
    env = os.environ.get("JOB_AGENCY_CONFIG")
    return Path(env).expanduser() if env else Path.home() / ".config" / "job-agency" / "config.yaml"


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
    for var in ("JOB_AGENCY_DATA", "JOB_SEARCH_ROOT"):
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser().resolve()
    if _demo_mode():
        return DEMO_DATA                      # tests and the demo never read a configured real data folder
    cfg = read_config().get("data_root")
    if cfg:
        return Path(cfg).expanduser().resolve()
    return NO_DATA


def _demo_mode() -> bool:
    return os.environ.get("JOB_AGENCY_DEMO") == "1" or "pytest" in sys.modules


def _resolve_runtime(data: Path) -> Path:
    if EVAL_MARK.is_file():
        return CODE_ROOT
    env = os.environ.get("JOB_AGENCY_RUNTIME")
    if env:
        return Path(env).expanduser().resolve()
    if _demo_mode() and data == DEMO_DATA:
        import tempfile
        return Path(tempfile.gettempdir()) / "job-agency-demo-runtime"      # never write into the repo or a real runtime
    env = read_config().get("runtime_root")
    return Path(env).expanduser().resolve() if env else data / "runtime"


DATA_ROOT = _resolve_data()
RUNTIME_ROOT = _resolve_runtime(DATA_ROOT)


def require_data() -> Path:
    """DATA_ROOT, or exit with a message that says how to set it."""
    if not DATA_ROOT.exists():
        sys.exit("job-agency: no data folder. Set JOB_AGENCY_DATA=<folder> (or data_root: in "
                 f"{config_file()}); `python3 tools/init-data.py --demo <folder>` makes one.")
    return DATA_ROOT
