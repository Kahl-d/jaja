"""reclassify_safe.py: re-run the classifier over a stored harvest file and keep every `resolve` answer.

`mail_classify.py classify` rewrites a whole *.classified.jsonl, which wipes the L3 answers
`resolve --answers` wrote in. Fixtures are SYNTHETIC (invented senders on example domains), laid out as
<tmp>/state/harvest/ so the CRM default (prospects/ next to state/) is an empty folder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import mail_classify as mc
import reclassify_safe as rs

BASE = {"to": ch.DEFAULT_TO, "label_ids": ["INBOX"], "direction": "in"}
PERSONAL = ("Sam Lee <sam.lee@mail.example.test>", "quick question",
            "Hi Alex, are you free to chat sometime next week? Sam")        # rules: other, L2
VAGUE = ("Pat Roe <pat@acme-robotics.example.test>", "Hello",
         "Hey, saw your profile. Would love to connect about a role on our team.")   # below threshold: L3 queue


def _mail(rid: str, sender: str, subject: str, body: str) -> dict:
    return dict(BASE, id=rid, thread_id="t-" + rid, **{"from": sender, "subject": subject, "body": body,
                                                         "snippet": body})


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def harvest(tmp_path, monkeypatch):
    """A source and its classified file. In the classified file: u1 unchanged, a1 stale (stored review, rules now
    say other), r1 resolved (resolved_on), r2 resolved by the older marker (backend agent only), m1 not in the
    source, a meta passthrough row. In the source only: n1 (new mail)."""
    monkeypatch.setattr(mc, "portal_index", lambda root=None: {})
    folder = tmp_path / "state" / "harvest"
    folder.mkdir(parents=True)
    src, out = folder / "gmail-2026-01-02.jsonl", folder / "gmail-2026-01-02.classified.jsonl"
    source = [_mail("u1", *PERSONAL), _mail("a1", *PERSONAL), _mail("r1", *VAGUE), _mail("r2", *PERSONAL),
              {"type": "meta", "id": "meta-1", "note": "export header"}]
    _write_jsonl(src, source)
    mc.classify_file(src, decider=mc.AgentQueueBackend(tmp_path / "baseline-queue.jsonl"), out=out,
                     crm=mc.CrmIndex(), portals={})
    rows = _rows(out)
    by_id = {r["id"]: r for r in rows}
    by_id["a1"]["classification"] = {"label": "review", "level": "L3", "confidence": 0.0, "backend": "agent-queue",
                                     "needs_review": True, "evidence": ""}
    by_id["r1"]["classification"] = {"label": "recruiter_outreach", "level": "L3", "confidence": 0.9,
                                     "backend": "agent", "needs_review": False, "evidence": "Hello",
                                     "evidence_verified": True, "resolved_on": "2026-01-03"}
    by_id["r2"]["classification"] = {"label": "networking", "level": "L3", "confidence": 0.8, "backend": "agent",
                                     "needs_review": False, "evidence": ""}
    m1 = dict(_mail("m1", *PERSONAL), classification={"label": "job_alert", "level": "L0", "confidence": 1.0})
    rows.insert(2, m1)
    _write_jsonl(out, rows)
    _write_jsonl(src, source + [_mail("n1", *PERSONAL)])
    return tmp_path, src, out


def _plan(out: Path) -> rs.FilePlan:
    return rs.plan_file(out, portals={})


def test_resolved_rows_keep_their_answer(harvest):
    _, _, out = harvest
    plan = _plan(out)
    assert sorted(plan.resolved_kept) == ["r1", "r2"]
    assert not {c.id for c in plan.changes} & {"r1", "r2"}
    before = {r["id"]: r for r in _rows(out)}
    assert rs.main(["--in", str(out), "--write"]) == 1
    after = {r["id"]: r for r in _rows(out)}
    assert after["r1"]["classification"] == before["r1"]["classification"]
    assert after["r2"]["classification"] == before["r2"]["classification"]
    assert rs.is_resolved(after["r1"]["classification"]) and rs.is_resolved(after["r2"]["classification"])


def test_other_rows_take_the_new_classification(harvest):
    _, _, out = harvest
    plan = _plan(out)
    assert [(c.id, c.old_label, c.new_label) for c in plan.changes] == [("a1", "review", "other")]
    assert plan.unchanged == 1 and plan.passthrough == 1
    assert rs.main(["--in", str(out), "--write"]) == 1
    after = {r["id"]: r for r in _rows(out)}
    assert after["a1"]["classification"] == after["u1"]["classification"]
    assert after["a1"]["classification"]["label"] == "other"


def test_dry_run_writes_nothing(harvest, capsys):
    root, src, out = harvest
    listing = sorted(p.name for p in out.parent.iterdir())
    data, src_data = out.read_bytes(), src.read_bytes()
    assert rs.main(["--in", str(out)]) == 1
    assert out.read_bytes() == data and src.read_bytes() == src_data
    assert sorted(p.name for p in out.parent.iterdir()) == listing
    text = capsys.readouterr().out
    assert "a1  review/L3 -> other/L2" in text and "dry-run (nothing written)" in text
    assert "1  review -> other" in text


def test_write_keeps_rows_order_and_bytes(harvest):
    _, _, out = harvest
    before_lines = out.read_text(encoding="utf-8").splitlines(keepends=True)
    assert rs.main(["--in", str(out), "--write"]) == 1
    after_lines = out.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(after_lines) == len(before_lines)
    for old, new in zip(before_lines, after_lines):
        o, n = json.loads(old), json.loads(new)
        assert o.get("id") == n.get("id")
        if o.get("id") == "a1":
            assert {k: v for k, v in o.items() if k != "classification"} == \
                   {k: v for k, v in n.items() if k != "classification"}
        else:
            assert old == new
    assert not [p for p in out.parent.iterdir() if p.name.endswith(".tmp")]
    assert rs.main(["--in", str(out)]) == 0          # a second run finds nothing to change


def test_write_is_atomic(harvest, monkeypatch):
    _, _, out = harvest
    data = out.read_bytes()
    calls = []

    def failing_replace(src, dst):
        calls.append((Path(src).parent, Path(dst)))
        raise OSError("disk full")

    monkeypatch.setattr(rs.os, "replace", failing_replace)
    with pytest.raises(OSError):
        rs.write_plan(_plan(out))
    assert calls == [(out.parent, out)]               # the new file went to a temp file next to it first
    assert out.read_bytes() == data
    assert sorted(p.name for p in out.parent.iterdir()) == ["gmail-2026-01-02.classified.jsonl",
                                                             "gmail-2026-01-02.jsonl"]


def test_write_refuses_a_reordered_rewrite(harvest, monkeypatch):
    _, _, out = harvest
    data = out.read_bytes()
    real_render = rs.render
    monkeypatch.setattr(rs, "render", lambda plan: list(reversed(real_render(plan))))
    with pytest.raises(rs.PreconditionError):
        rs.write_plan(_plan(out))
    assert out.read_bytes() == data
    assert rs.main(["--in", str(out), "--write"]) == 2
    assert out.read_bytes() == data


def test_write_refuses_a_change_outside_classification(harvest, monkeypatch):
    _, _, out = harvest
    data = out.read_bytes()
    real_render = rs.render

    def render_and_touch_subject(plan):
        lines = real_render(plan)
        i = plan.changes[0].index
        row = json.loads(lines[i])
        row["subject"] = "edited"
        lines[i] = rs.serialize(row)
        return lines

    monkeypatch.setattr(rs, "render", render_and_touch_subject)
    with pytest.raises(rs.PreconditionError, match="other than classification"):
        rs.write_plan(_plan(out))
    assert out.read_bytes() == data


def test_write_refuses_when_the_file_changed_since_read(harvest):
    _, _, out = harvest
    plan = _plan(out)
    out.write_text(out.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    edited = out.read_bytes()
    with pytest.raises(rs.PreconditionError, match="changed on disk"):
        rs.write_plan(plan)
    assert out.read_bytes() == edited


def test_row_missing_from_source_is_kept(harvest):
    _, _, out = harvest
    plan = _plan(out)
    assert plan.missing_in_source == ["m1"]
    line = next(x for x in out.read_text(encoding="utf-8").splitlines(keepends=True) if '"m1"' in x)
    rs.main(["--in", str(out), "--write"])
    assert line in out.read_text(encoding="utf-8").splitlines(keepends=True)


def test_new_source_rows_are_reported_not_appended(harvest, capsys):
    _, _, out = harvest
    plan = _plan(out)
    assert [n["id"] for n in plan.new_in_source] == ["n1"]
    rs.main(["--in", str(out), "--write", "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["totals"]["new_in_source_skipped"] == 1
    assert "n1" not in {r.get("id") for r in _rows(out)}


def test_exit_codes(harvest, tmp_path):
    root, src, out = harvest
    assert rs.main(["--in", str(out)]) == 1                         # changes found, dry run
    assert rs.main(["--in", str(out), "--write"]) == 1              # written
    assert rs.main(["--in", str(out)]) == 0                         # nothing left
    with pytest.raises(SystemExit) as usage:
        rs.main([])
    assert usage.value.code == 2
    lonely = out.parent / "gmail-2026-01-09.classified.jsonl"
    lonely.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    assert rs.main(["--in", str(lonely)]) == 2                      # named file without a source
    assert rs.main(["--all", "--root", str(root)]) == 0             # --all skips it and goes on
    bad = out.read_text(encoding="utf-8") + "{not json\n"
    out.write_text(bad, encoding="utf-8")
    assert rs.main(["--in", str(out), "--write"]) == 2
    assert out.read_text(encoding="utf-8") == bad
    rows = _rows(src)
    _write_jsonl(out, [dict(r, classification={"label": "other"}) for r in rows[:2] + rows[:1]])
    assert rs.main(["--in", str(out)]) == 2                         # duplicate message id


def test_l3_queue_is_not_written_to_the_harvest_folder(harvest, monkeypatch, capsys):
    root, _, out = harvest
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-not-a-key")    # Jev would be asked under `--l3 auto`
    asked = []
    monkeypatch.setattr(mc.JevBackend, "__call__", lambda self, request: asked.append(request))
    listing = sorted(p.name for p in out.parent.iterdir())
    assert rs.main(["--all", "--root", str(root), "--write", "--json"]) == 1
    rep = json.loads(capsys.readouterr().out)
    assert rep["totals"]["l3_requests_discarded"] >= 1               # r1's mail went to the (temporary) queue
    assert asked == []
    assert sorted(p.name for p in out.parent.iterdir()) == listing
    assert not list(root.rglob("l3-queue*"))
    assert not (root / "prospects").exists()
