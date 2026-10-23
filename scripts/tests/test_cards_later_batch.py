"""After the owner answered the morning cards, a later sweep's new items become a numbered later batch
(cards-<date>-b2.md/.json) instead of a refusal."""
from __future__ import annotations

import importlib.util
import json

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS

spec = importlib.util.spec_from_file_location("js_cards_later", SCRIPTS / "cards.py")
cards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cards)


def card(key: str, n: int = 0, mode: str = "quick") -> dict:
    return {"item_key": key, "n": n, "id": f"C-2026-10-06-{n:03d}", "mode": mode, "kind": "listing", "tier": 3,
            "action": "quick-apply-base", "effort": "E2", "title": key, "why": [], "score": 0, "location_tier": 0}


def test_new_items_after_answered_cards_form_a_numbered_later_batch(tmp_path):
    out = tmp_path
    (out / "cards-2026-10-06.json").write_text(json.dumps({"cards": [card("a", 1), card("b", 2)]}))
    res = {"date": "2026-10-06", "cards": [card("a"), card("b"), card("c"), card("d", mode="deep")]}
    md, js, batch = cards.later_batch(out, res)
    assert md.name == "cards-2026-10-06-b2.md" and js.name == "cards-2026-10-06-b2.json"
    assert [(c["item_key"], c["n"]) for c in batch["cards"]] == [("c", 3), ("d", 4)]
    assert batch["quick"] == 1 and batch["deep"] == 1
    (out / "cards-2026-10-06-b2.json").write_text(json.dumps(batch))
    md3, _, b3 = cards.later_batch(out, dict(res, cards=res["cards"] + [card("e")]))
    assert md3.name == "cards-2026-10-06-b3.md" and [(c["item_key"], c["n"]) for c in b3["cards"]] == [("e", 5)]


def test_nothing_new_writes_nothing(tmp_path):
    (tmp_path / "cards-2026-10-06.json").write_text(json.dumps({"cards": [card("a", 1)]}))
    md, js, _ = cards.later_batch(tmp_path, {"date": "2026-10-06", "cards": [card("a")]})
    assert md is None and js is None
