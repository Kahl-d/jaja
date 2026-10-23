"""The batch takes its roles from the READY rows of the generated NEXT-UP list (never a hand-kept queue file), and a
missing list is a plain refusal, not a traceback."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import apply_batch  # noqa: E402
import next_up  # noqa: E402


def test_next_up_gives_the_ready_rows_with_a_folder_best_first_and_their_pdf(tmp_path, monkeypatch):
    rows = [{"state": "PEOPLE FIRST", "slug": "a"}, {"state": "READY", "slug": "b", "pdf": "inline"},
            {"state": "READY", "slug": None}, {"state": "READY", "slug": "c"}, {"state": "READY", "slug": "d"}]
    monkeypatch.setattr(next_up, "build", lambda root, now: {"rows": rows})
    assert apply_batch.next_up_rows(tmp_path, 2) == [("b", "inline"), ("c", "classic")]


def test_next_up_runs_at_the_focused_tier_with_each_rows_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(next_up, "build", lambda root, now: {"rows": [{"state": "READY", "slug": "acme-ai-202610", "pdf": "inline"}]})
    seen = []
    monkeypatch.setattr(apply_batch, "run_one", lambda root, slug, today, pdf, dry, tier: seen.append((slug, pdf, tier))
                        or {"slug": slug, "status": "ready", "notes": []})
    apply_batch.main(["--root", str(tmp_path), "--next-up", "5", "--dry-run"])
    assert seen == [("acme-ai-202610", "inline", "focused")]


def test_next_up_needs_one_or_more(tmp_path, capsys):
    assert apply_batch.main(["--root", str(tmp_path), "--next-up", "0", "--dry-run"]) == 2
    assert apply_batch.main(["--root", str(tmp_path), "--next-up", "-1", "--dry-run"]) == 2


def test_a_missing_list_is_refused_plainly(tmp_path, capsys):
    rc = apply_batch.main(["--root", str(tmp_path), "--file", str(tmp_path / "queue-mass.txt"), "--dry-run"])
    assert rc == 2 and "no such list" in capsys.readouterr().err


def test_no_ready_row_says_why(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(next_up, "build", lambda root, now: {"rows": []})
    assert apply_batch.main(["--root", str(tmp_path), "--next-up", "5", "--dry-run"]) == 2
    assert "no page passes apply_pack yet" in capsys.readouterr().err
