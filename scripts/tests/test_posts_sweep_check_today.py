"""posts_sweep_check.py with no --file (no --file): it checks today's file from --today and --root, and a
file that does not exist yet says so plainly (no traceback, no counts). No network."""
from __future__ import annotations

import hashlib
import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import posts_sweep_check as psc  # noqa: E402

URL = "https://www.linkedin.com/posts/jane-doe_hiring-agents-activity-7380000000000000001-AbCd"
ROW = {"platform": "linkedin", "kind": "hiring_post", "url": URL, "author": "Jane Doe", "author_title": None,
       "author_url": None, "company": "Acme AI", "posted_at": None, "seen_at": "2026-10-06T14:10:00Z",
       "text": "We're hiring", "role_hint": None, "location_hint": None, "query": "li-01",
       "lead_id": hashlib.sha1(URL.encode("utf-8")).hexdigest()[:12]}


def test_a_missing_file_says_no_sweep_file_yet(tmp_path, capsys):
    assert psc.main(["--root", str(tmp_path), "--today", "2026-10-06"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("no sweep file yet:") and "sweep-2026-10-06.jsonl" in out and "errors:" not in out
    assert psc.main(["--file", str(tmp_path / "sweep-2026-10-07.jsonl"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["missing"] is True


def test_today_picks_the_file_under_root(tmp_path, capsys):
    folder = tmp_path / "state" / "posts"
    folder.mkdir(parents=True)
    (folder / "sweep-2026-10-06.jsonl").write_text(json.dumps(ROW) + "\n", encoding="utf-8")
    assert psc.main(["--root", str(tmp_path), "--today", "2026-10-06"]) == 0
    out = capsys.readouterr().out
    assert "errors: 0" in out and "rows: 1" in out and "new: 1" in out


def test_a_bad_today_is_refused(tmp_path, capsys):
    try:
        psc.main(["--root", str(tmp_path), "--today", "yesterday"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("a bad --today must be refused")
