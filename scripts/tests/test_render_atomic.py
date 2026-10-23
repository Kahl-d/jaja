"""F80 (SETUP trust test 2026-10-01, SETUP-H2 CS70): a failed render ("FAIL page budget: 2 pages > --max-pages 1")
overwrote V's good PDF and meta.yaml with the failed state. Now the build happens in a staging folder; V's PDF, docx
and meta.yaml are replaced only on success; a failure leaves V as it was, says so, and keeps the failed draft (PDF and
a failed meta.yaml) in a temp folder whose path it prints."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from test_render_resume import BROKEN, EVIDENCE, GOOD, needs_rendercv, rr  # noqa: F401


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


@needs_rendercv
def test_a_failed_render_leaves_the_good_build_untouched(tmp_path, capsys):
    v = tmp_path / "V"
    assert rr.main(["render", "--input", str(GOOD), "--out", str(v), "--evidence", str(EVIDENCE)]) == 0
    good = _snapshot(v)
    assert "meta.yaml" in good and any(n.endswith(".pdf") for n in good)
    capsys.readouterr()
    rc = rr.main(["render", "--input", str(BROKEN), "--out", str(v), "--force", "--evidence", str(EVIDENCE)])
    msg = "".join(capsys.readouterr())
    assert rc == 1 and "page budget" in msg
    assert _snapshot(v) == good                                         # byte for byte, nothing replaced or added
    assert f"{v} left as it was" in msg
    m = re.search(r"failed draft kept at (\S+)", msg)
    draft = Path(m.group(1))
    meta = yaml.safe_load((draft / "meta.yaml").read_text())
    assert meta["status"] == "failed" and (draft / meta["pdf"]).is_file()


@needs_rendercv
def test_a_failed_first_render_writes_nothing_into_v(tmp_path, capsys):
    v = tmp_path / "V"
    assert rr.main(["render", "--input", str(BROKEN), "--out", str(v), "--force", "--evidence", str(EVIDENCE)]) == 1
    assert not v.exists() or not any(v.iterdir())


@needs_rendercv
def test_success_replaces_the_build(tmp_path):
    v = tmp_path / "V"
    assert rr.main(["render", "--input", str(GOOD), "--out", str(v), "--evidence", str(EVIDENCE)]) == 0
    (v / "meta.yaml").write_text("status: stale\n")
    assert rr.main(["render", "--input", str(GOOD), "--out", str(v), "--evidence", str(EVIDENCE)]) == 0
    assert yaml.safe_load((v / "meta.yaml").read_text())["status"] == "ok"
    assert not [p for p in v.iterdir() if p.name.startswith(".")]      # no staging left behind
