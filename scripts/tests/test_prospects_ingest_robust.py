"""prospects.py ingest never crashes a batch: hand-written records get schema defaults, unreadable
records and failing events are reported and skipped."""
from __future__ import annotations

import json
from datetime import date

from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import prospects as pr
from test_prospects import TODAY, _event, _load, _roots


def test_ingest_fills_schema_defaults_on_a_hand_written_record(tmp_path):
    roots = _roots(tmp_path)
    roots.prospects.mkdir(parents=True)
    (roots.prospects / "dana-whitcombe.md").write_text(
        "---\nid: dana-whitcombe\nname: Dana Whitcombe\ncompany: Lumen Loop\nemails: [dana@lumenloop.test]\n"
        "source: gmail-backlog\nstatus: triaged\npriority: P1\nwarmth: 2\ncreated: 2026-09-20\nupdated: 2026-09-20\n---\n"
        "# Dana Whitcombe\n\n## Timeline\n", encoding="utf-8")
    report = pr.ingest_events(roots, [_event("d1", "Dana Whitcombe", email="dana@lumenloop.test",
                                             company="Lumen Loop")], today=TODAY)
    assert report["new_events"] == 1 and not report.get("bad_records")
    meta, body = _load(roots, "dana-whitcombe")
    assert meta["sources"] == ["gmail-backlog"] and meta["inbound_count"] == 1 and "[k:d1]" in body
    assert (meta["priority"], meta["warmth"], meta["status"]) != (None, None, None)
    assert meta["name"] == "Dana Whitcombe" and meta["created"] == date(2026, 9, 20)   # kept, not reset


def test_ingest_reports_an_unreadable_record_and_finishes_the_batch(tmp_path):
    roots = _roots(tmp_path)
    roots.prospects.mkdir(parents=True)
    (roots.prospects / "broken.md").write_text("---\nid: broken\nname: [unclosed\n---\n", encoding="utf-8")
    (roots.prospects / "listy.md").write_text("---\n- just\n- a list\n---\n", encoding="utf-8")
    report = pr.ingest_events(roots, [_event("n1", "Nora Vale", email="nora@kite.test", company="Kite Bio")],
                              today=TODAY)
    assert report["created"] == 1 and (roots.prospects / "nora-vale.md").is_file()
    assert {b["file"] for b in report["bad_records"]} == {"broken.md", "listy.md"}


def test_one_bad_event_is_reported_and_the_rest_of_the_batch_still_lands(tmp_path, monkeypatch):
    roots = _roots(tmp_path)
    real = pr._apply

    def flaky(meta, body, ev, today, gate=None):                 # _apply now takes the interview gate too
        if ev["key"] == "bad":
            raise ValueError("boom")
        return real(meta, body, ev, today, gate)

    monkeypatch.setattr(pr, "_apply", flaky)
    report = pr.ingest_events(roots, [_event("bad", "Zed Ro", email="zed@x.test", at="2026-09-01T10:00:00Z"),
                                      _event("ok", "Amy Wu", email="amy@y.test", at="2026-09-02T10:00:00Z")],
                              today=TODAY)
    assert (roots.prospects / "amy-wu.md").is_file() and not (roots.prospects / "zed-ro.md").exists()
    assert [e["key"] for e in report["errors"]] == ["bad"] and "boom" in report["errors"][0]["error"]


# --------------------------------------------------------------------------- score leaves triage's fields alone
def _hand_prioritized(roots):
    """A new record whose priority someone set without the lock (the status and priority are triage's call)."""
    pr.ingest_events(roots, [_event("s1", "Rae Lin", email="rae@acme.test", company="Acme", title="Recruiter",
                                    text="Hi Alex, we're hiring an ML Engineer on my team. Open to a chat?")],
                     today=TODAY)
    path = roots.prospects / "rae-lin.md"
    meta, body = jc.read_record(path)
    meta["priority"], meta["warmth"] = "P1", 0
    jc.write_record(path, meta, body)
    return path


def test_score_refreshes_computed_fields_and_leaves_status_and_priority(tmp_path, capsys):
    roots = _roots(tmp_path)
    path = _hand_prioritized(roots)
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score"]) == 0
    out = json.loads(capsys.readouterr().out)
    meta, _ = jc.read_record(path)
    assert (meta["status"], meta["priority"]) == ("new", "P1")
    assert meta["warmth"] >= 1 and meta["score_reasons"]                 # computed fields refreshed
    row = next(r for r in out["prospects"] if r["id"] == "rae-lin")
    assert row["days_since_inbound"] == 23 and row["suggested_priority"] in ("P0", "P1", "P2", "P3")
    assert out["triaged"] == 0


def test_apply_baseline_restores_the_old_status_and_priority_writes(tmp_path, capsys):
    roots = _roots(tmp_path)
    path = _hand_prioritized(roots)
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score", "--apply-baseline"]) == 0
    out = json.loads(capsys.readouterr().out)
    meta, _ = jc.read_record(path)
    assert meta["status"] == "triaged" and out["triaged"] == 1
    assert meta["priority"] == next(r for r in out["prospects"] if r["id"] == "rae-lin")["suggested_priority"]


def _mixed(roots):
    """Three records with stale computed fields: one new, one triaged, one in conversation."""
    pr.ingest_events(roots, [_event("m1", "Aisha Bello", email="aisha@acme.test", company="Acme", title="Recruiter"),
                             _event("m2", "Sofia Marchetti", email="sofia@betaai.test", company="Beta", title="Founder"),
                             _event("m3", "Nia Park", email="nia@zeta.test", company="Zeta", title="Recruiter")],
                     today=TODAY)
    for slug, status in (("aisha-bello", "triaged"), ("sofia-marchetti", "in_conversation"), ("nia-park", "new")):
        path = roots.prospects / f"{slug}.md"
        meta, body = jc.read_record(path)
        meta["status"], meta["warmth"], meta["score_reasons"] = status, 0, ["stale"]
        jc.write_record(path, meta, body)
    return {p.stem: p for p in roots.prospects.glob("*.md")}


def _snapshot(paths):
    return {slug: (p.stat().st_mtime_ns, p.read_bytes()) for slug, p in paths.items()}


def test_score_defaults_to_status_new_and_leaves_other_files_byte_identical(tmp_path, capsys):
    roots = _roots(tmp_path)
    paths = _mixed(roots)
    before = _snapshot(paths)
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score"]) == 0
    out = json.loads(capsys.readouterr().out)
    after = _snapshot(paths)
    assert after["aisha-bello"] == before["aisha-bello"] and after["sofia-marchetti"] == before["sofia-marchetti"]
    assert after["nia-park"] != before["nia-park"]
    assert [r["id"] for r in out["prospects"]] == ["nia-park"] and out["selected"] == ["new"]


def test_score_status_and_all_widen_the_pass_and_unchanged_records_are_not_rewritten(tmp_path, capsys):
    roots = _roots(tmp_path)
    paths = _mixed(roots)
    before = _snapshot(paths)
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score", "--status", "triaged"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)["prospects"]] == ["aisha-bello"]
    after = _snapshot(paths)
    assert after["aisha-bello"] != before["aisha-bello"] and after["sofia-marchetti"] == before["sofia-marchetti"]
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score", "--all"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["scored"] == 3 and first["changed"] == 2                       # aisha was already current
    settled = _snapshot(paths)
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-24", "score", "--all"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] == 0 and _snapshot(paths) == settled


def test_score_rejects_an_unknown_status(tmp_path, capsys):
    roots = _roots(tmp_path)
    assert pr.main(["--root", str(roots.js), "score", "--status", "ghosting"]) == 2


# --------------------------------------------------------------------------- --today stamps timelines
from comms_helpers import clock_2030  # noqa: E402,F401


def test_status_change_merge_and_outbound_lines_carry_the_today_date(tmp_path, clock_2030, capsys):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("t1", "Rae Lin", email="rae@acme.test", company="Acme"),
                             _event("t2", "Rae Lin", email="rae.lin@gmail.com", company="Other Co")], today=TODAY)
    cli = ["--root", str(roots.js), "--today", "2026-09-28"]
    assert pr.main([*cli, "set", "rae-lin", "status=triaged"]) == 0
    assert pr.main([*cli, "set", "rae-lin-other-co", "merge_into=rae-lin"]) == 0
    src = roots.harvest / "outbound-2026-09-28.jsonl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(json.dumps({"name": "Nora Vale", "company": "Kite Bio", "linkedin_url":
                               "https://www.linkedin.com/in/nora-vale", "found_via": "companies/kite-bio.md"}) + "\n")
    assert pr.main([*cli, "ingest", str(src)]) == 0
    rae = (roots.prospects / "rae-lin.md").read_text()
    assert "- 2026-09-28T12:00:00+00:00 · note · status new → triaged" in rae
    assert "- 2026-09-28T12:00:00+00:00 · note · merged rae-lin-other-co into this record" in rae
    nora = (roots.prospects / "nora-vale.md").read_text()
    assert "- 2026-09-28T12:00:00+00:00 · added" in nora
    assert "2030" not in rae + nora
