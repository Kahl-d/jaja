"""Scout coverage where the owner and the Recruiter look: scout_coverage.py
--write-json saves state/reports/scout-coverage-<date>.json beside the .md (same report, logic unchanged);
dash_api's overview and agency_status read the NEWEST saved JSON (never recomputed per request). Missing is
missing, never zero."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import agency_status  # noqa: E402
import scout_coverage as sc  # noqa: E402
from test_scout_coverage import tree  # noqa: E402  (the fixture tree: one js-scout receipt, 4 targets)


def test_write_json_saves_the_same_report_beside_the_md(tmp_path, capsys):
    root, os_root = tree(tmp_path)
    argv = ["--date", "2026-10-01", "--root", str(root), "--os-root", str(os_root)]
    assert sc.main(argv + ["--write-json"]) == 0
    saved = json.loads((root / "state" / "reports" / "scout-coverage-2026-10-01.json").read_text())
    assert saved == sc.report(date(2026, 10, 1), root, os_root)
    assert sc.main(argv + ["--write", "--write-json"]) == 0
    assert (root / "state" / "reports" / "scout-coverage-2026-10-01.md").is_file()


def _save(root, day, **over):
    r = {"date": day, "coverage": 0.5, "runs": 1, "states": {"read": 1, "capped": 1, "no-reader": 1, "error": 1},
         "new_postings": 2, "new_companies": [{"slug": "newco", "postings": 1}], **over}
    p = root / "state" / "reports" / f"scout-coverage-{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(r))


def test_summary_reads_the_newest_file_and_missing_is_none(tmp_path):
    assert sc.summary(tmp_path) == {"date": None, "coverage": None, "read": None, "capped": None, "no_reader": None,
                                    "new_postings": None, "new_companies": None, "source": None}
    _save(tmp_path, "2026-09-30", coverage=0.1)
    _save(tmp_path, "2026-10-01")
    s = sc.summary(tmp_path)
    assert (s["date"], s["coverage"], s["read"], s["capped"], s["no_reader"], s["new_postings"], s["new_companies"]) \
        == ("2026-10-01", 0.5, 1, 1, 1, 2, 1)
    assert s["source"] == "state/reports/scout-coverage-2026-10-01.json"
    _save(tmp_path, "2026-10-02", coverage=None, runs=0, states={})        # a day with no scout run
    s = sc.summary(tmp_path)
    assert s["coverage"] is None and s["read"] is None and s["date"] == "2026-10-02"


def test_dash_api_overview_carries_the_scout_block_without_recomputing(tmp_path, monkeypatch):
    import dash_api as da
    _save(tmp_path, "2026-10-01")
    monkeypatch.setattr(sc, "report", lambda *a, **k: (_ for _ in ()).throw(AssertionError("recomputed")))
    block = da.scouts(tmp_path)
    assert block["coverage"] == 0.5 and block["new_companies"] == 1
    assert da.scouts(tmp_path / "nowhere")["coverage"] is None


def test_agency_status_has_one_scouts_line(tmp_path):
    _save(tmp_path, "2026-10-01")
    assert agency_status.scouts_line(sc.summary(tmp_path)) == \
        "Scouts: coverage 50% (2026-10-01), 2 new postings, 1 new companies"
    assert agency_status.scouts_line(sc.summary(tmp_path / "nowhere")) == "Scouts: no data yet"
    _save(tmp_path, "2026-10-02", coverage=None, runs=0, states={}, new_postings=3, new_companies=[])
    assert agency_status.scouts_line(sc.summary(tmp_path)) == \
        "Scouts: coverage no scout run (2026-10-02), 3 new postings, 0 new companies"
