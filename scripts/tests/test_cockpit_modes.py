"""Modes: every cockpit action and pipeline row carries the company's segment and tier,
and the cockpit names each working mode (dream, startups, big companies, referrals, outreach, portals, posts) with its
rule and its counts, so the page can switch the whole workbench to what the owner works on that day."""
from __future__ import annotations

import sys
from pathlib import Path

from disc_harness import *  # noqa: F401,F403
from test_cockpit import NOW, RATES, _build, _snap

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import cockpit  # noqa: E402


def _snap_with_companies():
    s = _snap()
    s["crm"]["companies"] = [{"slug": "acme", "name": "Acme", "tier": "dream", "segment": "ai-lab"},
                             {"slug": "northwind", "name": "Northwind", "tier": "good", "segment": "startup"},
                             {"slug": "globex", "name": "Globex", "tier": "strong", "segment": "big-tech"}]
    s["applications"] = [{"slug": "z", "company": "Zed", "segment": "enterprise", "tier": "mass"}]
    s["portals"] = {"portals": [{"name": "Wellfound"}, {"name": "YC"}]}
    return s


def test_every_action_and_deal_carries_segment_and_tier(tmp_path):
    c = _build(tmp_path, snap=_snap_with_companies())
    acts = c["next"] + c["more"]
    by = {a["what"]: a for a in acts}
    prep = next(a for a in acts if a["kind"] == "prep")
    assert prep["company"] == "Northwind" and prep["segment"] == "startup" and prep["tier"] == "good"
    follow = next(a for a in acts if a["kind"] == "follow up")
    assert follow["company"] == "Acme" and follow["segment"] == "ai-lab" and follow["tier"] == "dream"
    send = next(a for a in acts if a["kind"] == "send")
    assert send["company"] == "Globex" and send["segment"] == "big-tech"
    reply = next(a for a in acts if a["kind"] == "reply")
    assert reply["segment"] is None and reply["tier"] is None                 # mail without a company says so, never guesses
    deals = {d["slug"]: d for d in c["pipeline"]}
    assert deals["a"]["segment"] == "ai-lab" and deals["a"]["tier"] == "dream"
    assert deals["t"]["segment"] == "startup"
    assert "by" not in by  # (keeps the dict used)


def test_modes_name_their_rule_and_count_what_they_cover(tmp_path):
    c = _build(tmp_path, snap=_snap_with_companies())
    modes = {m["id"]: m for m in c["modes"]}
    assert [m["id"] for m in c["modes"]] == ["everything", "dream", "startups", "big", "referrals", "outreach", "portals", "posts"]
    n = len(c["next"] + c["more"])
    assert modes["everything"]["actions"] == n and modes["everything"]["deals"] == len(c["pipeline"])
    assert modes["dream"]["actions"] == sum(a["tier"] == "dream" for a in c["next"] + c["more"]) >= 1
    assert modes["startups"]["deals"] == 1 and modes["big"]["actions"] >= 1
    assert modes["referrals"]["actions"] == sum(a["kind"] in ("people first", "send", "decide") for a in c["next"] + c["more"])
    assert modes["outreach"]["actions"] == sum(a["kind"] in ("reply", "send", "decide") for a in c["next"] + c["more"])
    assert modes["portals"]["wired"] is True and modes["portals"]["count"] == 2 and modes["portals"]["tab"] == "portals"
    assert modes["posts"]["wired"] is False and "state/linkedin" in modes["posts"]["source"]
    for m in c["modes"]:
        assert m["label"] and m["rule"]                                       # every mode says what it means
