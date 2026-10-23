"""F12 (SETUP trust test 2026-10-01, SETUP-H2 CS63): `h1b_lookup.py lookup ... --save-evidence` writes
research/targets/evidence/<company>-h1b-<date>.md (the command, the cache vintage from _manifest.json, the exit
status and the full JSON output), so a company dossier cites a file instead of quoting uncited script output.
stdout stays the JSON; a second run that day with a different result never overwrites the first."""
from __future__ import annotations

import json
import shutil

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FIXTURES

import h1b_lookup as hb  # noqa: E402

CACHE = FIXTURES / "h1b_synth" / "h1b_cache"


def _root(root):
    cache = root / "legal" / "evidence" / "h1b"
    shutil.copytree(CACHE, cache)
    (cache / "_manifest.json").write_text(json.dumps({"files": {"uscis_h1b_fy2026.csv": {
        "fetched_at": "2026-09-26T17:01:01Z", "rows": 3, "source": "https://example.test/fy2026"}}}))
    return root


def test_save_evidence_writes_the_command_vintage_and_full_output(root, capsys):
    _root(root)
    argv = ["--root", str(root), "lookup", "Openlab", "--today", "2026-09-24", "--save-evidence"]
    assert hb.main(argv) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)                                         # stdout is still pure JSON
    p = root / "research" / "targets" / "evidence" / "openlab-h1b-2026-09-24.md"
    assert p.is_file() and str(p.relative_to(root)) in cap.err
    text = p.read_text()
    assert "python3 scripts/h1b_lookup.py lookup Openlab --today 2026-09-24" in text
    assert "uscis_h1b_fy2026.csv: fetched 2026-09-26T17:01:01Z, 3 rows, https://example.test/fy2026" in text
    assert "exit 0" in text and json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0]) == out
    assert "\u2014" not in text


def test_a_rerun_with_a_different_result_never_overwrites(root, capsys):
    _root(root)
    base = ["--root", str(root), "lookup", "Openlab", "--today", "2026-09-24", "--save-evidence"]
    hb.main(base)
    first = (root / "research" / "targets" / "evidence" / "openlab-h1b-2026-09-24.md").read_text()
    hb.main(base)                                                     # same result: same file, nothing new
    assert sorted(p.name for p in (root / "research" / "targets" / "evidence").iterdir()) == ["openlab-h1b-2026-09-24.md"]
    hb.main(base + ["--state", "TX"])                                 # a different result: a -2 file
    names = sorted(p.name for p in (root / "research" / "targets" / "evidence").iterdir())
    assert names == ["openlab-h1b-2026-09-24-2.md", "openlab-h1b-2026-09-24.md"]
    assert (root / "research" / "targets" / "evidence" / "openlab-h1b-2026-09-24.md").read_text() == first


def test_a_missing_source_is_saved_with_its_exit_3(root, capsys):
    root.joinpath("legal", "evidence", "h1b").mkdir(parents=True)     # no cache at all
    assert hb.main(["--root", str(root), "lookup", "Openlab", "--today", "2026-09-24", "--save-evidence"]) == 3
    text = (root / "research" / "targets" / "evidence" / "openlab-h1b-2026-09-24.md").read_text()
    assert "exit 3" in text and "unknown" in text and "no _manifest.json" in text
