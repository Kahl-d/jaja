"""The routine catalogue (routines/*.yaml): valid, watchable, disabled by default, pointing at things that exist.

These read the real repo: they guard the specs a friend installs from. A friend's data folder is never touched.
"""
import datetime
import shlex
from pathlib import Path

import pytest

import frontmatter as fm
import lint
import watchdog as wd

REPO = Path(__file__).resolve().parents[3]


def specs() -> dict[str, dict]:
    return {str(r["meta"].get("name")): r for r in wd.load_routines(REPO)}


def role_names() -> set[str]:
    return {str(fm.load(p)[0].get("name") or p.stem) for p in (REPO / "agents").glob("*.md") if p.name.upper() != "README.MD"}


def lane_types() -> set[str]:
    return {p.name for p in (REPO / "lanes").iterdir() if p.is_dir()}


NAMES = sorted(specs())


def test_there_are_routines_and_every_spec_is_schema_valid():
    assert NAMES, "no routine specs found in routines/"
    assert [str(f) for f in lint.check_schemas(REPO) if f.path.startswith("routines/")] == []
    for name in NAMES:
        assert specs()[name]["path"] == f"routines/{name}.yaml"


def test_every_shipped_routine_is_disabled():
    """Nothing runs until the owner switches it on (and, for a model routine, names who said so in enabled_by)."""
    on = [n for n in NAMES if specs()[n]["meta"].get("enabled") is not False]
    assert on == []
    report = wd.evaluate(REPO, datetime.datetime(2026, 9, 25, 10, 30))
    assert report["state"] == "IDLE" and report["counts"]["enabled"] == 0


@pytest.mark.parametrize("name", NAMES)
def test_each_spec_carries_what_an_install_needs(name):
    meta = specs()[name]["meta"]
    wd.parse_cron(meta["schedule"])
    for key in ("purpose", "owner", "runs_on", "engine", "action", "tier", "notify", "budget"):
        assert meta.get(key) not in (None, "", [], {}), key
    assert "inputs" in meta and "outputs" in meta     # a retired routine may list none
    assert isinstance(meta.get("connectors"), list) and isinstance(meta.get("enabled"), bool)
    assert set(meta["notify"]) == {"on", "via"}
    assert meta["owner"] in role_names() | lane_types() | {"owner"}, meta["owner"]
    if meta["engine"] == "script":
        assert meta["budget"]["max_tokens"] == 0


@pytest.mark.parametrize("name", NAMES)
def test_each_action_points_at_something_that_exists(name):
    kind, _, target = str(specs()[name]["meta"]["action"]).partition(":")
    if kind == "skill":
        assert (REPO / "skills" / target / "SKILL.md").is_file(), target
    elif kind == "role":
        assert target in role_names()
    else:
        assert kind == "script"
        assert (REPO / shlex.split(target)[0]).is_file(), target


def test_no_routine_carries_a_send_capable_connector():
    send_words = ("send", "compose", "post", "publish")
    for routine in specs().values():
        for connector in routine["meta"].get("connectors") or []:
            assert not any(w in str(connector).lower() for w in send_words), f"{routine['path']} carries {connector}"
