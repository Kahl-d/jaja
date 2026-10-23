"""A company moved its board from Greenhouse to Ashby. Its open records still pointed at Greenhouse;
close_missing_records only closed records of the board's own vendor, so they could never close, and the new Ashby
copies would be marked their duplicates. A complete read of a company's current board now closes that company's open
records from another ATS vendor as "board moved"; alert, manual and HN records are untouched."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa


def _rec(rid, source, url, status="fit_checked"):
    return {"id": rid, "company_slug": "hooli", "source": source, "source_url": url, "status": status}


def test_a_complete_read_of_the_new_board_closes_the_old_vendors_open_records():
    existing = {
        "hooli-195": _rec("hooli-195", "greenhouse", "https://boards.greenhouse.io/hoolitech/jobs/1"),
        "hooli-ash": _rec("hooli-ash", "ashby", "https://jobs.ashbyhq.com/hooli/a"),
        "hooli-alert": _rec("hooli-alert", "linkedin-alert", "https://www.linkedin.com/jobs/view/1"),
        "hooli-old": _rec("hooli-old", "greenhouse", "https://boards.greenhouse.io/hoolitech/jobs/2", status="applied"),
    }
    res = fa.BoardResult("hooli", "ashby", all_urls={"https://jobs.ashbyhq.com/hooli/a"}, complete=True)
    closed = fa.close_missing_records(existing, [res], NOW, configured={"hooli": "ashby"})
    assert closed == ["hooli-195"]
    assert existing["hooli-195"]["status"] == "closed" and existing["hooli-195"]["closed_reason"] == "board moved to ashby"
    assert existing["hooli-alert"]["status"] == "fit_checked" and existing["hooli-old"]["status"] == "applied"


def test_an_incomplete_read_closes_nothing():
    existing = {"hooli-195": _rec("hooli-195", "greenhouse", "https://boards.greenhouse.io/hoolitech/jobs/1")}
    res = fa.BoardResult("hooli", "ashby", all_urls=set(), complete=False)
    assert fa.close_missing_records(existing, [res], NOW, configured={"hooli": "ashby"}) == []


def test_an_ad_hoc_read_of_another_vendor_never_closes_the_configured_board():
    """An ad-hoc read (a --board run) of a vendor that is not the company's configured one would have
    closed the whole configured board (a large configured board)."""
    existing = {f"nv-{i}": {"id": f"nv-{i}", "company_slug": "chipco", "source": "workday",
                            "source_url": f"https://chipco.wd5/{i}", "status": "fit_checked"} for i in range(3)}
    res = fa.BoardResult("chipco", "ashby", all_urls=set(), complete=True)
    assert fa.close_missing_records(existing, [res], NOW, configured={"chipco": "workday"}) == []
    assert fa.close_missing_records(existing, [res], NOW) == []                   # no configuration: never
    assert all(r["status"] == "fit_checked" for r in existing.values())


def test_a_queued_or_applied_for_record_is_flagged_for_relinking_not_closed():
    existing = {"q": _rec("q", "greenhouse", "https://boards.greenhouse.io/hoolitech/jobs/3", status="queued"),
                "a": dict(_rec("a", "greenhouse", "https://boards.greenhouse.io/hoolitech/jobs/4"),
                          application="[[hooli-ai-202610]]")}
    res = fa.BoardResult("hooli", "ashby", all_urls=set(), complete=True)
    flagged = []
    assert fa.close_missing_records(existing, [res], NOW, configured={"hooli": "ashby"}, flagged=flagged) == []
    assert sorted(flagged) == ["a", "q"]                                        # reported, never silent
    assert existing["q"]["board_moved_to"] == "ashby" and existing["q"]["status"] == "queued"
    assert existing["a"]["board_moved_to"] == "ashby" and existing["a"]["status"] == "fit_checked"
