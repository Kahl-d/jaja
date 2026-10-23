"""hunt_lessons.py: parse the Scout's LEARNED lines and DAY REVIEW posts in hub/lanes/HUNT.md into one table (id, date,
source, kind fail|slow|works, the line, SETUP's answer FIX|NO-FIX from hub/lanes/SETUP.md). Read only."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import hunt_lessons as hl  # noqa: E402

L1 = ("LEARNED js-hunt-boards has no receipt for 2026-10-01 at 11:45 PDT | `ls logs/runs/js-hunt-boards/` | the "
      "watchdog should alarm when a 3-hourly routine misses two slots")
L2 = "LEARNED fetch_ats Ashby took 14 minutes for 112 boards | timing in the run log | parallelise the board fetch"
L3 = "LEARNED [works] hunt_score.py score weights put the 3 best roles on top | the review file | keep the weights"
L4 = "LEARNED hiring-post watch found 2 roles the boards missed"

HUNT = f"""# HUNT

## Posts, newest LAST

### 2026-10-01 11:45 PDT · STATUS · to: ORCHESTRATOR
**What**: first sweep.

{L1}
{L2}

### 2026-10-02 18:30 PDT · DAY REVIEW · to: SETUP
- finds by source: boards 12, hiring posts 3
- time sink: Workday boards with no API, 40 minutes
- broken: the JD capture for Lever returned empty pages

{L3}
{L4}
"""


def tree(root, setup_lines=""):
    lanes = root / "hub" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / "HUNT.md").write_text(HUNT)
    (lanes / "SETUP.md").write_text("# SETUP\n\n" + setup_lines)
    return root


def test_learned_lines_become_rows_with_date_source_kind_and_a_stable_id(root):
    rows = [r for r in hl.collect(tree(root))["rows"] if r["type"] == "LEARNED"]
    assert [(r["date"], r["source"], r["kind"]) for r in rows] == [
        ("2026-10-01", "js-hunt-boards", "fail"), ("2026-10-01", "fetch_ats", "slow"),
        ("2026-10-02", "hunt_score.py", "works"), ("2026-10-02", "hiring-post", "works")]
    assert rows[0]["id"] == hl.lesson_id(L1) and rows[0]["id"].startswith("L-")
    assert rows[0]["what"].startswith("js-hunt-boards has no receipt") and rows[0]["change"].startswith("the watchdog")
    assert rows[3]["evidence"] == "" and rows[3]["change"] == ""          # a line without pipes is kept whole


def test_day_review_bullets_become_rows(root):
    rows = [r for r in hl.collect(tree(root))["rows"] if r["type"] == "DAY REVIEW"]
    assert [(r["date"], r["source"], r["kind"]) for r in rows] == [
        ("2026-10-02", "finds by source", "works"), ("2026-10-02", "time sink", "slow"), ("2026-10-02", "broken", "fail")]


def test_setup_answers_are_matched_by_id(root):
    a = f"answer: {hl.lesson_id(L1)} FIX change set 30: watchdog alarm on two missed slots\n"
    b = f"- answer: {hl.lesson_id(L2)} NO-FIX Ashby speed is the vendor's; not worth a change\n"
    res = hl.collect(tree(root, a + b))
    by = {r["id"]: r for r in res["rows"]}
    assert by[hl.lesson_id(L1)]["answer"] == "FIX" and "watchdog alarm" in by[hl.lesson_id(L1)]["answer_note"]
    assert by[hl.lesson_id(L2)]["answer"] == "NO-FIX"
    assert by[hl.lesson_id(L3)]["answer"] == ""
    assert res["open"] == len(res["rows"]) - 2


def test_cli_table_open_filter_and_json(root, capsys):
    tree(root, f"answer: {hl.lesson_id(L1)} FIX done\n")
    assert hl.main(["--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "| id | date | source | kind | line | answer |" in out and "lessons: 7 (open 6)" in out
    assert "\u2014" not in out
    hl.main(["--root", str(root), "--open"])
    assert hl.lesson_id(L1) not in capsys.readouterr().out
    hl.main(["--root", str(root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["rows"]) == 7 and data["open"] == 6


def test_read_only_and_missing_files(root, capsys):
    tree(root)
    before = {p: p.read_bytes() for p in (root / "hub" / "lanes").iterdir()}
    hl.main(["--root", str(root)])
    assert {p: p.read_bytes() for p in (root / "hub" / "lanes").iterdir()} == before
    empty = root / "empty"
    empty.mkdir()
    assert hl.main(["--root", str(empty)]) == 0
    assert "no data yet" in capsys.readouterr().out


# ----------------------------------------------------------------------------- found on a live file
def test_an_answer_whose_lesson_line_was_edited_is_reported_as_orphaned(root, capsys):
    """The Scout edited its first LEARNED line after SETUP answered it: the id changed, the answer must not vanish silently."""
    tree(root, "answer: L-0badc0de NO-FIX answered before the line was edited\n")
    res = hl.collect(root)
    assert res["orphans"] == [("L-0badc0de", "NO-FIX", "answered before the line was edited")]
    hl.main(["--root", str(root)])
    assert "answers with no matching lesson (edited line?): L-0badc0de" in capsys.readouterr().out


def test_source_skips_a_leading_article():
    rows = hl.parse_hunt("### 2026-10-01 10:00 PDT · S\nLEARNED the researcher subagent has no Bash | x | y\n"
                         "LEARNED The HN thread went up early | x | y\n")
    assert [r["source"] for r in rows] == ["researcher", "HN"]


def test_oldest_open_is_the_first_posted_on_the_same_day(tmp_path):
    import agency_status as ag
    root = tmp_path / "js"
    (root / "hub" / "lanes").mkdir(parents=True)
    later_first_in_hash = ["LEARNED step 4 first posted | a | b", "LEARNED aaa second posted | a | b"]   # ids: f90a7bff > f6350482
    (root / "hub" / "lanes" / "HUNT.md").write_text("### 2026-10-01 10:00 PDT · S\n" + "\n".join(later_first_in_hash) + "\n")
    assert ag.lessons(root)["oldest"] == (hl.lesson_id(later_first_in_hash[0]), "2026-10-01")
