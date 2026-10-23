"""Prospects learn from outcomes (agency charter): a prospect whose path gave a screen or better is
never P3 (floor P1, kept through every rescoring), and via_prospect is linked both ways (the prospect lists the
applications that went through it). Tiny fixture trees; no network."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from disc_harness import *  # noqa: F401,F403

import js_comms_util as cu  # noqa: E402
import js_common as jc  # noqa: E402
import prospects as pr  # noqa: E402

TODAY = date(2026, 10, 6)


def prospect(roots: cu.Roots, slug: str, **kw) -> Path:
    meta = {"id": slug, "name": slug.replace("-", " ").title(), "source": "gmail-backlog", "status": kw.get("status", "new"),
            "priority": kw.get("priority", "P3"), "warmth": 1, "created": "2026-09-28", "updated": "2026-09-28",
            "role": "other", "company_type": "unknown", "last_inbound_at": "2026-09-29", "applications": kw.get("apps", [])}
    if kw.get("locked"):
        meta["priority_locked"] = True
    roots.prospects.mkdir(parents=True, exist_ok=True)
    path = roots.prospects / f"{slug}.md"
    cu.write_record(path, meta, "# person\n\n## Timeline\n")
    return path


def application(roots: cu.Roots, slug: str, via: str | None, events: list[str]) -> None:
    folder = roots.js / "applications" / slug
    folder.mkdir(parents=True, exist_ok=True)
    head = ["---", "type: application", f"company: {slug.split('-')[0].title()}", "path: person" if via else "path: cold"]
    if via:
        head.append(f"via_prospect: {via}")
    lines = [*head, "---", "# app", "", "## Events"]
    lines += [f"- {at} · {typ} · manual · conf=1.00 · test" for at, typ in (e.split(" ", 1) for e in events)]
    (folder / "application.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def roots_at(tmp_path: Path) -> cu.Roots:
    js = tmp_path / "js"
    js.mkdir()
    return cu.Roots(js=js, os=tmp_path / "os")


def meta_of(roots: cu.Roots, slug: str) -> dict:
    return jc.read_record(roots.prospects / f"{slug}.md")[0]


def test_outcomes_links_and_raises_a_screen_path_off_p3(tmp_path):
    roots = roots_at(tmp_path)
    prospect(roots, "priya")                                           # P3, no applications listed
    prospect(roots, "sam", priority="P2", apps=["globex-fde"])
    prospect(roots, "cold-one", priority="P3")
    application(roots, "initech-fe", "priya", ["2026-09-29T12:00:00Z applied", "2026-09-29T18:30:00Z screen"])
    application(roots, "globex-fde", "sam", ["2026-09-29T12:00:00Z applied", "2026-10-02T17:00:00Z screen",
                                                  "2026-10-05T17:00:00Z rejected"])       # rejected later: still a screen
    application(roots, "acme-ml", "cold-one", ["2026-09-30T12:00:00Z applied"])        # applied only: no floor
    application(roots, "cold-app", None, ["2026-09-30T12:00:00Z applied"])
    application(roots, "ghost", "nobody", ["2026-09-30T12:00:00Z applied"])

    dry = pr.outcomes(roots, TODAY, write=False)
    assert meta_of(roots, "priya")["priority"] == "P3"                 # a dry run writes nothing
    assert dry["missing"] == [{"application": "ghost", "via_prospect": "nobody"}]

    rep = pr.outcomes(roots, TODAY, write=True)
    k = meta_of(roots, "priya")
    assert k["applications"] == ["initech-fe"] and k["priority"] == "P1"
    assert any("screen via initech-fe" in r for r in k["score_reasons"])
    r = meta_of(roots, "sam")
    assert r["priority"] == "P1" and r["applications"] == ["globex-fde"]
    c = meta_of(roots, "cold-one")
    assert c["applications"] == ["acme-ml"] and c["priority"] == "P3"
    assert {x["id"] for x in rep["raised"]} == {"priya", "sam"}
    assert {(x["id"], x["application"]) for x in rep["linked"]} == {("priya", "initech-fe"), ("cold-one", "acme-ml")}
    again = pr.outcomes(roots, TODAY, write=True)                      # idempotent
    assert again["linked"] == [] and again["raised"] == []


def test_the_floor_survives_rescoring_and_a_hand_set_p3(tmp_path):
    roots = roots_at(tmp_path)
    prospect(roots, "priya", apps=["initech-fe"], priority="P3", locked=True)
    application(roots, "initech-fe", "priya", ["2026-09-29T12:00:00Z applied", "2026-10-01T18:30:00Z interview"])
    meta = meta_of(roots, "priya")
    scored = pr.score_prospect(meta, TODAY, js_root=roots.js)
    assert scored["priority"] == "P1"
    assert any("interviewing via initech-fe" in r for r in scored["score_reasons"])
    pr.score_all(roots, TODAY, apply_baseline=True, statuses=None)
    assert meta_of(roots, "priya")["priority"] == "P1"


def test_do_not_contact_and_no_screen_keep_their_priority(tmp_path):
    roots = roots_at(tmp_path)
    prospect(roots, "stopped", apps=["a-1"], status="do_not_contact")
    application(roots, "a-1", "stopped", ["2026-09-29T12:00:00Z applied", "2026-09-30T12:00:00Z screen"])
    assert pr.score_prospect(meta_of(roots, "stopped"), TODAY, js_root=roots.js)["priority"] == "P3"
    assert pr.best_outcome(["missing-app"], roots.js) is None


def test_cli_outcomes_dry_run_by_default(tmp_path, capsys):
    roots = roots_at(tmp_path)
    prospect(roots, "priya")
    application(roots, "initech-fe", "priya", ["2026-09-29T12:00:00Z applied", "2026-09-29T18:30:00Z screen"])
    assert pr.main(["--root", str(roots.js), "--today", "2026-10-06", "outcomes"]) == 0
    out = capsys.readouterr().out
    assert "linked: 1" in out and "raised: 1" in out and "dry run" in out
    assert meta_of(roots, "priya")["priority"] == "P3"
