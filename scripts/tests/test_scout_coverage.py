"""scout_coverage.py: the day's checked share of the known job surface, and what was found."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scout_coverage as sc  # noqa: E402

RECEIPT = """# js-scout
## Run 2026-10-01T20:11:29Z · status ok

| stage | result |
|---|---|
| fetch | by_vendor {} |

Errors:
- roadwise (greenhouse): GET https://boards-api.greenhouse.io/v1/boards/roadwise/jobs: HTTP 404

Notes:
- bigsearch: unsupported ats_vendor 'other' (skipped)
- netco: workday listed 400 of 1357 (page cap)
"""


def tree(tmp_path, receipt=RECEIPT):
    root, os_root = tmp_path / "js", tmp_path / "os"
    (root / "targets").mkdir(parents=True)
    (root / "jobs").mkdir()
    (root / "targets" / "companies.yaml").write_text(yaml.safe_dump({"companies": [
        {"slug": s, "ats_vendor": v} for s, v in
        (("roadwise", "greenhouse"), ("bigsearch", "other"), ("netco", "workday"), ("quillco", "greenhouse"))]}), encoding="utf-8")
    (root / "sources.yaml").write_text(yaml.safe_dump({"sources": [{"id": "greenhouse", "kind": "ats-json",
                                                                    "enabled": True, "last_run": "2026-10-01"}]}))
    for jid, slug, seen, gate in (("quillco-1", "quillco", "2026-10-01T23:11:31Z", "pass"),
                                  ("newco-1", "newco", "2026-10-02T02:00:00Z", "review"),     # 19:00 PDT on 10-01
                                  ("old-1", "quillco", "2026-09-20T10:00:00Z", "pass")):
        (root / "jobs" / f"{jid}.md").write_text(
            f"---\ncompany_slug: {slug}\nsource: greenhouse\nfirst_seen: '{seen}'\ngate: {gate}\n---\nx\n", encoding="utf-8")
    runs = os_root / "logs" / "runs" / "js-scout"
    runs.mkdir(parents=True)
    if receipt:
        (runs / "2026-10-01.md").write_text(receipt, encoding="utf-8")
    return root, os_root


def test_states_coverage_and_finds(tmp_path):
    root, os_root = tree(tmp_path)
    r = sc.report(date(2026, 10, 1), root, os_root)
    assert r["states"] == {"error": 1, "no-reader": 1, "capped": 1, "read": 1}
    assert r["coverage"] == 0.5 and r["no_reader_by_vendor"] == {"other": 1}
    assert r["new_postings"] == 2 and r["new_passing_gates"] == 2      # the local (PDT) day, not the UTC one
    assert r["new_companies"] == [{"slug": "newco", "postings": 1}]


def test_a_day_without_a_run_says_no_run_never_zero(tmp_path):
    root, os_root = tree(tmp_path, receipt="")
    r = sc.report(date(2026, 10, 1), root, os_root)
    assert r["coverage"] is None and r["states"] == {"no run": 4}
    assert "no run" in sc.render(r)


def test_one_clean_read_in_the_day_counts_as_read(tmp_path):
    second = RECEIPT + "\n## Run 2026-10-01T23:11:31Z · status ok\n\n| fetch | by_vendor {} |\n\nNotes:\n- x: y\n"
    root, os_root = tree(tmp_path, receipt=second)
    assert sc.company_state("roadwise", sc.runs_of(date(2026, 10, 1), os_root))[0] == "read"


def test_no_sources_file_means_no_feed_rows_not_a_crash(tmp_path):
    root, os_root = tree(tmp_path)
    (root / "sources.yaml").unlink()
    assert sc.report(date(2026, 10, 1), root, os_root)["feeds"] == []


def test_deferred_or_resting_boards_are_not_read(tmp_path):
    # H1 CS83 finding (b): a board the run budget deferred, or the slow lane rested, was counted as read
    receipt = RECEIPT.replace("- netco: workday listed 400 of 1357 (page cap)\n",
                              "- netco: workday listed 400 of 1357 (page cap)\n"
                              "- deferred to the next run (run budget 20 min): quillco, zeta\n")
    root, os_root = tree(tmp_path, receipt=receipt)
    runs = sc.runs_of(date(2026, 10, 1), os_root)
    assert sc.company_state("quillco", runs)[0] == "deferred"
    second = receipt + ("\n## Run 2026-10-01T23:11:31Z · status ok\n\n| fetch | by_vendor {} |\n\nNotes:\n"
                        "- slow lane (nothing new on the last 2 runs at 60 s or more each; read once in 20 h): quillco. "
                        "Recruiter: these boards cost time and gave no new posting\n")
    root, os_root = tree(tmp_path / "b", receipt=second)
    assert sc.company_state("quillco", sc.runs_of(date(2026, 10, 1), os_root))[0] == "deferred"
    r = sc.report(date(2026, 10, 1), root, os_root)
    assert r["states"].get("deferred") == 1 and r["coverage"] == 0.5    # roadwise reads clean in the second run
