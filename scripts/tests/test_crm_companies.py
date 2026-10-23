"""The CRM's Companies page (accounts): one row per company on the watchlist or with an
application or a person, with tier, segment, open roles, applications, contacts and last touch; a row opens the
account (roles, people, its deals). Counts come from the snapshot's own rows, nothing recomputed."""
from __future__ import annotations

import sys
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402

WATCH = [{"name": "Orion Technologies", "slug": "orion", "tier": "strong", "segment": "enterprise"},
         {"name": "Quiet Co", "slug": "quiet", "tier": "good"}]
JOBS = [{"id": "ks-1", "company": "Orion", "company_slug": "orion", "title": "AI R&D Engineer", "status": "fit_checked", "fit": 4.5, "duplicate_of": None},
        {"id": "ks-2", "company": "Orion", "company_slug": "orion", "title": "ML Engineer", "status": "discovered", "fit": None, "duplicate_of": None},
        {"id": "ks-3", "company": "Orion", "company_slug": "orion", "title": "Old role", "status": "closed", "fit": 4.0, "duplicate_of": None},
        {"id": "ks-4", "company": "Orion", "company_slug": "orion", "title": "AI R&D Engineer", "status": "discovered", "fit": None, "duplicate_of": "ks-1"},
        {"id": "nc-1", "company": "Newco", "company_slug": "newco", "title": "AI Engineer", "status": "applied", "fit": 4.2, "duplicate_of": None}]
APPS = [{"slug": "orion-rd", "company": "Orion", "company_slug": "orion", "title": "AI R&D Engineer", "stage": "applied", "segment": "enterprise"},
        {"slug": "newco-ai", "company": "Newco", "company_slug": "newco", "title": "AI Engineer", "stage": "screen", "segment": "startup"}]
PEOPLE = [{"id": "pat", "name": "Pat Quinn Roe", "company_slug": "orion", "warmth": 1, "last_inbound_at": "2026-09-30", "status": "in_conversation"},
          {"id": "zed", "name": "Zed Q", "company_slug": "orion", "warmth": 0, "last_inbound_at": None, "status": "new"}]


def rows():
    return {r["slug"]: r for r in crm.companies(WATCH, JOBS, APPS, PEOPLE)}


def test_an_account_counts_its_open_roles_deals_and_people():
    k = rows()["orion"]
    assert (k["name"], k["tier"], k["segment"]) == ("Orion Technologies", "strong", "enterprise")
    assert k["open_roles"] == 2 and k["applications"] == 1 and k["contacts"] == 2   # closed and duplicate roles left out
    assert k["last_touch"] == "2026-09-30" and [p["name"] for p in k["people"]] == ["Pat Quinn Roe", "Zed Q"]
    assert [r["id"] for r in k["roles"]] == ["ks-1", "ks-2"] and k["deals"][0]["stage"] == "applied"


def test_a_company_off_the_watchlist_with_a_deal_is_an_account():
    n = rows()["newco"]
    assert n["tier"] is None and n["segment"] == "startup" and n["applications"] == 1 and n["open_roles"] == 0
    assert n["last_touch"] is None


def test_order_puts_live_deals_first():
    assert [r["slug"] for r in crm.companies(WATCH, JOBS, APPS, PEOPLE)][:2] == ["newco", "orion"]   # a screen beats applied
    assert rows()["quiet"]["open_roles"] == 0 and rows()["quiet"]["contacts"] == 0


def test_last_touch_is_a_pacific_date():
    people = [{"id": "k", "name": "K", "company_slug": "kestrel", "warmth": 0, "last_inbound_at": "2026-09-28T02:10:12Z"},
              {"id": "j", "name": "J", "company_slug": "kestrel", "warmth": 0, "last_inbound_at": "2026-09-26"}]
    k = {r["slug"]: r for r in crm.companies([], [], [], people)}["kestrel"]
    assert k["last_touch"] == "2026-09-27"                            # 02:10 UTC on 09-28 is 09-27 in the owner's time zone
