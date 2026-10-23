"""conftest.py: tests marked real_records run in the real tree and are skipped inside an eval sandbox (a `.eval-sandbox`
marker next to the scripts/ copy, the same signal gmail_auth.config_dir uses), so the corpus-keeper card's command
works unchanged in both places (SETUP-H3, 2026-10-01)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SAMPLE = '''import pytest


@pytest.mark.real_records
def test_pins_real_records():
    assert True


def test_plain():
    assert True
'''


def _tree(tmp_path, sandbox):
    for src in TESTS.parent.glob("*.py"):                 # the scripts/ copy an eval sandbox gets (held_safe needs them)
        (tmp_path / "scripts").mkdir(exist_ok=True)
        shutil.copy(src, tmp_path / "scripts" / src.name)
    tests = tmp_path / "scripts" / "tests"
    tests.mkdir(parents=True)
    for name in ("conftest.py", "held_safe.py"):
        shutil.copy(TESTS / name, tests / name)
    (tests / "test_sample.py").write_text(SAMPLE)
    if sandbox:
        (tmp_path / ".eval-sandbox").write_text("/somewhere/system/scripts")
    return tests


def _run(tests):
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests)],
                         capture_output=True, text=True, cwd=tests.parent.parent)
    return out.stdout.strip().splitlines()[-1]


def test_real_records_run_in_the_real_tree(tmp_path):
    assert "2 passed" in _run(_tree(tmp_path, sandbox=False))


def test_real_records_are_skipped_in_an_eval_sandbox(tmp_path):
    line = _run(_tree(tmp_path, sandbox=True))
    assert "1 passed" in line and "1 skipped" in line
