"""card_audit.py: a read-only
audit of the role cards, lane agents and skills: size, named paths exist, cited rulings exist and are not superseded,
skills and handoff roles exist, role eval sets exist, no stale file names."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import card_audit as ca  # noqa: E402

RULINGS = """rulings:
- {id: R-4, status: standing, quote: no sends without a yes}
- {id: R-6, status: superseded, superseded_by: R-155, quote: old}
- {id: R-1, status: history, quote: old authority}
- {id: R-155, status: standing, quote: one at a time}
"""
GOOD = """---
name: good-role
skills: [score-fit]
reports_to: recruiter
status: probation
---
# Good
Run `python3 scripts/real.py --x` and read `profile/evidence.yaml` and `scripts/kernel/sync.py` (R-4, R-155).
Placeholders are skipped: `applications/<slug>/fit.md`, `jobs/*.md`.
"""
BAD = """---
name: bad-role
skills: [score-fit, ghost-skill]
reports_to: nobody-role
status: probation
---
# Bad
Hand off to the job-lead. Read `scripts/missing.py` and `research/nope.md`; per R-6 and R-1 and R-999.
Check hub/APPLY-QUEUE.md, then TRACKER.md and progress.md.
""" + ("x" * 13000) + "\n"
ARCHIVED = "---\nname: job-lead\nstatus: archived\n---\n# Old\n"
SKILL = "---\nname: score-fit\ndescription: score\n---\n# Score\nRun `scripts/real.py`.\n"
FAT_SKILL = "---\nname: fat\ndescription: fat\n---\n# Fat\n" + ("y" * 7000) + "\n"
LANE = "---\nname: lane-apply\nskills: [score-fit]\n---\n# Apply lane\nRead `hub/NEXT-UP.md` and hub/APPLY-QUEUE.md.\n"
CASES = "role: good-role\ncases:\n- {id: a}\n- {id: b}\n- {id: c}\n"


def world(tmp_path):
    os_root = tmp_path / "os"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    for d in ("agents", "lanes/apply", "skills/score-fit", "skills/fat", "scripts", "profile", "hub"):
        (js / d).mkdir(parents=True, exist_ok=True)
    (js / "agents" / "good-role.md").write_text(GOOD)
    (js / "agents" / "bad-role.md").write_text(BAD)
    (js / "agents" / "job-lead.md").write_text(ARCHIVED)
    (js / "skills" / "score-fit" / "SKILL.md").write_text(SKILL)
    (js / "skills" / "fat" / "SKILL.md").write_text(FAT_SKILL)
    (js / "lanes" / "apply" / "AGENT.md").write_text(LANE)
    (js / "scripts" / "real.py").write_text("# real\n")
    (js / "profile" / "evidence.yaml").write_text("evidence: {}\n")
    (js / "hub" / "NEXT-UP.md").write_text("# next\n")
    (js / "hub" / "rulings.yaml").write_text(RULINGS)
    (os_root / "scripts" / "kernel").mkdir(parents=True)
    (os_root / "scripts" / "kernel" / "sync.py").write_text("# sync\n")
    (js / "agents" / "recruiter.md").write_text("---\nname: recruiter\n---\n")
    ev = js / "evals" / "good-role"
    (ev / "results").mkdir(parents=True)
    (ev / "cases.yaml").write_text(CASES)
    (ev / "results" / "2026-09-30-a.json").write_text("{}")
    (ev / "results" / "2026-10-01-b.json").write_text("{}")
    return js, os_root


def by_file(rows):
    return {r["file"]: r for r in rows}


def test_a_clean_card_has_no_findings_and_reports_its_eval(tmp_path):
    js, os_root = world(tmp_path)
    r = by_file(ca.audit(js, os_root))["agents/good-role.md"]
    assert r["findings"] == [], r["findings"]
    assert r["eval_cases"] == 3 and r["eval_last_run"] == "2026-10-01"


def test_the_bad_card_reports_every_kind(tmp_path):
    js, os_root = world(tmp_path)
    r = by_file(ca.audit(js, os_root))["agents/bad-role.md"]
    kinds = [f["kind"] for f in r["findings"]]
    text = " | ".join(f["msg"] for f in r["findings"])
    assert "bloat" in kinds and r["kb"] > 12
    assert "scripts/missing.py" in text and "research/nope.md" in text
    assert "R-6" in text and "R-155" in text and "R-1 " in text + " " and "R-999" in text
    assert "ghost-skill" in text and "nobody-role" in text and "job-lead" in text
    assert "hub/APPLY-QUEUE.md" in text and "TRACKER.md" in text and "progress.md" in text
    assert "no eval set" in text
    assert kinds.count("path") == 2 and kinds.count("stale-name") == 3


def test_skills_and_lanes_are_audited_and_archived_cards_skipped(tmp_path):
    js, os_root = world(tmp_path)
    rows = by_file(ca.audit(js, os_root))
    assert "agents/job-lead.md" not in rows
    assert [f["kind"] for f in rows["skills/fat/SKILL.md"]["findings"]] == ["bloat"]
    assert rows["skills/score-fit/SKILL.md"]["findings"] == []
    lane = rows["lanes/apply/AGENT.md"]
    assert [f["kind"] for f in lane["findings"]] == ["stale-name"] and lane["eval_cases"] is None


def test_cli_table_json_and_read_only(tmp_path, capsys):
    js, os_root = world(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert ca.main(["--root", str(js), "--os-root", str(os_root)]) == 1
    out = capsys.readouterr().out
    assert "| file |" in out and "| agents/bad-role.md |" in out and "FINDINGS" in out and "\u2014" not in out
    assert ca.main(["--root", str(js), "--os-root", str(os_root), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {r["file"] for r in data["files"]} >= {"agents/good-role.md", "agents/bad-role.md"} and data["findings"] > 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
