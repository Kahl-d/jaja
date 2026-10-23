"""install.sh in a throwaway HOME: demo install ends with doctor green, a re-run changes nothing, the hook is ours."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required for install.sh")


def env_for(home: Path, **extra):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    for k in ("JAJA_DATA", "JOB_SEARCH_ROOT", "JAJA_CONFIG", "JAJA_DEMO", "JAJA_CONFIG_DIR",
              "JAJA_RUNTIME"):
        env.pop(k, None)
    env.update(extra)
    return env


def install(root: Path, *args, home: Path, **extra):
    return subprocess.run([BASH, str(root / "install.sh"), *args], capture_output=True, text=True,
                          env=env_for(home, **extra), cwd=str(root), timeout=900, stdin=subprocess.DEVNULL)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_demo_install_ends_with_doctor_green(home):
    r = install(CODE, "--demo", "--yes", "--no-hook", home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    data = home / "jaja-demo"
    assert (data / "jobs" / "acme-corp-100.md").is_file()
    cfg = (home / ".config" / "jaja" / "config.yaml").read_text(encoding="utf-8")
    assert cfg.strip() == f"data_root: {data.resolve()}"
    out = r.stdout
    assert 'claude plugin marketplace add "' in out
    assert "claude plugin install jaja@jaja --scope user" in out
    assert "== Doctor" in out and " 0 fail" in out
    assert "[FAIL]" not in out


def test_rerun_is_idempotent(home):
    assert install(CODE, "--demo", "--yes", "--no-hook", "--skip-doctor", home=home).returncode == 0
    data = home / "jaja-demo"
    note = data / "profile" / "VOICE.md"
    note.write_text("my own words\n", encoding="utf-8")
    r = install(CODE, "--demo", "--yes", "--no-hook", "--skip-doctor", home=home)
    assert r.returncode == 0
    assert "created 0 file(s)" in r.stdout and "skipped (exists)" in r.stdout
    assert note.read_text(encoding="utf-8") == "my own words\n"


def test_blank_install_to_a_chosen_folder_keeps_other_config_keys(home, tmp_path):
    cfg = home / ".config" / "jaja" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    (tmp_path / "rt").mkdir()
    cfg.write_text("runtime_root: " + str(tmp_path / "rt") + "\n", encoding="utf-8")
    mine = tmp_path / "mine"
    r = install(CODE, "--yes", "--no-hook", "--data", str(mine), home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    text = cfg.read_text(encoding="utf-8")
    assert f"data_root: {mine.resolve()}" in text and "runtime_root:" in text
    assert (mine / "profile" / "facts.yaml").is_file() and not (mine / "jobs" / "acme-corp-100.md").exists()
    # a blank folder is a valid, if empty, install: doctor has no FAIL
    assert "[FAIL]" not in r.stdout


def test_usage_errors(home):
    assert install(CODE, "--bogus", home=home).returncode == 2
    assert install(CODE, "--data", home=home).returncode == 2
    r = install(CODE, "--help", home=home)
    assert r.returncode == 0 and "--demo" in r.stdout


@pytest.fixture
def mini_repo(tmp_path):
    """Just enough of the repo for install.sh --skip-doctor, with a .git folder to hold the hook."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(CODE / "install.sh", root / "install.sh")
    (root / "tools").mkdir()
    for name in ("init-data.py", "pre-commit-leak.sh"):
        shutil.copy(CODE / "tools" / name, root / "tools" / name)
    shutil.copy(CODE / ".gitleaks.toml", root / ".gitleaks.toml")
    shutil.copytree(CODE / "templates" / "data", root / "templates" / "data")
    shutil.copytree(CODE / "config" / "defaults.d", root / "config" / "defaults.d")
    shutil.copytree(CODE / "examples" / "demo-data", root / "examples" / "demo-data")
    (root / ".git" / "hooks").mkdir(parents=True)
    return root


def test_hook_is_installed_once_and_is_executable(mini_repo, home):
    r = install(mini_repo, "--demo", "--yes", "--skip-doctor", home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    hook = mini_repo / ".git" / "hooks" / "pre-commit"
    text = hook.read_text(encoding="utf-8")
    assert "# jaja leak guard" in text and "pre-commit-leak.sh" in text
    assert hook.stat().st_mode & stat.S_IXUSR
    before = text
    install(mini_repo, "--demo", "--yes", "--skip-doctor", home=home)
    assert hook.read_text(encoding="utf-8") == before


def test_a_foreign_hook_is_left_alone(mini_repo, home):
    hook = mini_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    r = install(mini_repo, "--demo", "--yes", "--skip-doctor", home=home)
    assert r.returncode == 0
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert "left alone" in r.stdout


def test_the_hook_blocks_a_leak_and_passes_clean_files(mini_repo, tmp_path):
    """pre-commit-leak.sh on a real throwaway git repo: pattern-only mode flags a home path, passes plain text."""
    if shutil.which("git") is None:
        pytest.skip("git is required")
    shutil.copy(CODE / "tools" / "leak_check.py", mini_repo / "tools" / "leak_check.py")
    shutil.rmtree(mini_repo / ".git")
    g = lambda *a: subprocess.run(["git", "-C", str(mini_repo), *a], check=True, capture_output=True,
                                  env=dict(os.environ, HOME=str(tmp_path), GIT_CONFIG_GLOBAL="/dev/null"))
    g("init", "-q")
    (mini_repo / "notes.txt").write_text("nothing personal here\n")
    g("add", "notes.txt")
    env = env_for(tmp_path)
    ok = subprocess.run([BASH, str(mini_repo / "tools" / "pre-commit-leak.sh")], capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "pattern checks only" in ok.stderr
    (mini_repo / "bad.txt").write_text("see /Us" + "ers/someone/Documents/secret.txt\n")
    g("add", "bad.txt")
    bad = subprocess.run([BASH, str(mini_repo / "tools" / "pre-commit-leak.sh")], capture_output=True, text=True, env=env)
    assert bad.returncode == 1 and "bad.txt:1: home-path" in bad.stdout
