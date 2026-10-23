"""blind_pairs.py: the base-comparer sees A and B, never which one is the base, until it has written its verdicts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import blind_pairs as bp  # noqa: E402


def page(acme):
    return {"cv": {"sections": {"Experience": [
        {"company": "Acme Labs", "position": "Founding Engineer", "highlights": list(acme)},
        {"company": "Beta Works", "position": "Founding AI Engineer", "highlights": ["Built the voice agent. <!-- ev:ev-x-1 -->"]},
    ]}}}


def write(tmp_path, name, data):
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


A1, A2, A3 = ("Owned the app end to end. <!-- ev:ev-a -->", "Built the harness. <!-- ev:ev-b -->",
              "Built the Watcher. <!-- ev:ev-c -->")


def test_order_only_difference_becomes_one_order_pair_and_hides_the_base(tmp_path):
    base = write(tmp_path, "base", page((A2, A3, A1)))
    var = write(tmp_path, "var", page((A1, A2, A3)))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=7)
    text = out.read_text(encoding="utf-8")
    assert "base" not in text.lower() and "variant" not in text.lower()
    assert "ORDER Acme Labs" in text and "Option A" in text and "Option B" in text
    assert "<!--" not in text                       # evidence comments stripped: they would leak nothing, but read cleaner
    k = json.loads(key.read_text(encoding="utf-8"))
    assert set(k["pairs"]["ORDER Acme Labs"].values()) == {"base", "variant"}


def test_changed_line_pairs_and_reveal(tmp_path):
    base = write(tmp_path, "base", page((A1, A2)))
    var = write(tmp_path, "var", page((A1, "Built the harness that agents run in safely. <!-- ev:ev-b -->")))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=1)
    k = json.loads(key.read_text(encoding="utf-8"))
    pid = next(p for p in k["pairs"] if p.startswith("LINE"))
    winner_label = next(lbl for lbl, who in k["pairs"][pid].items() if who == "variant")
    verdicts = tmp_path / "verdicts.yaml"
    verdicts.write_text(yaml.safe_dump({pid: {"winner": winner_label, "why": "clearer"}}), encoding="utf-8")
    rows = bp.reveal(key, verdicts)
    assert rows[0]["winner"] == "variant" and rows[0]["verdict"] == "KEEP"
    verdicts.write_text(yaml.safe_dump({pid: {"winner": "tie", "why": "same"}}), encoding="utf-8")
    assert bp.reveal(key, verdicts)[0]["verdict"] == "REVERT"     # a tie keeps the base


def test_identical_pages_produce_no_pairs(tmp_path):
    base = write(tmp_path, "base", page((A1, A2)))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(base, base, out, key, seed=1)
    assert json.loads(key.read_text(encoding="utf-8"))["pairs"] == {}
    assert "no differences" in out.read_text(encoding="utf-8").lower()


def with_skills(rows):
    data = page((A1, A2))
    data["cv"]["sections"]["Skills"] = [{"label": lbl, "details": det} for lbl, det in rows]
    return data


S_AGENT = ("Agent systems", "agent harnesses, MCP <!-- ev:ev-a -->")
S_EVAL = ("Evaluation", "golden task sets, LLM-as-judge rubrics <!-- ev:ev-b -->")


def test_skills_rows_are_paired_blind(tmp_path):
    base = write(tmp_path, "base", with_skills([S_AGENT, S_EVAL]))
    var = write(tmp_path, "var", with_skills([S_AGENT, ("Evaluation", "golden task sets, trajectory grading <!-- ev:ev-b -->"),
                                              ("Product", "shipped end to end")]))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=3)
    k = json.loads(key.read_text(encoding="utf-8"))["pairs"]
    assert set(k["SKILLS Evaluation"].values()) == {"base", "variant"}
    assert set(k["ONE-SIDED Skills a"].values()) == {"base", "variant"}       # neutral id, random side
    assert "SKILLS Agent systems" not in k and "ORDER Skills" not in k
    text = out.read_text(encoding="utf-8")
    assert "Evaluation: golden task sets, trajectory grading" in text and "<!--" not in text
    assert "base" not in text.lower() and "variant" not in text.lower()


def test_skills_reorder_and_rename(tmp_path):
    base = write(tmp_path, "base", with_skills([S_AGENT, S_EVAL]))
    var = write(tmp_path, "var", with_skills([S_EVAL, ("Agents and tools", "agent harnesses, MCP")]))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=5)
    k = json.loads(key.read_text(encoding="utf-8"))["pairs"]
    assert "SKILLS renamed 1" in k
    assert "ORDER Skills" not in k                     # one common row: no order to judge
    var2 = write(tmp_path, "var2", with_skills([S_EVAL, S_AGENT]))
    bp.make(var2, base, out, key, seed=5)
    assert list(json.loads(key.read_text(encoding="utf-8"))["pairs"]) == ["ORDER Skills"]


def test_reorder_with_a_reworded_line_is_still_an_order_pair(tmp_path):
    base = write(tmp_path, "base", page((A2, A3, A1)))
    var = write(tmp_path, "var", page((A1, "Built the harness agents run in. <!-- ev:ev-b -->", A3)))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=2)
    k = json.loads(key.read_text(encoding="utf-8"))["pairs"]
    assert "ORDER Acme Labs" in k and "LINE Acme Labs 1" in k
    var2 = write(tmp_path, "var2", page(("Built the harness agents run in. <!-- ev:ev-b -->", A3, A1)))
    bp.make(var2, base, out, key, seed=2)
    assert "ORDER Acme Labs" not in json.loads(key.read_text(encoding="utf-8"))["pairs"]   # same order, one reword
