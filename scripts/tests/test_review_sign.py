"""review_sign.py: the editor signs at the top level, keeps the old signature and a signed copy."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_sign as rs  # noqa: E402
from js_common import fm  # noqa: E402


def test_sign_writes_top_level_fields_and_a_signed_copy(tmp_path):
    (tmp_path / "resume.yaml").write_text("cv: {name: X}\n", encoding="utf-8")
    (tmp_path / "review.md").write_text("---\nverdict: approved\nvariant_hash: abc\nsigned_at: '2026-10-01T20:00:00'\n"
                                        "round_3: {signed_at: keep-me}\n---\n## Findings\n- E-01 FIXED\n", encoding="utf-8")
    meta = rs.sign(tmp_path, "approved", 4, datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc))
    again, body = fm.load(tmp_path / "review.md")
    assert again["round"] == 4 and str(again["signed_at"]).startswith("2026-10-01T21:00:00")
    assert again["round_3"] == {"signed_at": "keep-me"} and again["previous_signature"]["variant_hash"] == "abc"
    assert "E-01 FIXED" in body and (tmp_path / "signed" / f"{meta['variant_hash'][:12]}.yaml").is_file()



def test_cli_now_sets_the_signed_time_for_evals(tmp_path):
    # evals keep their task date, so the CLI takes --now (ISO time with offset) instead of the wall clock
    (tmp_path / "resume.yaml").write_text("cv: {name: X}\n", encoding="utf-8")
    (tmp_path / "review.md").write_text("---\nverdict: pending\n---\n## Findings\n", encoding="utf-8")
    assert rs.main([str(tmp_path), "--verdict", "approved", "--round", "1", "--now", "2026-09-30T10:00:00-07:00"]) == 0
    meta, _ = fm.load(tmp_path / "review.md")
    assert str(meta["signed_at"]).startswith("2026-09-30T10:00:00")
    assert rs.main([str(tmp_path), "--verdict", "approved", "--round", "1", "--now", "yesterday"]) == 2
