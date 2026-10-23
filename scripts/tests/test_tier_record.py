"""An application's tier (dream, focused, mass) is a recorded field set only through
`tracker.py tier <slug> <tier> --source "<why>"`; agency_status and strategy_review read it, then the newest
apply-batch sheet, then strategy_review.infer_tier marked "(inferred)". Company joins go through company_index
(exact slug, name or alias, never fuzzy), so "Northwind" finds "Northwind Design Systems" (slug northwind)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import agency_status  # noqa: E402
import strategy_review as sr  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402
from test_strategy_review import TODAY, app, js, w  # noqa: E402,F401  (the fixture world)

NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)


def _meta(root, slug):
    return fm.load(root / "applications" / slug / "application.md")[0]


def test_the_setter_records_the_tier_and_why(js):
    out = tr.set_tier(js, "acme-z", "focused", "inferred by the tier rule, backfill", NOW)
    assert out == {"application": "acme-z", "from": None, "to": "focused"}
    meta = _meta(js, "acme-z")
    assert meta["tier"] == "focused" and meta["tier_source"] == "inferred by the tier rule, backfill"


@pytest.mark.parametrize("tier,source", [("giant", "x"), ("dream", ""), ("dream", "   ")])
def test_the_setter_refuses_an_unknown_tier_or_no_reason(js, tier, source):
    before = (js / "applications" / "acme-z" / "application.md").read_bytes()
    with pytest.raises(tr.TrackerError):
        tr.set_tier(js, "acme-z", tier, source, NOW)
    assert (js / "applications" / "acme-z" / "application.md").read_bytes() == before


def test_dry_run_and_cli(js, capsys):
    before = (js / "applications" / "acme-z" / "application.md").read_bytes()
    assert tr.main(["--root", str(js), "tier", "acme-z", "mass", "--source", "why", "--dry-run"], now=NOW) == 0
    assert (js / "applications" / "acme-z" / "application.md").read_bytes() == before
    assert "acme-z: tier None -> mass (dry-run" in capsys.readouterr().out
    assert tr.main(["--root", str(js), "tier", "acme-z", "mass", "--source", "why"], now=NOW) == 0
    assert tr.main(["--root", str(js), "tier", "nope", "mass", "--source", "why"], now=NOW) == 2
    tr.main(["--root", str(js), "project"], now=NOW)                    # a re-projection keeps the field
    assert _meta(js, "acme-z")["tier"] == "mass"


def test_strategy_review_reads_the_recorded_tier_first(js):
    tr.set_tier(js, "orbital-x", "focused", "the owner's word", NOW)    # its batch sheet says dream
    rows = {r["application"]: r for r in sr.collect(js, TODAY)}
    assert rows["orbital-x"]["tier"] == "focused"
    assert rows["acme-z"]["tier"] == "focused (inferred)"               # no field, no sheet: the rule, marked


def test_a_short_company_name_joins_its_watchlist_entry_by_slug(js):
    w(js / "targets" / "companies.yaml", """
        companies:
          - {name: Orbital Labs, slug: orbital, tier: dream, ats_vendor: greenhouse}
          - {name: "Northwind Design Systems", slug: northwind, tier: dream, ats_vendor: workday}
        """)
    app(js, "northwind-a", "Northwind", "Senior AI Engineer", None, [("2026-09-30T00:04:07Z", "applied")])
    row = next(r for r in sr.collect(js, TODAY) if r["application"] == "northwind-a")
    assert row["tier"] == "dream (inferred)" and row["dream_list"] is True
    assert row["applied"] == date(2026, 9, 29)                          # 17:04 PDT: the local day


def test_agency_status_counts_recorded_and_inferred_tiers(js):
    tr.set_tier(js, "acme-z", "focused", "the owner's word", NOW)
    tiers = agency_status.funnel_and_tiers(js, TODAY)[1]
    assert tiers["focused"]["applied"] == 1 and tiers["untiered"]["applied"] == 0
    tr.set_tier(js, "orbital-x", "dream", "x", NOW)
    app(js, "zeta-q", "Zeta", "AI Engineer", 4.6, [("2026-09-30T18:00:00Z", "applied")])
    tiers = agency_status.funnel_and_tiers(js, TODAY)[1]
    assert tiers["dream"]["inferred"] == 1 and tiers["untiered"]["applied"] == 0


def test_a_backfilled_tier_still_reads_as_inferred(js):
    tr.set_tier(js, "acme-z", "focused", "inferred by the tier rule, backfill", NOW)
    rows = {r["application"]: r for r in sr.collect(js, TODAY)}
    assert rows["acme-z"]["tier"] == "focused (inferred)"
    tiers = agency_status.funnel_and_tiers(js, TODAY)[1]
    assert tiers["focused"]["inferred"] == 1 and tiers["focused"]["applied"] == 0
