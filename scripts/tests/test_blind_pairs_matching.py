"""blind_pairs paired base-only and variant-only lines by position, so a rewritten bullet that moved down one place was
paired with a different new bullet and the pair was partly unblinded. A moved, rewritten line now finds its base line by
evidence-id overlap, then text similarity, before position; what is left is a real addition or removal, shown one-sided
under a neutral id with the line on a random side, so the judge never learns which page holds it."""
from __future__ import annotations

import json
import re

from test_blind_pairs import A1, A2, A3, bp, page, with_skills, write  # noqa: F401

NEW = "Shipped a pricing service. <!-- ev:ev-z -->"
A2R = "Built the sandboxed harness agents run in. <!-- ev:ev-b -->"        # A2 reworded (same evidence)


def _make(tmp_path, base_lines, var_lines, seed=1):
    base, var = write(tmp_path, "base", page(base_lines)), write(tmp_path, "var", page(var_lines))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=seed)
    return out.read_text(encoding="utf-8"), json.loads(key.read_text(encoding="utf-8"))


def _section(text, pid):
    return text.split(f"## {pid}\n", 1)[1].split("\n## ", 1)[0]


def test_a_moved_rewritten_line_pairs_with_its_base_line_by_evidence(tmp_path):
    text, key = _make(tmp_path, (A1, A2, A3), (A1, NEW, A2R, A3))       # the shape: rewritten, moved down one
    line = _section(text, "LINE Acme Labs 1")
    assert "Built the harness." in line and "sandboxed harness" in line and "pricing service" not in line
    solo = [p for p in key["pairs"] if p.startswith("ONE-SIDED")]
    assert len(solo) == 1 and "pricing service" in _section(text, solo[0])


def test_without_shared_evidence_text_similarity_pairs_before_position(tmp_path):
    b2 = "Built the evaluation harness for agent runs."
    v_new, v2 = "Wrote the onboarding docs.", "Built the evaluation harness for every agent run."
    text, key = _make(tmp_path, (A1, b2, A3), (A1, v_new, v2, A3))
    line = _section(text, "LINE Acme Labs 1")
    assert b2 in line and v2 in line and v_new not in line


def test_one_sided_pairs_hide_which_page_holds_the_line(tmp_path):
    sides = set()
    for seed in range(12):
        text, key = _make(tmp_path, (A1, A2, A3), (A1, A3, NEW), seed=seed)   # A2 removed, NEW added
        solos = [p for p in key["pairs"] if p.startswith("ONE-SIDED")]
        assert len(solos) == 2 and not [p for p in key["pairs"] if p.startswith(("ADDED", "REMOVED"))]
        for pid in solos:
            sec = _section(text, pid)
            assert sec.count("(no line here)") == 1 and not re.search(r"added|removed|base|variant", sec, re.I)
            line_side = "A" if sec.split("**Option B**")[0].count("(no line here)") == 0 else "B"
            sides.add(line_side)
            owner = key["pairs"][pid][line_side]
            assert owner == {"added": "variant", "removed": "base"}[key["kinds"][pid]]
    assert sides == {"A", "B"}                                           # the line is not always on one side


def test_reveal_one_sided(tmp_path):
    text, key = _make(tmp_path, (A1, A2, A3), (A1, A3, NEW), seed=4)
    kpath = tmp_path / "key.json"
    verdicts = {}
    for pid in key["pairs"]:
        if pid.startswith("ONE-SIDED"):
            empty = next(lbl for lbl, who in key["pairs"][pid].items()
                         if who != {"added": "variant", "removed": "base"}[key["kinds"][pid]])
            verdicts[pid] = {"winner": empty, "why": "the page reads better without it"}
    vp = tmp_path / "v.yaml"
    vp.write_text(json.dumps(verdicts))
    rows = {r["pair"]: r for r in bp.reveal(kpath, vp)}
    for pid in verdicts:
        want = {"added": "REVERT", "removed": "KEEP"}[key["kinds"][pid]]     # without the line: drop it / keep the cut
        assert rows[pid]["verdict"] == want and rows[pid]["kind"] == key["kinds"][pid]


def test_a_renamed_skills_row_id_does_not_name_either_side(tmp_path):
    base = write(tmp_path, "base", with_skills([("Agent systems", "agent harnesses, MCP"), ("Evaluation", "golden sets")]))
    var = write(tmp_path, "var", with_skills([("Agent systems", "agent harnesses, MCP"), ("Evals", "golden sets"),
                                              ("Product", "shipped end to end")]))
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(var, base, out, key, seed=2)
    k = json.loads(key.read_text())
    assert "SKILLS renamed 1" in k["pairs"] and not [p for p in k["pairs"] if "Evaluation" in p or "Evals" in p]
    assert [p for p in k["pairs"] if p.startswith("ONE-SIDED Skills")] and k["kinds"]
