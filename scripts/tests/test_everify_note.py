"""everify_note.py: the owner's manual E-Verify Employer Search lookup lands in legal/checks/<company>.md in the
format legal-visa writes and the apply gate reads. The check file here is SYNTHETIC (an invented company),
shaped like a real legal/checks/<company>.md, including a folded multi-line value."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apply_pack  # noqa: E402
import everify_note as ev  # noqa: E402
from js_common import fm  # noqa: E402

TODAY = date(2026, 9, 27)
CHECK = """\
---
company: Quillstone Systems
legal_entity: "Quillstone Systems, Inc. (per the dossier); the enrolled name is to confirm"
e_verify: unknown
e_verify_source: 'E-Verify Employer Search, tried 2026-09-26: the employer table is an interactive
  Tableau view these tools cannot query. Not searched by name'
stance: sponsors-selectively
cap_exempt: false
remote_ok_for_i983: false
employment_type: "full-time (posting's job type); W-2 not stated"
verdict: clear-with-questions
checked_at: 2026-09-26
---
Information only, not legal advice; questions for an attorney or the DSO are in `legal/questions.md`.

# Quillstone Systems: employer check (Applied AI Engineer)

## Verdict: clear-with-questions

E-Verify unknown; ask at the first recruiter contact.

## Questions for the recruiter

1. "Is Quillstone Systems enrolled in E-Verify?"
"""


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "job-search"
    (root / "legal" / "checks").mkdir(parents=True)
    (root / "legal" / "checks" / "quillstone-systems.md").write_text(CHECK, encoding="utf-8")
    return root


def path(root, slug="quillstone-systems"):
    return root / "legal" / "checks" / f"{slug}.md"


def gate(root, slug="quillstone-systems"):
    return apply_pack.visa_gate(SimpleNamespace(root=root, used=set()), slug)


def test_enrolled_sets_yes_and_keeps_every_other_line(js):
    res = ev.record(js, "Quillstone Systems", "enrolled", "searched 'Quillstone Systems'; row: open, enrolled 2024-03-01",
                    date(2026, 9, 27), TODAY)
    text = path(js).read_text(encoding="utf-8")
    front, body = fm.load(path(js))
    assert front["e_verify"] == "yes"                            # a string, not YAML true
    assert front["e_verify_source"].startswith("E-Verify Employer Search (https://www.e-verify.gov/")
    assert "looked up by the owner 2026-09-27: enrolled (searched 'Quillstone Systems'; row: open" in front["e_verify_source"]
    for key in ("legal_entity", "stance", "cap_exempt", "remote_ok_for_i983", "employment_type", "verdict", "checked_at"):
        old_line = next(line for line in CHECK.splitlines() if line.startswith(f"{key}:"))
        assert old_line in text.splitlines()                      # untouched, byte for byte
    assert "Tableau view these tools cannot query" not in text   # the folded old value went with its key
    assert CHECK.split("---\n", 2)[2].rstrip("\n") in body        # the old body, whole
    assert body.splitlines()[0].startswith("Information only, not legal advice")
    assert "## E-Verify lookups\n- 2026-09-27: enrolled (e_verify yes; was unknown)." in body
    assert res["recheck"] is True and res["verdict"] == "clear-with-questions"
    line, blockers = gate(js)
    assert "E-Verify yes" in line and blockers == []


def test_not_found_is_not_no(js):
    ev.record(js, "quillstone-systems", "not-found", "searched two spellings, no row", date(2026, 9, 27), TODAY)
    front, _ = fm.load(path(js))
    assert front["e_verify"] == "unknown"
    assert "not-found" in front["e_verify_source"] and "enrolled under another name" in front["e_verify_source"]
    _, blockers = gate(js)
    assert not any("e_verify no" in b for b in blockers)


def test_lookups_append_and_never_erase(js):
    ev.record(js, "Quillstone Systems", "not-found", "first search", date(2026, 9, 26), TODAY)
    ev.record(js, "Quillstone Systems", "enrolled", "second search, legal name", date(2026, 9, 27), TODAY)
    _, body = fm.load(path(js))
    log = body.split("## E-Verify lookups\n", 1)[1].splitlines()
    assert log[0].startswith("- 2026-09-26: not-found (e_verify unknown; was unknown)")
    assert log[1].startswith("- 2026-09-27: enrolled (e_verify yes; was unknown)")
    assert body.count("## E-Verify lookups") == 1


def test_no_check_yet_writes_a_pending_stub_that_keeps_the_gate_closed(js):
    res = ev.record(js, "Brightline Robotics", "enrolled", "searched Brightline Robotics Inc", date(2026, 9, 27), TODAY)
    assert res["created"] and res["slug"] == "brightline-robotics"
    front, body = fm.load(path(js, "brightline-robotics"))
    assert list(front)[:10] == ev.KEY_ORDER
    assert front["verdict"] == "pending" and front["e_verify"] == "yes" and front["company"] == "Brightline Robotics"
    assert body.splitlines()[0] == ev.FIRST_LINE
    _, blockers = gate(js, "brightline-robotics")
    assert any("no clear verdict (pending)" in b for b in blockers)


def test_bad_input_writes_nothing(js):
    before = path(js).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="after today"):
        ev.record(js, "Quillstone Systems", "enrolled", "x", date(2026, 9, 28), TODAY)
    with pytest.raises(ValueError, match="--source"):
        ev.record(js, "Quillstone Systems", "enrolled", "   ", date(2026, 9, 27), TODAY)
    with pytest.raises(ValueError, match="--status"):
        ev.record(js, "Quillstone Systems", "no", "x", date(2026, 9, 27), TODAY)
    ev.record(js, "Quillstone Systems", "enrolled", "x", date(2026, 9, 27), TODAY, dry_run=True)
    assert path(js).read_text(encoding="utf-8") == before


def test_cli_record_and_show(js, capsys):
    assert ev.main(["--root", str(js), "record", "Quillstone Systems", "--status", "enrolled", "--source",
                    "searched the legal name", "--date", "2026-09-27"], today=TODAY) == 0
    out = capsys.readouterr().out
    assert "updated legal/checks/quillstone-systems.md: e_verify unknown -> yes" in out
    assert "re-run visa-check company quillstone-systems" in out
    assert ev.main(["--root", str(js), "show", "quillstone-systems"], today=TODAY) == 0
    assert "e_verify: yes" in capsys.readouterr().out
    assert ev.main(["--root", str(js), "record", "Quillstone Systems", "--status", "enrolled", "--source", "x",
                    "--date", "2026-10-01"], today=TODAY) == 1
    with pytest.raises(SystemExit):
        ev.main(["--root", str(js), "record", "Quillstone Systems", "--status", "maybe", "--source", "x"], today=TODAY)
