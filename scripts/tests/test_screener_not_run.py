"""screener_check.py --not-run: while roles/ai-screener.md does not exist, the gap is recorded and does not block;
once the role exists, NOT RUN is a finding and --not-run refuses."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import screener_check as sc  # noqa: E402


def tree(tmp_path: Path, role: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "js"
    v = root / "resumes" / "variants" / "acme-ai-202610-b76"
    v.mkdir(parents=True)
    (v / "resume.yaml").write_text("basics: {name: Test}\n", encoding="utf-8")
    if role:
        (root / "roles").mkdir()
        (root / "roles" / "ai-screener.md").write_text("---\nname: ai-screener\n---\n", encoding="utf-8")
    return root, v


def test_not_run_records_the_gap_and_passes_while_the_role_is_absent(tmp_path, capsys):
    root, v = tree(tmp_path)
    assert sc.main([str(v), "--not-run", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "screener NOT RUN recorded" in out and "OK (screener NOT RUN: role not created; a recorded gap, not a PASS)" in out
    text = (v / "screener.md").read_text(encoding="utf-8")
    assert "verdict: NOT RUN" in text and "not a PASS" in text and sc._sha(v) in text
    assert sc.check(v, root) == [] and sc.main([str(v), "--root", str(root)]) == 0


def test_a_missing_file_without_the_role_says_how_to_record_the_gap(tmp_path):
    root, v = tree(tmp_path)
    [f] = sc.check(v, root)
    assert "role is not created yet" in f and "--not-run" in f


def test_not_run_goes_stale_when_the_page_changes(tmp_path):
    root, v = tree(tmp_path)
    sc.main([str(v), "--not-run", "--root", str(root)])
    (v / "resume.yaml").write_text("basics: {name: Changed}\n", encoding="utf-8")
    [f] = sc.check(v, root)
    assert "record it again with --not-run" in f
    assert sc.main([str(v), "--not-run", "--root", str(root)]) == 0      # re-recording replaces a NOT RUN file


def test_once_the_role_exists_not_run_is_a_finding_and_refused(tmp_path, capsys):
    root, v = tree(tmp_path)
    sc.main([str(v), "--not-run", "--root", str(root)])
    (root / "roles").mkdir()
    (root / "roles" / "ai-screener.md").write_text("---\nname: ai-screener\n---\n", encoding="utf-8")
    [f] = sc.check(v, root)
    assert "exists now: run the ai-screener" in f
    assert sc.main([str(v), "--not-run", "--root", str(root)]) == 2
    assert "refused" in capsys.readouterr().err


def test_not_run_never_overwrites_a_real_verdict(tmp_path, capsys):
    root, v = tree(tmp_path)
    real = "---\nverdict: REVISE\nscore: 5\n---\n"
    (v / "screener.md").write_text(real, encoding="utf-8")
    sc.main([str(v), "--not-run", "--root", str(root)])
    assert (v / "screener.md").read_text(encoding="utf-8") == real
    assert "kept" in capsys.readouterr().err


def test_the_role_check_reads_the_real_tree():
    assert sc.role_exists() == (sc.JS_ROOT / "roles" / "ai-screener.md").is_file()
