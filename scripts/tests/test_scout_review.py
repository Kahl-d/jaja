"""scout_review.py: close the scout review by script (job-scout evals, 2026-09-25).

- sources.yaml passed_gates is computed from the job files' final statuses after the review, never hand-counted.
- A keep whose ghost_score sits in the liveness band (0.3 to 0.6) needs a liveness fetch that day; over 0.6 it
  needs a warm contact. Otherwise the job is re-gated (gate: review, with the reason) and not counted as passed.
"""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import scout_review as sr  # noqa: E402
from js_common import fm, write_record  # noqa: E402

TODAY = "2026-09-28"

BLOCK_SOURCES = """\
# comment kept
sources:
  - id: greenhouse
    kind: ats-json
    enabled: true
    yield_30d: 3
    last_run: '2026-09-28T13:00:00Z'
    notes: "keyless"
  - id: lever
    kind: ats-json
    yield_30d: null
    last_run: null
    passed_gates: 7   # old hand count
"""
FLOW_SOURCES = """\
sources:
  - {id: greenhouse-watchlist, kind: greenhouse, state: active, last_run: 2026-09-25, new: 6, passed_gates: 4, queued: 1}
  - {id: lever-watchlist, kind: lever, state: active, last_run: 2026-09-25, new: 2, queued: 0}
"""


def _job(root, jid, *, source="greenhouse", status="fit_checked", gate="pass", ghost=0.1, first_seen=TODAY,
         last_seen=TODAY, company_slug="acme", review_line=None, verdict="unscored", **extra):
    props = {"id": jid, "company": "Acme", "company_slug": company_slug, "title": "ML Engineer", "source": source,
            "source_url": f"https://example.com/{jid}", "first_seen": first_seen, "last_seen": last_seen,
            "status": status, "gate": gate, "gate_reasons": [], "ghost_score": ghost, "fit_verdict": verdict,
            "tags": [], **extra}
    body = "## JD snapshot\n<untrusted_jd>\nBuild agents.\n</untrusted_jd>\n"
    if review_line:
        body += f"\n## Scout review\n- {review_line}\n"
    write_record(root / "jobs" / f"{jid}.md", props, body)
    return root / "jobs" / f"{jid}.md"


def _sources(root, text=BLOCK_SOURCES):
    (root / "sources.yaml").write_text(text, encoding="utf-8")


def _close(root, *args):
    return sr.main(["close", "--root", str(root), "--today", TODAY, *args])


def test_passed_gates_is_computed_from_final_statuses(root, capsys):
    _sources(root)
    _job(root, "acme-1")                                                        # gate pass, fit_checked
    _job(root, "acme-2", gate="review", review_line=f"{TODAY} kept: title row")  # review settled as keep
    _job(root, "acme-3", gate="review", status="skipped", review_line=f"{TODAY} skipped: contract")
    _job(root, "acme-4", status="queued")
    _job(root, "acme-5", first_seen="2026-09-20")                               # an earlier run: not counted
    _job(root, "acme-6", source="lever", status="skipped", gate="fail")
    assert _close(root) == 0
    text = (root / "sources.yaml").read_text()
    assert "    passed_gates: 3\n" in text                                      # inserted in the greenhouse entry
    assert "    passed_gates: 0   # old hand count\n" in text                   # rewritten in place, comment kept
    assert text.startswith("# comment kept") and 'notes: "keyless"' in text
    out = capsys.readouterr().out
    assert "greenhouse: new 4, passed_gates 3, skipped 1" in out


def test_an_undecided_review_gate_is_not_counted_and_is_named(root, capsys):
    _sources(root)
    _job(root, "acme-1", gate="review")                                         # no Scout review line yet
    assert _close(root) == 0
    assert "    passed_gates: 0\n" in (root / "sources.yaml").read_text()
    assert "undecided review gates: acme-1" in capsys.readouterr().out


def test_flow_style_entries_are_updated_by_id_or_kind(root):
    _sources(root, FLOW_SOURCES)
    _job(root, "acme-1", source="greenhouse")
    _job(root, "acme-2", source="lever")
    assert _close(root) == 0
    text = (root / "sources.yaml").read_text()
    assert "{id: greenhouse-watchlist, kind: greenhouse, state: active, last_run: 2026-09-25, new: 6, passed_gates: 1, queued: 1}" in text
    assert "{id: lever-watchlist, kind: lever, state: active, last_run: 2026-09-25, new: 2, queued: 0, passed_gates: 1}" in text


def test_a_keep_in_the_liveness_band_without_a_fetch_today_is_regated(root, capsys):
    _sources(root)
    path = _job(root, "kite-1", gate="review", ghost=0.5, last_seen="2026-09-26",
                review_line=f"{TODAY} kept: no pay range, ghost-risk")
    assert _close(root) == 0
    props, body = fm.load(path)
    assert props["gate"] == "review" and props["status"] == "fit_checked"
    assert any(str(r).startswith(f"ghost re-gate {TODAY}") for r in props["gate_reasons"])
    assert "re-gated by scout_review.py" in body
    assert "    passed_gates: 0\n" in (root / "sources.yaml").read_text()
    out = capsys.readouterr().out
    assert "re-gated kite-1" in out and "0.50" in out


def test_a_keep_in_the_band_that_was_seen_today_stays(root, capsys):
    _sources(root)
    path = _job(root, "kite-1", gate="review", ghost=0.5, last_seen=f"{TODAY}T13:00:00Z",
                review_line=f"{TODAY} kept: ghost-risk")
    assert _close(root) == 0
    assert not fm.load(path)[0]["gate_reasons"]
    assert "kite-1: ghost_score 0.50, live (last_seen" in capsys.readouterr().out


def test_over_06_needs_a_warm_contact(root):
    _sources(root)
    cold = _job(root, "cold-1", gate="review", ghost=0.72, company_slug="coldco", review_line=f"{TODAY} kept")
    warm = _job(root, "warm-1", gate="review", ghost=0.72, company_slug="brightfold-ai", review_line=f"{TODAY} kept")
    write_record(root / "prospects" / "sofia.md", {"id": "sofia", "company_slug": "brightfold-ai"}, "")
    assert _close(root) == 0
    assert fm.load(cold)[0]["gate_reasons"] and not fm.load(warm)[0]["gate_reasons"]


def test_live_records_the_fetch_and_clears_the_regate(root, capsys):
    _sources(root)
    path = _job(root, "kite-1", gate="pass", ghost=0.5, last_seen="2026-09-26", review_line=f"{TODAY} kept")
    _close(root)
    assert fm.load(path)[0]["gate"] == "review"
    assert sr.main(["live", "kite-1", "--root", str(root), "--today", TODAY, "--evidence",
                    "Lever API 200: posting 230 open"]) == 0
    props, body = fm.load(path)
    assert str(props["live_checked_at"]) == TODAY and props["gate"] == "pass"       # the gate it had before
    assert any(str(r).startswith("note: ghost re-gate cleared") for r in props["gate_reasons"])
    assert f"{TODAY} liveness ok: Lever API 200: posting 230 open" in body
    capsys.readouterr()
    _close(root)
    assert "    passed_gates: 1\n" in (root / "sources.yaml").read_text()
    assert _close(root) == 0 and fm.load(path)[0]["gate"] == "pass"             # idempotent


def test_live_needs_evidence(root):
    _job(root, "kite-1")
    with pytest.raises(SystemExit):
        sr.main(["live", "kite-1", "--root", str(root), "--today", TODAY])


def test_dry_run_writes_nothing(root, capsys):
    _sources(root)
    path = _job(root, "kite-1", gate="review", ghost=0.5, last_seen="2026-09-26", review_line=f"{TODAY} kept")
    before, sources = path.read_text(), (root / "sources.yaml").read_text()
    assert _close(root, "--dry-run") == 0
    assert path.read_text() == before and (root / "sources.yaml").read_text() == sources
    assert "re-gated kite-1" in capsys.readouterr().out


def test_a_missing_sources_file_is_an_error(root, capsys):
    _job(root, "acme-1")
    assert _close(root) == 2
    assert "sources.yaml" in capsys.readouterr().out
