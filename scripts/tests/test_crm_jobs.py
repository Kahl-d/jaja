"""The CRM's Jobs page, Fresh 10 first (order: Home, Pipeline, Jobs). Rows come from
fresh10.pick computed once per snapshot build (never per request); each shows expected interviews with the measured
rates' n, STRETCH and stopped marks, and the people report's candidates (state/daily/<date>-fresh10-people.md) as
"to verify". Fixtures and an injected pick only."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402

NOW = datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)          # 2026-10-01 22:30 local
RATES = {"warm": 0.26, "cold": 0.03, "warm_n": 2, "warm_k": 2, "cold_n": 24, "cold_k": 0}


def fake_pick(root, now):
    return {"date": "2026-10-01", "hours": 72, "counts": {"jobs": 5285, "fresh": 423, "stopped": 1}, "rates": RATES,
            "rows": [{"id": "pinecrest-ai-764be5bb", "company": "Pinecrest AI", "title": "Forward Deployed Engineer", "expected": 0.032,
                      "posted": "2026-10-01 13:17", "fit": None, "hunt": 70, "years": None, "stretch": False, "warm": [],
                      "lens": "forward-deployed", "apply_url": "https://jobs.ashbyhq.com/lumaai/x", "next": "score first"},
                     {"id": "orchard-100126-0337", "company": "Apple", "title": "Sr. Machine Learning Engineer", "expected": 0.016,
                      "posted": "2026-10-01 09:34", "fit": 4.1, "hunt": 72, "years": 4, "stretch": True, "warm": ["Ana Q"],
                      "lens": "applied-ml-and-nlp", "apply_url": "https://jobs.apple.com/x", "next": "role package"}],
            "candidates": 30}


PEOPLE = """---
type: report
---
| # | role (job id) | candidate | role or tie | source URL | why them | confidence |
|---|---|---|---|---|---|---|
| 6 | Pinecrest AI · Forward Deployed Engineer (`pinecrest-ai-764be5bb`) | Pinecrest blog authors: **Lia Chen**, **Omar Reyes** | authors | https://pinecrest.example.test/blog | blog | to verify |
| 5 | Apple · Sr. Machine Learning Engineer (`orchard-100126-0337`) | none found | | | the JD names no one | none |
"""


def tree(tmp_path, people=True, stopped=False):
    js = tmp_path / "js"
    if people:
        (js / "state" / "daily").mkdir(parents=True)
        (js / "state" / "daily" / "2026-10-01-fresh10-people.md").write_text(PEOPLE, encoding="utf-8")
    if stopped:
        (js / "state" / "recruiter").mkdir(parents=True, exist_ok=True)
        (js / "state" / "recruiter" / "stopped.yaml").write_text("stopped:\n  - job: orchard-100126-0337\n    by: orchestrator\n")
    return js


def test_rows_keep_the_pick_order_with_expected_interviews_and_the_rates_n(tmp_path):
    j = crm.jobs(tree(tmp_path), NOW, pick=fake_pick)
    assert [r["id"] for r in j["rows"]] == ["pinecrest-ai-764be5bb", "orchard-100126-0337"]
    assert j["rows"][0]["expected_text"] == "3.2%" and j["rates_text"] == "warm 2 of 2 (small sample), cold 0 of 24"
    assert j["source"].startswith("fresh10.pick") and j["date"] == "2026-10-01"


def test_stretch_and_stopped_marks(tmp_path):
    rows = {r["id"]: r for r in crm.jobs(tree(tmp_path, stopped=True), NOW, pick=fake_pick)["rows"]}
    assert rows["orchard-100126-0337"]["marks"] == ["STRETCH", "STOPPED"]
    assert rows["pinecrest-ai-764be5bb"]["marks"] == []


def test_people_report_candidates_show_as_to_verify(tmp_path):
    rows = {r["id"]: r for r in crm.jobs(tree(tmp_path), NOW, pick=fake_pick)["rows"]}
    assert rows["pinecrest-ai-764be5bb"]["to_verify"] == ["Lia Chen", "Omar Reyes"]
    assert rows["orchard-100126-0337"]["to_verify"] == []            # "none found" names no one
    assert rows["orchard-100126-0337"]["warm"] == ["Ana Q"]


def test_no_people_report_is_missing_not_empty(tmp_path):
    j = crm.jobs(tree(tmp_path, people=False), NOW, pick=fake_pick)
    assert j["people_report"] is None and all(r["to_verify"] is None for r in j["rows"])


def test_a_failed_pick_is_missing_not_zero(tmp_path):
    def broken(root, now):
        raise RuntimeError("jobs unreadable")
    j = crm.jobs(tree(tmp_path), NOW, pick=broken)
    assert j["rows"] is None and "jobs unreadable" in j["error"]


def test_warm_paths_show_names_not_prospect_ids(tmp_path):
    def pick(root, now):
        res = fake_pick(root, now)
        res["rows"][1]["warm"] = ["tess-morgan", "unknown-id"]
        return res
    rows = {r["id"]: r for r in crm.jobs(tree(tmp_path), NOW, pick=pick, names={"tess-morgan": "Tess Morgan"})["rows"]}
    assert rows["orchard-100126-0337"]["warm"] == ["Tess Morgan", "unknown-id"]
