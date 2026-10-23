"""blind_pairs never paired an Experience entry's summary or position line, so a changed summary ("shipped to
production") went through with no verdict. Both are now pairs, blinded like bullets; a summary only one page has is
one-sided."""
from __future__ import annotations

import json

from test_blind_pairs import A1, A2, A3, bp, write  # noqa: F401


def entry(summary=None, position="Founding Engineer", lines=(A1, A2, A3)):
    e = {"company": "Acme Labs", "position": position, "highlights": list(lines)}
    if summary is not None:
        e["summary"] = summary
    return {"cv": {"sections": {"Experience": [e]}}}


def _make(tmp_path, base, var, seed=5):
    out, key = tmp_path / "pairs.md", tmp_path / "key.json"
    bp.make(write(tmp_path, "var", var), write(tmp_path, "base", base), out, key, seed=seed)
    return out.read_text(encoding="utf-8"), json.loads(key.read_text(encoding="utf-8"))


def test_a_changed_summary_is_a_blind_pair_even_when_the_bullets_match(tmp_path):
    base = entry("*AI co-pilot for RF design.* <!-- ev:ev-acme-011 -->")
    var = entry("*AI co-pilot for RF design, shipped to production.* <!-- ev:ev-acme-011 -->")
    text, key = _make(tmp_path, base, var)
    assert set(key["pairs"]["SUMMARY Acme Labs"].values()) == {"base", "variant"}
    sec = text.split("## SUMMARY Acme Labs\n", 1)[1]
    assert "shipped to production" in sec and "AI co-pilot for RF design." in sec and "<!--" not in text
    assert "base" not in text.lower() and "variant" not in text.lower()


def test_a_changed_position_is_a_blind_pair(tmp_path):
    text, key = _make(tmp_path, entry(), entry(position="Founding AI Engineer"))
    assert set(key["pairs"]["POSITION Acme Labs"].values()) == {"base", "variant"}
    assert "Founding AI Engineer" in text


def test_a_summary_only_one_page_has_is_one_sided(tmp_path):
    text, key = _make(tmp_path, entry(), entry("*Added a summary.*"))
    solo = [p for p in key["pairs"] if p.startswith("ONE-SIDED")]
    assert len(solo) == 1 and key["kinds"][solo[0]] == "added" and "Added a summary." in text


def test_identical_summary_and_position_add_no_pair(tmp_path):
    _, key = _make(tmp_path, entry("*Same.*"), entry("*Same.*"))
    assert key["pairs"] == {}
