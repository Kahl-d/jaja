"""rulings.py: one id per ruling across the registry and the hub files; quotes verbatim; the registry stays valid."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rulings as rl  # noqa: E402

PREFIX = "K" + "-"        # the registry's id prefix, built here so the tests carry no literal id


def rid(n: int | str) -> str:
    return f"{PREFIX}{n}"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "hub" / "lanes").mkdir(parents=True)
    (tmp_path / "hub" / "rulings.yaml").write_text(yaml.safe_dump({"rulings": [
        {"id": rid(1), "date": "2030-01-10", "quote": "nothing goes out", "source": "x", "status": "standing"},
        {"id": rid("165a"), "date": "2030-01-11", "quote": "self-healing retries", "source": "x", "status": "standing"},
    ]}, sort_keys=False), encoding="utf-8")
    return tmp_path


def test_next_id_reads_the_hub_files_too(root):
    assert rl.next_id(root) == rid(166)
    (root / "hub" / "ORCHESTRATOR.md").write_text(f"## {rid(188)} · 2030-01-12 11:51 PDT · THE LOOP\n", encoding="utf-8")
    (root / "hub" / "lanes" / "SETUP.md").write_text(f"### RULING (owner, to this lane; recorded as {rid(189)}, verbatim)\n",
                                                     encoding="utf-8")
    assert rl.next_id(root) == rid(190)


def test_add_appends_verbatim_and_joins_follow_ons(root):
    e = rl.add(root, ["The core idea of the first message ...", "We want a system that can do that."],
               "hub/lanes/SETUP.md", "research", "Reading: x", ["research"], ["role-analyst"], [],
               time="12:10 PDT", date="2030-01-12")
    assert e["id"] == rid(166)
    assert e["quote"] == "The core idea of the first message ... || We want a system that can do that."
    rows = yaml.safe_load((root / "hub" / "rulings.yaml").read_text(encoding="utf-8"))["rulings"]
    assert rows[-1]["id"] == rid(166) and rows[-1]["scope"]["roles"] == ["role-analyst"]
    assert rl.check(root) == []


def test_add_refuses_a_taken_id_an_empty_quote_and_a_bad_topic(root):
    with pytest.raises(ValueError, match="already"):
        rl.add(root, ["x"], "s", "resume", "", [], [], [], rid=rid(1))
    with pytest.raises(ValueError, match="empty quote"):
        rl.add(root, ["  "], "s", "resume", "", [], [], [])
    with pytest.raises(ValueError, match="topic"):
        rl.add(root, ["x"], "s", "nonsense", "", [], [], [])


def test_check_finds_duplicates(root):
    p = root / "hub" / "rulings.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["rulings"].append(dict(data["rulings"][0]))
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert rl.check(root) == [f"{rid(1)}: duplicate id"]
    assert rl.main(["--root", str(root), "check"]) == 1
