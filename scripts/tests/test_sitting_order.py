"""sitting_order.py: the ONBOARDING sittings ranked by expected interviews per hour of the owner's time."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sitting_order as so  # noqa: E402

from layout import JS  # noqa: E402
RATES = {"warm": 0.25, "cold": 0.03}

INDEX = """# Portal sittings

| # | sitting | sheet | minutes | why this place |
|---|---|---|---|---|
| 1 | Board A | boarda.md | 10 | alerts |
| 2 | Gmail re-login | gmail.md | 3 | mail |
| 3 | Founders | founders.md | 5 | founders write first |
| 4 | Workday, per target company | workday-candidate-home.md | 3 each | big-company alerts |
"""

PLATFORMS = """platforms:
- {id: boarda, control: alert-mail, presence: {kind: job-board}}
- {id: founders, control: alert-mail, presence: {kind: founder-matching}}
- {id: workday-candidate-home, control: alert-mail}
"""

COMPANIES = """companies:
- {slug: a, ats_vendor: workday, tier: dream}
- {slug: b, ats_vendor: workday, tier: good}
- {slug: c, ats_vendor: workday, tier: fallback}
- {slug: d, ats_vendor: greenhouse, tier: dream}
"""


def _root(tmp_path: Path) -> Path:
    (tmp_path / "state" / "portal-sittings").mkdir(parents=True)
    (tmp_path / "targets").mkdir()
    (tmp_path / "state" / "portal-sittings" / "INDEX.md").write_text(INDEX, encoding="utf-8")
    (tmp_path / "targets" / "platforms.yaml").write_text(PLATFORMS, encoding="utf-8")
    (tmp_path / "targets" / "companies.yaml").write_text(COMPANIES, encoding="utf-8")
    return tmp_path


def test_parses_index_and_workday_minutes(tmp_path):
    rows = so.sittings(_root(tmp_path))
    assert [r["name"] for r in rows] == ["Board A", "Gmail re-login", "Founders", "Workday, per target company"]
    assert rows[1]["sheets"] == ["gmail"]
    assert rows[3]["minutes"] == 6          # 3 each x the 2 dream or good Workday targets; fallback does not count


def test_measured_yield_beats_prior_and_shrinks_queue_share():
    measured = so.platform_value("boarda", {"kind": "job-board"}, {"new": 300, "queued": 30}, RATES, days=30)
    prior = so.platform_value("boarda", {"kind": "job-board"}, {"new": 2, "queued": 0}, RATES, days=30)
    assert measured["source"] == "measured" and prior["source"] == "prior"
    assert measured["roles_wk"] == 70.0      # 300 new in 30 days
    # queue share (30 + 10 x 0.05) / (300 + 10), not the raw 10 %
    assert abs(measured["queue_share"] - 30.5 / 310) < 1e-9


def test_linkedin_export_counts_warm_paths():
    v = so.platform_value("linkedin", {"presence": {"kind": "profile-network"}}, {}, RATES)
    plain = so.platform_value("other", {"presence": {"kind": "profile-network"}}, {}, RATES)
    assert abs((v["interviews_wk"] - plain["interviews_wk"]) - 1.0 * (0.25 - 0.03)) < 1e-3


def test_gmail_is_pinned_first_and_founders_outrank_a_board(tmp_path):
    res = so.order(_root(tmp_path), RATES, yields={})
    names = [r["name"] for r in res["rows"]]
    assert names[0] == "Gmail re-login" and res["rows"][0]["gate"]
    assert names.index("Founders") < names.index("Board A")   # warm inbound per hour beats cold alerts per hour
    for r in res["rows"][1:]:
        assert r["per_hour"] >= 0
    hours = [r["per_hour"] for r in res["rows"][1:]]
    assert hours == sorted(hours, reverse=True)


def test_render_names_priors_and_the_rates(tmp_path):
    text = so.render(so.order(_root(tmp_path), RATES, yields={}))
    assert "stated prior" in text and "warm 25%" in text and "cold 3%" in text
    assert "| 1 | Gmail re-login" in text


@pytest.mark.real_records
def test_live_index_every_sitting_ranked():
    res = so.order(JS, RATES)
    assert len(res["rows"]) == len(so.sittings(JS)) >= 20
    assert res["rows"][0]["sheets"] == ["gmail"]


def test_done_sittings_leave_the_order(tmp_path):
    root = _root(tmp_path)
    res = so.order(root, RATES, yields={}, status={"founders": "alert set", "boarda": "to create"})
    assert [r["name"] for r in res["done"]] == ["Founders"]
    assert "Founders" not in [r["name"] for r in res["rows"]]
    assert "**Done (no sitting needed):** Founders (founders alert set)" in so.render(res)
    assert "Portal status not read" in so.render(so.order(root, RATES, yields={}))


def test_read_status_gmail_token_and_linkedin_export(monkeypatch, tmp_path):
    import gmail_auth
    import network_map
    import portal_sitting
    monkeypatch.setattr(portal_sitting, "status", lambda root: {"linkedin": "tracker linked", "dice": "manual only",
                                                                "wellfound": "tracker linked"})
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "platforms.yaml").write_text(
        "platforms:\n- {id: wellfound, presence: {kind: founder-matching}}\n", encoding="utf-8")
    monkeypatch.setattr(gmail_auth, "token_status", lambda kind: {"exists": True, "needs_reauth": False, "days_left": 6})
    monkeypatch.setattr(network_map, "latest_export", lambda root: None)
    st = so.read_status(tmp_path)
    assert st["gmail"] == "token ok" and st["linkedin"] == "export pending" and st["dice"] == "manual only"
    assert st["wellfound"] == "profile unverified"          # alerts arrive, but no sitting recorded the profile
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "accounts.yaml").write_text("accounts:\n- {platform: wellfound}\n", encoding="utf-8")
    assert so.read_status(tmp_path)["wellfound"] == "profile unverified"   # an account row alone proves no profile
    (tmp_path / "state" / "accounts.yaml").write_text(
        "accounts:\n- {platform: wellfound, profile_filled_on: '2026-10-02'}\n", encoding="utf-8")
    assert so.read_status(tmp_path)["wellfound"] == "tracker linked"
    monkeypatch.setattr(gmail_auth, "token_status", lambda kind: {"exists": True, "needs_reauth": False, "days_left": 1})
    monkeypatch.setattr(network_map, "latest_export", lambda root: tmp_path / "x.jsonl")
    st = so.read_status(tmp_path)
    assert st["gmail"] == "to create" and st["linkedin"] == "tracker linked"


def test_manual_only_sittings_are_optional(tmp_path):
    res = so.order(_root(tmp_path), RATES, yields={}, status={"boarda": "manual only", "founders": "to create"})
    assert [r["name"] for r in res["optional"]] == ["Board A"]
    assert "Board A" not in [r["name"] for r in res["rows"]]
    assert "**Optional (every sheet is manual only" in so.render(res)


def test_review_fixes_manual_alerts_prior_share_and_block(tmp_path):
    manual = so.platform_value("vc-portfolio", {}, {}, RATES, state="manual only")
    assert manual["interviews_wk"] == 0
    arriving = so.platform_value("wellfound", {"presence": {"kind": "founder-matching"}}, {}, RATES,
                                 state="profile unverified")
    assert arriving["roles_wk"] == 0 and arriving["interviews_wk"] == round(0.5 * 0.25, 4)
    res = so.order(_root(tmp_path), dict(RATES, warm_k=2, warm_n=2, cold_k=0, cold_n=25), yields={},
                   status={"boarda": "to create", "founders": "to create", "workday-candidate-home": "to create"})
    text = so.render(res)
    assert "Measured reply rates" not in text and "a rejection does not count" in text and "from warm 2 of 2 and cold 0 of 25" in text
    assert "pulled toward a stated prior of 1 in 20" in text and "the rest rank on stated priors of roles" in text and "How far to trust this order" in text and '"was #"' in text
    assert "stated prior only" in text and "measured 0" not in text


def test_first_block_line_and_export_before_presence(monkeypatch, tmp_path):
    root = _root(tmp_path)
    (root / "state" / "portal-sittings" / "INDEX.md").write_text(INDEX.replace("| founders.md | 5 |", "| founders.md | 80 |"), encoding="utf-8")
    text = so.render(so.order(root, RATES, yields={}, status={}))
    assert "**First block ends here:** rows 1 to" in text
    import gmail_auth
    import network_map
    import portal_sitting
    monkeypatch.setattr(portal_sitting, "status", lambda r: {"linkedin": "tracker linked"})
    monkeypatch.setattr(gmail_auth, "token_status", lambda kind: {"exists": False})
    monkeypatch.setattr(network_map, "latest_export", lambda r: None)
    (root / "targets" / "platforms.yaml").write_text(
        "platforms:\n- {id: linkedin, presence: {kind: profile-network}}\n", encoding="utf-8")
    assert so.read_status(root)["linkedin"] == "export pending"


def test_presence_scope_matches_the_writer(monkeypatch, tmp_path):
    # presence_surfaces count as presence portals, and a "no sitting" state is never
    # downgraded to "profile unverified" (only alert set and tracker linked are)
    import gmail_auth
    import network_map
    import portal_sitting
    monkeypatch.setattr(portal_sitting, "status", lambda r: {"introbot": "tracker linked", "himalayas": "no sitting"})
    monkeypatch.setattr(gmail_auth, "token_status", lambda kind: {"exists": False})
    monkeypatch.setattr(network_map, "latest_export", lambda r: None)
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "platforms.yaml").write_text(
        "platforms:\n- {id: himalayas, presence: {kind: job-board}}\npresence_surfaces:\n- {id: introbot}\n",
        encoding="utf-8")
    st = so.read_status(tmp_path)
    assert st["introbot"] == "profile unverified" and st["himalayas"] == "no sitting"
