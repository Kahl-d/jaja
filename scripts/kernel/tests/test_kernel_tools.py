"""redact.py, new-programme.sh, context_budget.py and roots.py: the small tools around the kernel."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import roots as roots_mod

KERNEL = Path(__file__).resolve().parents[1]
CODE = KERNEL.parents[1]
ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/nonexistent"}


def test_redact_replaces_secret_shapes_and_check_mode_never_writes(tmp_path):
    f = tmp_path / "digest.md"
    key, token = "sk-" + "ant-" + "abcdefghijklmnop", "ghp_" + "0123456789abcdefghijklmn"      # built here so no secret-shaped literal is stored
    f.write_text(f"key {key} and token {token} here\nplain line\n", encoding="utf-8")
    check = subprocess.run([sys.executable, str(KERNEL / "redact.py"), "--check", str(f)], capture_output=True, text=True, env=ENV)
    assert check.returncode == 1 and key in f.read_text(encoding="utf-8")
    done = subprocess.run([sys.executable, str(KERNEL / "redact.py"), str(f)], capture_output=True, text=True, env=ENV)
    assert done.returncode == 0
    text = f.read_text(encoding="utf-8")
    assert key not in text and token not in text and "plain line" in text
    assert subprocess.run([sys.executable, str(KERNEL / "redact.py"), "--check", str(f)], env=ENV).returncode == 0


def test_new_programme_scaffolds_a_hub_the_hooks_can_find(tmp_path):
    data = tmp_path / "data"
    run = lambda *extra: subprocess.run(["bash", str(KERNEL / "new-programme.sh"), "--programme", "Example Sprint", "--root", str(data),
                                         "--prefix", "EX", "--branch", "main", *extra], capture_output=True, text=True, env=ENV)
    done = run()
    assert done.returncode == 0, done.stderr
    prog = json.loads((data / "hub" / "programme.json").read_text(encoding="utf-8"))
    assert prog["programme"] == "Example Sprint" and prog["prefix"] == "EX" and prog["shipping_branch"] == "main" and prog["lanes"] == []
    assert (data / "hub" / "ORCHESTRATOR.md").is_file() and (data / "classify.py").is_file()
    assert "skills/lane-orchestration" in done.stdout and "system/" not in done.stdout
    again = run()
    assert again.returncode != 0 and "already exists" in again.stderr                  # never a second hub beside a live one
    assert run("--force").returncode == 0


def test_new_programme_needs_a_name_and_a_root():
    r = subprocess.run(["bash", str(KERNEL / "new-programme.sh")], capture_output=True, text=True, env=ENV)
    assert r.returncode != 0 and "--programme is required" in r.stderr


def test_context_budget_measures_root_lanes_and_a_subagent(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "AGENTS.md").write_text("# rules\n" * 20, encoding="utf-8")
    r = subprocess.run([sys.executable, str(KERNEL / "context_budget.py"), "--json"], capture_output=True, text=True,
                       env={**ENV, "JOB_AGENCY_DATA": str(data), "JOB_AGENCY_RUNTIME": str(tmp_path / "rt")})
    assert r.returncode == 0, r.stderr
    rows = {x["session"]: x for x in json.loads(r.stdout)}
    assert "root" in rows and "subagent:resume-tailor" in rows
    assert {f"lane:{p.name}" for p in (CODE / "lanes").iterdir() if p.is_dir() and (p / "AGENT.md").is_file()} <= set(rows)
    assert rows["root"]["chain"] == len(("# rules\n" * 20).encode()) and rows["root"]["hooks"] == 0     # no programme: no hook output


def test_roots_single_tree_and_configured_forms(tmp_path):
    one = roots_mod.roots(tmp_path)
    assert one.code == one.data == one.runtime == tmp_path.resolve()
    assert roots_mod.roots(one) is one
    cfg = roots_mod.roots(None)
    assert cfg.code == CODE
    import argparse
    ap = argparse.ArgumentParser()
    roots_mod.add_root_args(ap)
    got = roots_mod.from_args(ap.parse_args(["--root", str(tmp_path), "--runtime", str(tmp_path / "rt")]))
    assert got.code == got.data == tmp_path.resolve() and got.runtime == (tmp_path / "rt").resolve()


@pytest.mark.parametrize("script", ["lint.py", "sync.py", "sweep.py", "watchdog.py", "nightly_sweep.py", "approvals.py", "evals.py",
                                    "hub_events.py", "lane_state.py", "rulings_digest.py", "token_ledger.py"])
def test_every_kernel_cli_answers_help_without_touching_anything(script, tmp_path):
    r = subprocess.run([sys.executable, str(KERNEL / script), "--help"], capture_output=True, text=True, env=ENV, cwd=tmp_path)
    assert r.returncode == 0 and "usage" in r.stdout.lower()
    assert list(tmp_path.iterdir()) == []
