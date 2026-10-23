"""Growth (a private company at Series D or later, or 500 to 10,000 people) is a segment choice on the dashboard and
a write the segment script accepts. Fixture tree only (test_dashboard_api's)."""
from __future__ import annotations

from test_dashboard_api import NOW, da, tree  # noqa: F401  (the fixture tree and its clock)


def test_the_segment_control_offers_growth(tree):  # noqa: F811
    os_root, js = tree
    assert "growth" in da.build(js, os_root, NOW)["choices"]["segments"]
    res = da.run_write({"op": "segment", "company": "kappa", "segment": "growth"}, js)
    assert res["ok"], res
    assert "segment: growth" in (js / "targets" / "companies.yaml").read_text()


def test_a_scaleup_prospect_votes_growth():
    """prospects.py's company_type 'scaleup' is the growth segment not startup."""
    assert da.PROSPECT_TYPE_SEGMENT["scaleup"] == "growth"
    assert set(da.PROSPECT_TYPE_SEGMENT.values()) <= set(da.SEGMENTS)


def test_a_growth_segment_reads_back_on_the_dashboard(tree):  # noqa: F811
    """A companies.yaml `segment: growth` (the owner's pick included) shows as growth, never replaced by another rule
    such as H-1B volume."""
    os_root, js = tree
    assert da.run_write({"op": "segment", "company": "kappa", "segment": "growth"}, js)["ok"]
    rows = {r["slug"]: r for r in da.build(js, os_root, NOW)["applications"]}
    assert rows["kappa-ml-engineer-202609"]["segment"] == "growth"
    assert da.WATCHLIST_SEGMENT["growth"] == "growth"
