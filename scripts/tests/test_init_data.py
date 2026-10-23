"""tools/init-data.py: a data folder skeleton, the demo fill, no overwrites, the config pointer."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CODE = Path(__file__).resolve().parents[2]
TOOL = CODE / "tools" / "init-data.py"
DEFAULTS = CODE / "config" / "defaults.d"


def run_tool(*args, home, env_extra=None, check=True):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env.pop("JOB_AGENCY_CONFIG", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True, env=env)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def script(name, *args, data, home, runtime=None):
    env = dict(os.environ, HOME=str(home), JOB_AGENCY_DATA=str(data), PYTHONDONTWRITEBYTECODE="1",
               JOB_AGENCY_RUNTIME=str(runtime or Path(data) / "runtime"))
    env.pop("JOB_AGENCY_DEMO", None)
    return subprocess.run([sys.executable, str(CODE / "scripts" / name), *map(str, args)], capture_output=True,
                          text=True, env=env, cwd=str(CODE))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def skeleton(tmp_path, home):
    data = tmp_path / "data"
    run_tool(data, home=home)
    return data


def merged_defaults():
    out: dict = {}

    def merge(base, extra):
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = v

    for f in sorted(DEFAULTS.glob("*.yaml")):
        merge(out, yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    return out


def test_skeleton_has_every_folder_and_file(skeleton):
    for d in ("profile/corpus", "profile/answers", "resumes/bases", "applications", "prospects", "jobs", "jobs-pending",
              "state", "graph", "touches", "outbox", "pursuits", "hub/lanes", "hub/briefs", "targets", "views", "config",
              "runtime/work/approvals", "runtime/logs/runs", "runtime/vault", "legal"):
        assert (skeleton / d).is_dir(), d
    for f in ("README.md", "profile/facts.yaml", "profile/evidence.yaml", "profile/PROFILE.md", "profile/VOICE.md",
              "profile/skills-taxonomy.yaml", "profile/corpus/README.md", "hub/programme.json", "hub/registry.json",
              "hub/rulings.yaml", "hub/events.jsonl", "legal/timers.md", "config/identity.yaml", "config/style.yaml",
              "config/policy.yaml", "targets/roles.md", "targets/fit-gates.yaml", "sources.yaml",
              "views/jobs.base", "views/people.base"):
        assert (skeleton / f).is_file(), f
    assert (skeleton / "hub" / "events.jsonl").read_text() == ""


def test_config_files_list_every_default_key_commented_out(skeleton):
    """Uncomment the three files and they equal the merged defaults; as written they set nothing."""
    expect = merged_defaults()
    for section in ("identity", "style", "policy"):
        text = (skeleton / "config" / f"{section}.yaml").read_text(encoding="utf-8")
        assert (yaml.safe_load(text) or {}) == {}, f"{section}.yaml must set nothing until edited"
        live = "\n".join(re.sub(r"^# ?", "", ln) for ln in text.splitlines() if ln.startswith("# ") or ln == "#")
        body = "\n".join(ln for ln in live.splitlines() if not ln.startswith(("identity.yaml:", "style.yaml:", "policy.yaml:")))
        got = yaml.safe_load(_strip_prose(body)) or {}
        assert got.get(section) == expect[section], section


def _strip_prose(text: str) -> str:
    """Drop the guide's own sentences (they sit before the first top-level key)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^(identity|style|policy):\s*$", ln):
            return "\n".join(lines[i:])
    return text


def test_views_have_no_placeholder(skeleton):
    for f in (skeleton / "views").glob("*.base"):
        assert "{{" not in f.read_text(encoding="utf-8"), f.name
    assert 'file.inFolder("jobs")' in (skeleton / "views" / "jobs.base").read_text(encoding="utf-8")


def test_vault_root_prefixes_the_views(tmp_path, home):
    vault = tmp_path / "vault"
    data = vault / "career" / "search"
    run_tool(data, "--vault-root", vault, home=home)
    assert 'file.inFolder("career/search/jobs")' in (data / "views" / "jobs.base").read_text(encoding="utf-8")


def test_never_overwrites_and_reports_skips(skeleton, home):
    facts = skeleton / "profile" / "facts.yaml"
    facts.write_text("identity: {full_name: Mine}\n", encoding="utf-8")
    r = run_tool(skeleton, home=home)
    assert facts.read_text(encoding="utf-8") == "identity: {full_name: Mine}\n"
    assert "skipped (exists): profile/facts.yaml" in r.stdout
    assert "created 0 file(s)" in r.stdout


def test_skeleton_passes_the_record_checks_and_builds_a_dashboard(skeleton, home):
    r = script("check_records.py", "--all", "--root", skeleton, data=skeleton, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    r = script("check_evidence.py", data=skeleton, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    r = script("dashboard.py", "--dry-run", "--today", "2026-10-20", data=skeleton, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Job search dashboard" in r.stdout


def test_demo_fill_is_complete_and_valid(tmp_path, home):
    data = tmp_path / "demo"
    run_tool(data, "--demo", home=home)
    assert len(list((data / "jobs").glob("*.md"))) == 7
    assert len(list((data / "applications").glob("*/application.md"))) == 3
    assert len(list((data / "prospects").glob("*.md"))) == 5
    assert (data / "runtime" / "work" / "approvals" / "A-2026-0001.md").is_file()
    r = script("check_records.py", "--all", "--root", data, data=data, home=home)
    assert r.returncode == 0, r.stdout + r.stderr
    r = script("check_evidence.py", data=data, home=home)
    assert r.returncode == 0, r.stdout + r.stderr


def test_set_config_writes_data_root_and_keeps_other_keys(tmp_path, home):
    cfg = home / ".config" / "job-agency" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("runtime_root: /somewhere/else\n# a comment\ndata_root: /old/place\n", encoding="utf-8")
    data = tmp_path / "data"
    run_tool(data, "--set-config", home=home)
    lines = cfg.read_text(encoding="utf-8").splitlines()
    assert f"data_root: {data.resolve()}" in lines
    assert "runtime_root: /somewhere/else" in lines and "# a comment" in lines
    assert sum(1 for ln in lines if ln.startswith("data_root:")) == 1
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"


def test_config_untouched_without_the_flag(tmp_path, home):
    run_tool(tmp_path / "data", home=home)
    assert not (home / ".config" / "job-agency" / "config.yaml").exists()


def test_set_config_honours_job_agency_config(tmp_path, home):
    cfg = tmp_path / "elsewhere.yaml"
    run_tool(tmp_path / "data", "--set-config", home=home, env_extra={"JOB_AGENCY_CONFIG": str(cfg)})
    assert cfg.read_text(encoding="utf-8").startswith("data_root: ")


def test_refuses_a_folder_inside_the_repo(home):
    r = run_tool(CODE / "my-private-data", home=home, check=False)
    assert r.returncode == 2 and "inside the repo" in r.stderr
    assert not (CODE / "my-private-data").exists()


def test_refuses_a_file(tmp_path, home):
    f = tmp_path / "afile"
    f.write_text("x")
    assert run_tool(f, home=home, check=False).returncode == 2
