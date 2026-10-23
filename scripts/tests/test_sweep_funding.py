"""Just raised (agency charter 0.1, the finding mandate: "just raised, hiring before posting" leads): Form D raises from
scripts/funding_signals.py appear in the morning sweep once each and become cards (a watchlist company in tier 0).
Tiny fixture trees; no network."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _load("recruiter_sweep", "js_sweep_funding")
cards = _load("cards", "js_cards_funding")
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


def signal(adsh: str, company: str, day: str, **kw) -> dict:
    return {"company": company, "cik": "1", "form": "D", "file_date": day, "adsh": adsh, "amount": kw.get("amount"),
            "amount_status": "stated" if kw.get("amount") else "indefinite", "industry_group": "Other Technology",
            "location": kw.get("location", "San Francisco, CA"), "link": f"https://www.sec.gov/{adsh}-index.htm",
            "matched_by": ["keyword:AI"], "target": kw.get("target", False), "target_slug": kw.get("slug"),
            "dossier": False, "open_jobs": kw.get("open_jobs", 0)}


def write_signals(root: Path, day: str, signals: list[dict]) -> None:
    folder = root / "state" / "signals"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"funding-{day}.json").write_text(json.dumps({"signals": signals}), encoding="utf-8")


def roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    root, os_root = tmp_path / "js", tmp_path / "os"
    (root / "targets").mkdir(parents=True)
    (os_root / "work" / "approvals").mkdir(parents=True)
    return root, os_root, root / "state" / "recruiter"


def test_raises_are_listed_once_and_old_ones_left_out(tmp_path):
    root, os_root, out = roots(tmp_path)
    write_signals(root, "2026-10-04", [signal("a-1", "Orbit Labs Inc.", "2026-09-28", amount=247488419, target=True,
                                              slug="orbit", open_jobs=18),
                                       signal("a-2", "Meadow AI, Inc.", "2026-10-01", amount=3000000)])
    write_signals(root, "2026-10-05", [signal("a-2", "Meadow AI, Inc.", "2026-10-01", amount=3500000),   # newer wins
                                       signal("a-3", "Old Raise AI", "2026-09-01", amount=1000000)])
    res = sweep.build(root, os_root, NOW, "2026-10-05", out)
    got = {r["key"]: r for r in res["groups"]["funding"]}
    assert set(got) == {"funding:a-1", "funding:a-2"} and got["funding:a-2"]["amount"] == 3500000
    assert res["left_out"]["funding"]["older raise"] == 1
    assert any(x.startswith("Just raised") and x.endswith(": 2") for x in sweep.summary(res))
    text = sweep.render(res)
    assert "### Just raised (2)" in text and "$247,488,419" in text and "on the watchlist" in text
    sweep.write(res, out, NOW)
    again = sweep.build(root, os_root, NOW, "last", out)
    assert again["groups"]["funding"] == [] and again["left_out"]["funding"]["already listed"] == 2


def test_no_signal_file_says_no_data_yet(tmp_path):
    root, os_root, out = roots(tmp_path)
    res = sweep.build(root, os_root, NOW, "2026-10-05", out)
    assert res["groups"]["funding"] is None
    assert "Just raised (Form D, scripts/funding_signals.py): no data yet" in sweep.summary(res)


def test_cards_put_a_watchlist_raise_in_tier_0_and_the_rest_in_tier_3(tmp_path):
    root, os_root, out = roots(tmp_path)
    write_signals(root, "2026-10-05", [signal("a-1", "Orbit Labs Inc.", "2026-09-28", amount=247488419, target=True,
                                              slug="orbit", open_jobs=18),
                                       signal("a-2", "Meadow AI, Inc.", "2026-10-01")])
    res = json.loads(json.dumps(sweep.build(root, os_root, NOW, "2026-10-05", out), default=str))
    deck = {c["item_key"]: c for c in cards.build(root, res)["cards"]}
    orbit, meadow = deck["funding:a-1"], deck["funding:a-2"]
    assert orbit["tier"] == 0 and orbit["action"] == "research" and orbit["title"].startswith("[just raised] Orbit Labs")
    assert "18 open jobs on file" in orbit["why"]
    assert meadow["tier"] == 3 and "amount not stated" in meadow["why"][0]
