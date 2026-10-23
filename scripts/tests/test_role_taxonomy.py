"""role_taxonomy.py: the market's role categories from the job records."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import role_taxonomy as rt  # noqa: E402

LENS = "---\nfamily: {f}\ntitles_covered: {t}\nstatus: draft\n---\n" + "".join(f"## {n}. x\nx\n" for n in range(1, 8))


def make(tmp_path):
    (tmp_path / "profile" / "lenses").mkdir(parents=True)
    (tmp_path / "profile" / "lenses" / "agent-harness.md").write_text(LENS.format(f="agent-harness", t='["*Agent Harness*"]'))
    (tmp_path / "profile" / "lenses" / "evaluation.md").write_text(LENS.format(f="evaluation", t='["*Evaluation*"]'))
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    rows = [("a1", "Software Engineer, Agent Harness", "2026-09-25"), ("a2", "Agent Harness Engineer", "2026-09-28"),
            ("e1", "Research Engineer, Evaluation", "2026-08-20"), ("i1", "Inference Engineer, LLM Serving", "2026-09-20"),
            ("i2", "Senior Inference Engineer", "2026-09-21")]
    for jid, title, seen in rows:
        (jobs / f"{jid}.md").write_text(f"---\ntitle: \"{title}\"\nfirst_seen: '{seen}'\nstatus: discovered\n---\nbody\n")
    return tmp_path


def test_counts_growth_and_uncovered(tmp_path):
    res = rt.taxonomy(make(tmp_path), date(2026, 10, 1))
    row = {r["family"]: r for r in res["table"]}
    assert row["agent-harness"]["last_30d"] == 2 and row["evaluation"]["prior_30d"] == 1
    assert res["uncovered"] == 2
    assert ("inference", 2) in res["uncovered_words"]
    assert "Role taxonomy" in rt.render(res)


def test_write(tmp_path):
    root = make(tmp_path)
    assert rt.main(["--root", str(root), "--today", "2026-10-01", "--write"]) == 0
    assert (root / "state" / "recruiter" / "role-taxonomy-2026-10-01.md").is_file()
