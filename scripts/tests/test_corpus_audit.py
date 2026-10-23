"""scripts/corpus_audit.py: the corpus-keeper's periodic audit of profile/ (read only).

Keeper findings (exit 1 while any remain): links, index, status, stale, timeline, titles, conflicts.
Lists for the owner (no effect on the exit code): open conflicts, rulings that still wait on a condition,
needs-confirmation entries, "to confirm" notes and uncertain timeline notes. Synthetic profiles here;
one smoke test reads the real profile.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import corpus_audit as ca  # noqa: E402

from layout import JS  # noqa: E402
TODAY = date(2025, 9, 15)
MONTHS = [f"2025-{m:02d}" for m in range(1, 10)]


def corpus_file(cid: str, kind: str, title: str, start: str, end: str, status: str = "confirmed",
                body: str = "") -> str:
    meta = {"id": cid, "kind": kind, "org": "Org", "title": title, "dates": {"start": start, "end": end},
            "location": "San Francisco, CA", "status": status, "discretion": "resume-ok",
            "lenses": ["ai-software"], "evidence": [f"ev-{cid}-001"]}
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n# " + title + "\n\n" + body


def index_row(kind: str, cid: str, title: str, start: str, end: str, status: str = "confirmed",
              path: str | None = None) -> str:
    return (f"| {kind} | {cid} | {title} | {start} to {end} | {status} | resume-ok | ai-software | "
            f"`{path or f'corpus/{cid}/README.md'}` |")


def base_facts() -> dict:
    active = {m: (["acme"] if m <= "2025-06" else []) + (["side"] if m >= "2025-03" else []) for m in MONTHS}
    return {
        "employment": [{"id": "exp-acme", "org": "Acme", "title": "Engineer", "title_render": "Engineer",
                        "start": "2025-01", "end": "2025-06", "kind": "experience", "status": "confirmed",
                        "corpus": "corpus/acme/README.md", "evidence": ["ev-acme-001"]}],
        "projects_index": "projects-index.md",
        "timeline": {"items": [{"id": "acme", "start": "2025-01", "end": "2025-06", "label": "Acme"},
                               {"id": "side", "start": "2025-03", "end": "present", "label": "Side project"}],
                     "months": [{"month": m, "active": active[m]} for m in MONTHS]},
        "conflicts": [
            {"id": "C01", "topic": "Acme title", "versions": [{"claim": "a"}], "proposed_ruling": "Engineer",
             "status": "ruled", "ruled": {"by": "owner", "on": date(2025, 9, 1), "basis": "chat", "ruling": "Engineer"}},
            {"id": "C02", "topic": "Acme team size", "versions": [{"claim": "b"}], "proposed_ruling": "none",
             "status": "awaiting-owner"},
            {"id": "C03", "topic": "Side poster", "versions": [{"claim": "c"}], "proposed_ruling": "once presented",
             "status": "ruled", "ruled": {"by": "owner", "on": date(2025, 9, 1), "basis": "chat",
                                          "ruling": "add it once presented", "note": "held until the talk is confirmed"}},
        ],
    }


def build(tmp_path: Path, facts: dict | None = None, *, acme_status: str = "confirmed", acme_body: str = "",
          side_status: str = "confirmed", rows: list[str] | None = None, profile_md: str | None = None,
          status_md: str | None = None) -> Path:
    js = tmp_path / "js"
    prof = js / "profile"
    for d in ("acme", "side"):
        (prof / "corpus" / d).mkdir(parents=True)
    (prof / "corpus" / "acme" / "README.md").write_text(
        corpus_file("acme", "experience", "Engineer", "2025-01", "2025-06", acme_status, acme_body), encoding="utf-8")
    (prof / "corpus" / "side" / "README.md").write_text(
        corpus_file("side", "project", "Side project", "2025-03", "present", side_status), encoding="utf-8")
    if rows is None:
        rows = [index_row("experience", "acme", "Engineer", "2025-01", "2025-06", acme_status),
                index_row("project", "side", "Side project", "2025-03", "present", side_status)]
    (prof / "projects-index.md").write_text(
        "# Projects and experiences index\n\n| Kind | Id | Title | Dates | Status | Discretion | Lenses | File |\n"
        "|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")
    (prof / "facts.yaml").write_text(yaml.safe_dump(facts or base_facts(), sort_keys=False), encoding="utf-8")
    (prof / "PROFILE.md").write_text(profile_md if profile_md is not None else
                                     "---\nstatus: approved\n---\n\n### Engineer · Acme, San Francisco, CA · Jan 2025 - Jun 2025\n",
                                     encoding="utf-8")
    (prof / "CORPUS-STATUS.md").write_text(status_md or "# CORPUS-STATUS\n\n| File | Kind | Status | Open questions for Alex |\n"
                                           "|---|---|---|---|\n| `corpus/acme/README.md` | experience | complete | none |\n",
                                           encoding="utf-8")
    return js


def checks(report: dict, check: str) -> list[dict]:
    return [f for f in report["keeper"] if f["check"] == check]


def text(findings: list[dict]) -> str:
    return "\n".join(f"{f['where']}: {f['message']}" for f in findings)


# --------------------------------------------------------------------------- clean
def test_a_clean_profile_has_no_keeper_findings(tmp_path):
    report = ca.audit(build(tmp_path), TODAY)
    assert report["keeper"] == []
    assert report["owner"]["open_conflicts"] == ["C02: Acme team size"]
    assert report["owner"]["conditional_rulings"] == ["C03: held until the talk is confirmed"]


# --------------------------------------------------------------------------- links and index
def test_a_corpus_folder_nothing_reaches_is_a_links_finding(tmp_path):
    js = build(tmp_path)
    orphan = js / "profile" / "corpus" / "orphan"
    orphan.mkdir()
    (orphan / "README.md").write_text(corpus_file("orphan", "project", "Orphan", "2025-02", "2025-02"), encoding="utf-8")
    found = text(checks(ca.audit(js, TODAY), "links"))
    assert "corpus/orphan/" in found and "not reached" in found
    assert "corpus/orphan/README.md" in found and "no projects-index.md row" in found


def test_an_index_row_pointing_at_a_missing_file(tmp_path):
    rows = [index_row("experience", "acme", "Engineer", "2025-01", "2025-06"),
            index_row("project", "side", "Side project", "2025-03", "present"),
            index_row("project", "gone", "Gone", "2025-01", "2025-01")]
    found = text(checks(ca.audit(build(tmp_path, rows=rows), TODAY), "links"))
    assert "corpus/gone/README.md" in found and "does not exist" in found


def test_an_index_row_that_differs_from_its_frontmatter(tmp_path):
    rows = [index_row("experience", "acme", "Engineer", "2025-01", "2025-06"),
            index_row("project", "side", "Side hustle", "2025-02", "present", "needs-confirmation")]
    found = text(checks(ca.audit(build(tmp_path, rows=rows), TODAY), "index"))
    assert "side" in found and "status" in found and "dates" in found and "title 'Side hustle'" in found


def test_a_truncated_index_title_is_accepted(tmp_path):
    rows = [index_row("experience", "acme", "Engin", "2025-01", "2025-06"),
            index_row("project", "side", "Side project", "2025-03", "present")]
    assert checks(ca.audit(build(tmp_path, rows=rows), TODAY), "index") == []


# --------------------------------------------------------------------------- status
def test_facts_and_corpus_status_must_agree_for_an_experience(tmp_path):
    report = ca.audit(build(tmp_path, acme_status="needs-confirmation"), TODAY)
    found = text(checks(report, "status"))
    assert "exp-acme" in found and "corpus/acme/README.md" in found and "needs-confirmation" in found


@pytest.mark.parametrize("ref,where,expect", [
    ("C01", "keeper", "ruled"),                       # every conflict it names is ruled: stale status
    ("C02", "owner", "waits on C02"),                # open
    ("C03", "owner", "waits on C03"),                # ruled, but the ruling waits on a condition
    (None, "owner", "no conflict recorded"),
])
def test_needs_confirmation_entries(tmp_path, ref, where, expect):
    facts = base_facts()
    entry = facts["employment"][0]
    entry["status"] = "needs-confirmation"
    if ref:
        entry["conflict"] = ref
    report = ca.audit(build(tmp_path, facts, acme_status="needs-confirmation"), TODAY)
    if where == "keeper":
        found = text(checks(report, "status"))
        assert "exp-acme" in found and expect in found
    else:
        assert checks(report, "status") == []
        assert any("exp-acme" in line and expect in line for line in report["owner"]["needs_confirmation"])


# --------------------------------------------------------------------------- stale notes
def test_open_notes_that_name_only_ruled_conflicts_are_stale(tmp_path):
    body = ("Scope line; the figure is held (C01).\n"
            "Held under C01: waits for Alex's ruling.\n"
            "Title (C01, ruled 2025-09-01: Engineer).\n"
            "Team size is pending (C02).\n\n"
            "## Open questions\n\n"
            "1. Title on record (C01)?\n"
            "2. Team size (C02)?\n"
            "3. Poster (C03)?\n"
            "4. Title and team (C01, C02)?\n"
            "5. Title (C01): ruled 2025-09-01, Engineer.\n")
    report = ca.audit(build(tmp_path, acme_body=body), TODAY)
    stale = checks(report, "stale")
    lines = sorted(f["line"] for f in stale)
    readme = (tmp_path / "js" / "profile" / "corpus" / "acme" / "README.md").read_text(encoding="utf-8").splitlines()
    assert [readme[n - 1] for n in lines] == ["Held under C01: waits for Alex's ruling.", "1. Title on record (C01)?"]


def test_corpus_status_table_rows_are_scanned(tmp_path):
    status_md = ("# CORPUS-STATUS\n\n| File | Kind | Status | Open questions for Alex |\n|---|---|---|---|\n"
                 "| `corpus/acme/README.md` | experience | needs-alex | Title on record (C01) |\n")
    stale = checks(ca.audit(build(tmp_path, status_md=status_md), TODAY), "stale")
    assert len(stale) == 1 and stale[0]["where"].startswith("CORPUS-STATUS.md:")


def test_an_unknown_conflict_id_is_a_finding(tmp_path):
    found = text(checks(ca.audit(build(tmp_path, acme_body="Dates disputed (C99).\n"), TODAY), "conflicts"))
    assert "C99" in found


# --------------------------------------------------------------------------- timeline
def test_timeline_months_follow_the_items(tmp_path):
    facts = base_facts()
    months = {m["month"]: m for m in facts["timeline"]["months"]}
    months["2025-05"]["active"].remove("side")
    months["2025-08"]["active"].append("acme")
    months["2025-09"]["active"].append("ghost")
    found = text(checks(ca.audit(build(tmp_path, facts), TODAY), "timeline"))
    assert "side" in found and "2025-05" in found
    assert "acme" in found and "2025-08" in found
    assert "ghost" in found


def test_months_must_reach_today(tmp_path):
    found = text(checks(ca.audit(build(tmp_path), date(2025, 11, 2)), "timeline"))
    assert "2025-09" in found and "2025-11" in found


@pytest.mark.parametrize("note,where", [("end may be 2025-07 (C01)", "keeper"), ("end may be 2025-07 (C02)", "owner"),
                                       ("records start 2025-02", "owner")])
def test_uncertain_timeline_notes(tmp_path, note, where):
    facts = base_facts()
    facts["timeline"]["items"][0]["uncertain"] = note
    report = ca.audit(build(tmp_path, facts), TODAY)
    stale = checks(report, "stale")
    if where == "keeper":
        assert len(stale) == 1 and "acme" in stale[0]["where"]
    else:
        assert stale == [] and any(note in line for line in report["owner"]["timeline_notes"])


# --------------------------------------------------------------------------- titles
def test_a_title_render_profile_md_does_not_render(tmp_path):
    found = text(checks(ca.audit(build(tmp_path, profile_md="---\nstatus: draft\n---\n"), TODAY), "titles"))
    assert "exp-acme" in found and "Engineer" in found


def test_profile_pending_waits_for_the_profile_editor(tmp_path):
    facts = base_facts()
    facts["employment"][0]["profile_pending"] = "2025-09-15 I-099: title_render proposed"
    report = ca.audit(build(tmp_path, facts, profile_md="---\nstatus: draft\n---\n"), TODAY)
    assert checks(report, "titles") == []
    assert any("exp-acme" in line for line in report["waiting"])
    rendered = ca.audit(build(tmp_path / "again", facts), TODAY)
    found = text(checks(rendered, "titles"))
    assert "exp-acme" in found and "profile_pending" in found


# --------------------------------------------------------------------------- lists for the owner
def test_to_confirm_notes_are_listed_for_the_owner(tmp_path):
    body = "- AC-01 [ai-software] Built the thing. (ev-acme-001) [confirm his share of the code]\n" \
           "- AC-02 [ai-software] Every claim traced to confirmed experience. (ev-acme-001)\n"
    report = ca.audit(build(tmp_path, acme_body=body), TODAY)
    notes = report["owner"]["to_confirm"]
    assert len(notes) == 1 and "confirm his share" in notes[0] and "corpus/acme/README.md" in notes[0]


def test_a_valid_condition_met_settles_a_conditional_ruling(tmp_path):
    facts = base_facts()
    facts["conflicts"][2]["condition_met"] = {"by": "owner", "on": date(2025, 9, 10), "basis": "chat",
                                              "words": "We gave the talk."}
    report = ca.audit(build(tmp_path, facts), TODAY)
    assert report["owner"]["conditional_rulings"] == []
    body = "- AC-01 [ai-software] Side poster line held, pending (C03).\n"
    stale = checks(ca.audit(build(tmp_path / "stale", facts, acme_body=body), TODAY), "stale")
    assert any("C03" in f["message"] for f in stale), text(stale)       # settled now, so "pending" is stale


@pytest.mark.parametrize("bad", [{"by": "coordinator"}, {"words": ""}, {"on": "soon"}, {"basis": ""}])
def test_an_invalid_condition_met_leaves_the_ruling_waiting(tmp_path, bad):
    facts = base_facts()
    met = {"by": "owner", "on": date(2025, 9, 10), "basis": "chat", "words": "We gave the talk."}
    facts["conflicts"][2]["condition_met"] = dict(met, **bad)
    report = ca.audit(build(tmp_path, facts), TODAY)
    assert report["owner"]["conditional_rulings"] == ["C03: held until the talk is confirmed"]


# --------------------------------------------------------------------------- CLI
def test_cli_exit_codes_and_json(tmp_path, capsys):
    js = build(tmp_path)
    assert ca.main(["--root", str(js), "--today", "2025-09-15"]) == 0
    out = capsys.readouterr().out
    assert "corpus_audit: 0 finding(s) for the keeper" in out and "C02" in out
    assert ca.main(["--root", str(js), "--today", "2025-11-02", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["date"] == "2025-11-02" and data["keeper"][0]["check"] == "timeline"


def test_cli_rejects_a_bad_date(tmp_path):
    with pytest.raises(SystemExit):
        ca.main(["--root", str(build(tmp_path)), "--today", "15/09/2025"])


# --------------------------------------------------------------------------- the real profile
@pytest.mark.real_records
def test_the_real_profile_links_index_timeline_and_titles_are_clean():
    report = ca.audit(JS, date(2026, 9, 25))
    bad = [f for f in report["keeper"] if f["check"] in {"links", "index", "timeline", "titles", "conflicts"}]
    assert bad == [], text(bad)
    assert report["owner"]["open_conflicts"], "the real profile lists its open conflicts"
    assert all(isinstance(c, str) for c in report["owner"]["conditional_rulings"])
