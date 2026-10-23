"""hooks/rulings_common.py (the digest the start hooks print) and scripts/kernel/rulings_digest.py (the CLI over it)."""
import subprocess
import sys
from pathlib import Path

import pytest

import agency_config as cfg
import rulings_common as rc

KERNEL = Path(__file__).resolve().parents[1]


def _rows():
    return [
        {"id": "R-4", "date": "2026-09-01", "lanes": ["all"], "roles": ["all"], "line": "never send", "pinned": True},
        {"id": "R-90", "date": "2026-10-06", "lanes": ["apply"], "roles": [], "line": "apply order", "pinned": False},
        {"id": "R-91", "date": "2026-10-05", "lanes": ["all"], "roles": [], "line": "all order", "pinned": False},
        {"id": "R-10", "date": "2026-09-02", "lanes": ["apply"], "roles": [], "line": "old order", "pinned": False},
    ]


def test_digest_pins_first_recent_lane_specific_then_count():
    out = rc.digest(_rows(), lane="apply", today="2026-10-07").splitlines()
    assert out[1].startswith("- R-4 (pinned)")
    assert out[2].startswith("- R-90 (10-06)") and out[3].startswith("- R-91")
    assert "1 more standing for apply" in out[-1]  # R-10 is older than 14 days


def test_digest_respects_cap():
    out = rc.digest(_rows(), lane="apply", today="2026-10-07", cap=150)
    assert "R-90" not in out and "more standing" in out


def test_digest_drops_expired_orders():
    rows = _rows() + [{"id": "R-95", "date": "2026-10-06", "lanes": ["apply"], "roles": [], "line": "day order",
                       "pinned": False, "expires": "2026-10-06"}]
    assert "R-95" not in rc.digest(rows, lane="apply", today="2026-10-07")


def test_pins_file_decides_what_is_pinned(tmp_path):
    (tmp_path / "rulings.yaml").write_text(
        "rulings:\n- {id: R-1, date: '2026-10-01', status: standing, reading: one, scope: {lanes: [all], roles: [all]},"
        " enforced_by: script}\n- {id: R-2, date: '2026-10-02', status: standing, reading: two, scope: {lanes: [apply]}}\n")
    assert [r["id"] for r in rc.index_rows(tmp_path / "rulings.yaml") if r["pinned"]] == ["R-1"]
    (tmp_path / "rulings-pins.yaml").write_text("pins: [R-2]\n")
    assert [r["id"] for r in rc.index_rows(tmp_path / "rulings.yaml") if r["pinned"]] == ["R-2"]


# ---------------------------------------------------------------- the render budget (a start print is capped)
def long_rows(n: int, words: int = 120) -> list[dict]:
    return [{"id": f"R-{i}", "date": "2026-10-01", "quote": "word " * words} for i in range(1, n + 1)]


def test_a_long_scope_fits_the_budget_with_the_footer_and_the_also_line():
    text = rc.render(long_rows(200), "Rulings for lane recruiter")
    assert len(text) <= rc.BUDGET
    assert text.endswith("never relayed as a paraphrase.")
    also = next(line for line in text.splitlines() if line.startswith("Also standing"))
    shown = [line.split(" ")[1] for line in text.splitlines() if line.startswith("- R-")]
    listed = also.split(": ", 1)[1].split(", ")
    assert set(shown) | set(listed) == {f"R-{i}" for i in range(1, 201)} and not set(shown) & set(listed)
    assert shown[0] == "R-200"                                   # newest first; the oldest go to the also line


def test_a_short_scope_prints_every_ruling_and_no_also_line():
    text = rc.render(long_rows(3, 10), "Rulings")
    assert "Also standing" not in text and text.count("\n- R-") == 3


# ---------------------------------------------------------------- lane aliases come from the config
def row(rid, lanes):
    return {"id": rid, "status": "standing", "scope": {"lanes": lanes}, "line": rid, "date": "2026-10-07", "pinned": False}


@pytest.fixture
def aliases():
    cfg.override({"policy": {"lane_aliases": {"hunt": ["scout", "posts"], "merged": ["resume", "research"]}}})
    yield
    cfg.reload()


def test_a_merged_lane_inherits_the_rulings_of_the_lanes_it_absorbed(aliases):
    rows = [row("R-1", ["resume"]), row("R-2", ["research"]), row("R-3", ["hunt"]), row("R-4", ["all"])]
    assert {r["id"] for r in rc.standing_for(rows, lane="MERGED")} == {"R-1", "R-2", "R-4"}


def test_hunt_inherits_scout_and_posts_but_not_apply(aliases):
    rows = [row("R-1", ["scout"]), row("R-2", ["posts"]), row("R-3", ["apply"])]
    assert {r["id"] for r in rc.standing_for(rows, lane="hunt")} == {"R-1", "R-2"}


def test_digest_counts_inherited_rulings(aliases):
    rows = [{**row("R-1", ["resume"]), "scope": None, "lanes": ["resume"], "roles": []},
            {**row("R-2", ["other"]), "scope": None, "lanes": ["other"], "roles": []}]
    assert "1 standing for lane merged" in rc.digest(rows, lane="merged", today="2026-10-07")


def test_without_aliases_a_lane_gets_only_its_own_rulings():
    assert {r["id"] for r in rc.standing_for([row("R-1", ["resume"]), row("R-2", ["hunt"])], lane="hunt")} == {"R-2"}


# ---------------------------------------------------------------- the plugin-scoped agent name
def test_lane_type_from_agent_strips_the_plugin_name():
    assert rc.lane_type_from_agent("lane-resume") == "resume"
    assert rc.lane_type_from_agent("jaja:lane-resume") == "resume"
    assert rc.lane_type_from_agent("jaja:fit-scorer") is None and rc.lane_type_from_agent(None) is None


# ---------------------------------------------------------------- registries and the CLI
def test_the_registry_is_hub_rulings_yaml_in_the_data_folder(tmp_path, monkeypatch):
    (tmp_path / "hub").mkdir()
    import lane_common
    monkeypatch.setattr(rc, "WORKSPACE", tmp_path)
    monkeypatch.setattr(lane_common, "WORKSPACE", tmp_path)
    assert rc.registries() == []                                 # absent is fine: an empty registry
    (tmp_path / "hub" / "rulings.yaml").write_text("rulings: []\n")
    assert rc.registries() == [tmp_path / "hub" / "rulings.yaml"]


def test_the_cli_builds_the_index_shows_a_ruling_and_prints_a_digest(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "rulings.yaml").write_text(
        "rulings:\n- {id: R-1, date: '2026-10-01', status: standing, quote: 'send nothing without a yes', reading: no sends,\n"
        "   scope: {lanes: [all], roles: [all]}, enforced_by: script}\n"
        "- {id: R-2, date: '2026-10-02', status: standing, reading: apply order, scope: {lanes: [apply]}}\n")

    def run(*args):
        return subprocess.run([sys.executable, str(KERNEL / "rulings_digest.py"), *args, "--hub", str(hub)],
                              capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})

    built = run("build")
    assert built.returncode == 0 and "2 standing, 1 pinned" in built.stdout and (hub / "rulings.index.json").is_file()
    shown = run("show", "R-1", "R-9")
    assert 'send nothing without a yes' in shown.stdout and "R-9: not found" in shown.stdout
    assert "R-1 (pinned)" in run("digest", "--lane", "apply", "--all").stdout
    assert "pinned" in run("stats").stdout
