"""js_common resolves both roots to the sandbox when the eval marker is present."""
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
import os


def _roots(js_root: Path) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-c", "import js_common as c; print(c.JS_ROOT); print(c.OS_ROOT)"],
        cwd=js_root / "scripts", capture_output=True, text=True, check=True,
        env={**os.environ, "JOB_AGENCY_DATA": os.environ.get("JOB_AGENCY_DATA") or str(SCRIPTS.parent / "examples" / "demo-data")})
    return out.stdout.split()


def test_marker_makes_sandbox_both_roots(tmp_path):
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts").mkdir(parents=True)
    for name in ("js_common.py", "agency_paths.py", "agency_config.py"):
        shutil.copy2(SCRIPTS / name, sandbox / "scripts" / name)
    shutil.copytree(SCRIPTS / "kernel", sandbox / "scripts" / "kernel", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (sandbox / ".eval-sandbox").write_text("sandbox", encoding="utf-8")
    js, os_root = _roots(sandbox)
    assert Path(js) == sandbox.resolve() and Path(os_root) == sandbox.resolve()


def test_real_department_roots_unchanged():
    from layout import DATA, OS_REAL
    js, os_root = _roots(SCRIPTS.parent)
    assert Path(js) == DATA and Path(os_root) == OS_REAL
