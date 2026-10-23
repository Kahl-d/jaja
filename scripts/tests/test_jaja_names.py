"""The product is called jaja: JAJA_* variables and ~/.config/jaja, with the earlier job-agency names still accepted."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]


def probe(tmp_path, env_extra, home=None, expr="print(ap.DATA_ROOT, ap.RUNTIME_ROOT, ap.config_dir())"):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JAJA_", "JOB_AGENCY_", "JOB_SEARCH_"))}
    env.update(env_extra)
    env["HOME"] = str(home or tmp_path / "home")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = f"import sys; sys.path.insert(0, {str(CODE / 'scripts')!r}); import agency_paths as ap; {expr}"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_jaja_data_and_runtime_are_read(tmp_path):
    out = probe(tmp_path, {"JAJA_DATA": str(tmp_path / "d"), "JAJA_RUNTIME": str(tmp_path / "r")})
    assert str((tmp_path / "d").resolve()) in out and str((tmp_path / "r").resolve()) in out


def test_the_earlier_job_agency_names_still_work(tmp_path):
    out = probe(tmp_path, {"JOB_AGENCY_DATA": str(tmp_path / "old"), "JOB_AGENCY_RUNTIME": str(tmp_path / "oldrt")})
    assert str((tmp_path / "old").resolve()) in out and str((tmp_path / "oldrt").resolve()) in out


def test_job_search_root_is_still_accepted(tmp_path):
    out = probe(tmp_path, {"JOB_SEARCH_ROOT": str(tmp_path / "legacy")})
    assert str((tmp_path / "legacy").resolve()) in out


def test_the_new_name_wins_over_the_old(tmp_path):
    out = probe(tmp_path, {"JAJA_DATA": str(tmp_path / "new"), "JOB_AGENCY_DATA": str(tmp_path / "old")})
    assert out.split()[0] == str((tmp_path / "new").resolve())


def test_demo_flag_answers_to_both_names(tmp_path):
    for var in ("JAJA_DEMO", "JOB_AGENCY_DEMO"):
        out = probe(tmp_path, {var: "1"})
        assert "examples/demo-data" in out.split()[0]


def test_config_dir_prefers_jaja_and_falls_back_to_the_old_folder(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "job-agency").mkdir(parents=True)
    assert probe(tmp_path, {}, home).endswith(".config/job-agency")          # only the old folder exists
    (home / ".config" / "jaja").mkdir()
    assert probe(tmp_path, {}, home).endswith(".config/jaja")                # both: the new one wins
    assert probe(tmp_path, {"JAJA_CONFIG_DIR": str(tmp_path / "x")}, home).endswith("/x")
    assert probe(tmp_path, {"JOB_AGENCY_CONFIG_DIR": str(tmp_path / "y")}, home).endswith("/y")


def test_a_blank_home_uses_the_new_config_folder(tmp_path):
    assert probe(tmp_path, {}, tmp_path / "empty").endswith(".config/jaja")


def test_plugin_and_marketplace_are_named_jaja():
    import json
    plugin = json.loads((CODE / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((CODE / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["name"] == "jaja" and market["name"] == "jaja" and market["plugins"][0]["name"] == "jaja"
