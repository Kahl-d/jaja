"""prospect_set sends every hand-set score field through scripts/prospects.py set (J7a fix 3).

Before, the tool wrote warmth, fit_score and sponsorship_hint directly into the record: no lock (the next
`score` run could overwrite them), no date, and score_reasons kept the script's old numbers. prospects.py set
is the single writer for manual prospect changes; it locks, dates (locked_on) and marks each value.
"""
from __future__ import annotations

import pytest

from job_search_mcp import store as st


@pytest.fixture
def s(tree):
    return st.Store.from_env()


def load(path):
    import js_common as jc
    return jc.read_record(path)


def test_hand_set_score_fields_are_locked_dated_and_marked(s, tree):
    out = s.prospect_set("bob-agency", fields={"warmth": 3, "fit_score": 4.0, "sponsorship_hint": "yes"})
    assert out["via"].startswith("scripts/prospects.py set")
    assert out["changed"] == ["fit_score", "sponsorship_hint", "warmth"]
    meta, _ = load(tree["js"] / "prospects" / "bob-agency.md")
    assert (meta["warmth"], meta["fit_score"], meta["sponsorship_hint"]) == (3, 4.0, "yes")
    assert {"warmth", "fit_score", "sponsorship_hint"} <= set(meta["locked_fields"])
    assert {k: str(v) for k, v in meta["locked_on"].items()} == {
        "warmth": "2026-09-25", "fit_score": "2026-09-25", "sponsorship_hint": "2026-09-25"}
    assert meta["score_reasons"] == ["warmth (set manually 2026-09-25: 3)", "fit_score (set manually 2026-09-25: 4.0)",
                                     "sponsorship_hint (set manually 2026-09-25: yes)"]


def test_an_unchanged_value_is_not_resent(s, tree):
    out = s.prospect_set("bob-agency", fields={"warmth": 1})           # the record already says 1
    assert out["changed"] == [] and out["note"] == "nothing to change"
    meta, _ = load(tree["js"] / "prospects" / "bob-agency.md")
    assert "locked_fields" not in meta and "locked_on" not in meta


def test_hand_set_fields_at_creation_are_locked_too(s, tree):
    """A prospect created with hand-set scores keeps them: the next `score` run must not overwrite them."""
    out = s.prospect_set("new-guy", fields={"name": "New Guy", "source": "outbound", "warmth": 3, "priority": "P0"},
                         create=True)
    assert out["created"] is True and out["via"].startswith("scripts/prospects.py set")
    path = tree["js"] / "prospects" / "new-guy.md"
    meta, _ = load(path)
    assert (meta["warmth"], meta["priority"], meta["name"], meta["source"]) == (3, "P0", "New Guy", "outbound")
    assert "warmth" in meta["locked_fields"] and meta["priority_locked"] is True
    assert str(meta["locked_on"]["warmth"]) == "2026-09-25"
    import subprocess, sys
    subprocess.run([sys.executable, str(st.SCRIPTS / "prospects.py"), "--root", str(tree["js"]), "--today", "2026-09-26",
                    "score"], check=True, capture_output=True, text=True)
    meta, _ = load(path)
    assert (meta["warmth"], meta["priority"]) == (3, "P0")


def test_creation_without_hand_set_fields_still_writes_directly(s, tree):
    out = s.prospect_set("plain-new", fields={"name": "Plain New", "source": "outbound", "company": "Acme"}, create=True)
    assert out["created"] is True
    meta, _ = load(tree["js"] / "prospects" / "plain-new.md")
    assert meta["status"] == "new" and "locked_fields" not in meta
