"""scripts/doctor.py: green on the demo data, red where it should be, never prints a token value."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[2]
DOCTOR = CODE / "scripts" / "doctor.py"
DEMO = CODE / "examples" / "demo-data"


def run_doctor(*args, home, env_extra=None):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    for k in ("JAJA_DATA", "JOB_SEARCH_ROOT", "JAJA_CONFIG", "JAJA_DEMO", "JAJA_CONFIG_DIR"):
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(DOCTOR), *map(str, args)], capture_output=True, text=True, env=env,
                          cwd=str(CODE), timeout=600)


@pytest.fixture(scope="module")
def demo_report(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    r = run_doctor("--data", DEMO, "--json", home=home)
    return r, json.loads(r.stdout)


def by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_demo_data_is_green(demo_report):
    r, report = demo_report
    assert r.returncode == 0, r.stdout
    assert report["summary"]["fail"] == 0
    checks = by_name(report)
    for name in ("python", "data-root", "data-folders", "config-values", "profile-files", "evidence-ids", "records",
                 "lint", "adapters", "hooks", "plugin-manifest", "routines"):
        assert checks[name]["state"] in ("ok", "warn"), checks[name]
    assert checks["hooks"]["state"] == "ok"
    assert checks["routines"]["state"] == "ok"
    assert "demo data" in checks["data-root"]["message"]


def test_connectors_are_not_configured_not_failed(demo_report):
    _, report = demo_report
    c = by_name(report)["connectors"]
    assert c["state"] == "ok" and "not configured" in c["message"]


def test_text_output_one_line_per_check(tmp_path):
    r = run_doctor("--data", DEMO, home=tmp_path)
    assert r.returncode == 0
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("[")]
    assert len(lines) >= 18
    assert all(ln[1:5].strip() in ("ok", "warn", "FAIL", "skip") for ln in lines)
    assert "doctor: " in r.stdout.splitlines()[-1]


def test_no_data_folder_fails_with_a_hint(tmp_path):
    r = run_doctor("--fix-hints", home=tmp_path)
    assert r.returncode == 1
    assert "[FAIL] data-root" in r.stdout
    assert "init-data.py" in r.stdout


def test_missing_data_dir_fails(tmp_path):
    r = run_doctor("--data", tmp_path / "nope", "--json", home=tmp_path)
    assert r.returncode == 1
    assert by_name(json.loads(r.stdout))["data-root"]["state"] == "fail"


def test_incomplete_data_folder_names_the_missing_pieces(tmp_path):
    data = tmp_path / "d"
    (data / "profile").mkdir(parents=True)
    r = run_doctor("--data", data, "--json", home=tmp_path)
    checks = by_name(json.loads(r.stdout))
    assert r.returncode == 1
    assert checks["data-folders"]["state"] == "fail" and "jobs" in checks["data-folders"]["message"]
    assert checks["profile-files"]["state"] == "fail"


def test_a_broken_config_file_fails(tmp_path):
    data = tmp_path / "d"
    subprocess.run([sys.executable, str(CODE / "tools" / "init-data.py"), str(data), "--quiet"], check=True,
                   env=dict(os.environ, HOME=str(tmp_path)))
    (data / "config" / "style.yaml").write_text("style: [unclosed\n", encoding="utf-8")
    r = run_doctor("--data", data, "--json", home=tmp_path)
    assert by_name(json.loads(r.stdout))["config-values"]["state"] == "fail"
    assert r.returncode == 1


def test_connector_tokens_are_listed_by_name_only(tmp_path):
    cdir = tmp_path / ".config" / "jaja"
    cdir.mkdir(parents=True)
    secret = "SECRET-TOKEN-VALUE-do-not-print"
    tok = cdir / "gmail_token_read.json"
    tok.write_text(json.dumps({"refresh_token": secret}), encoding="utf-8")
    tok.chmod(0o600)
    r = run_doctor("--data", DEMO, "--json", home=tmp_path)
    assert secret not in r.stdout + r.stderr
    c = by_name(json.loads(r.stdout))["connectors"]
    assert c["state"] == "ok" and "gmail_token_read.json" in c["message"]
    tok.chmod(0o644)
    r = run_doctor("--data", DEMO, "--json", home=tmp_path)
    c = by_name(json.loads(r.stdout))["connectors"]
    assert c["state"] == "warn" and "chmod 600" in c["hint"]
    assert secret not in r.stdout


# ---- the checks that need a different code root are exercised directly
@pytest.fixture
def doctor_mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("doctor_under_test", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "doctor_under_test", mod)      # dataclasses looks the module up by name
    spec.loader.exec_module(mod)
    mod.CHECKS.clear()
    monkeypatch.setattr(mod, "CODE_ROOT", tmp_path)
    return mod


def test_enabled_routine_is_a_warning(doctor_mod, tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "a.yaml").write_text("name: a\nenabled: false\n")
    (tmp_path / "routines" / "b.yaml").write_text("name: b\nenabled: true\n")
    doctor_mod.check_routines()
    c = doctor_mod.CHECKS[-1]
    assert c.state == "warn" and "b" in c.message


def test_hooks_json_naming_a_missing_file_fails(doctor_mod, tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/gone.py"'}]}]}}))
    doctor_mod.check_hooks(dict(os.environ))
    c = doctor_mod.CHECKS[-1]
    assert c.state == "fail" and "gone.py" in c.message


def test_a_crashing_hook_fails(doctor_mod, tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "boom.py").write_text("raise SystemExit(3)\n")
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/boom.py"'}]}]}}))
    doctor_mod.check_hooks(dict(os.environ))
    assert doctor_mod.CHECKS[-1].state == "fail" and "rc=3" in doctor_mod.CHECKS[-1].message


def test_plugin_manifest_must_point_at_real_folders(doctor_mod, tmp_path):
    base = tmp_path / ".claude-plugin"
    base.mkdir()
    (base / "plugin.json").write_text(json.dumps({"name": "x", "version": "1", "description": "d", "skills": "./skills"}))
    (base / "marketplace.json").write_text(json.dumps({"name": "m", "plugins": [{"name": "x"}]}))
    doctor_mod.check_plugin()
    assert doctor_mod.CHECKS[-1].state == "fail" and "./skills" in doctor_mod.CHECKS[-1].message
