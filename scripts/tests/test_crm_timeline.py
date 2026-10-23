"""People's timeline drawer (page 5): each prospect's "## Timeline" lines, newest first, timed
lines shown in the owner's time zone, an untimed line kept with its date. Read only; the record stays the source."""
from __future__ import annotations

import sys
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402

BODY = """# Pat

## Timeline
- 2026-09-28 · note · created by the research lane
- 2026-09-28T21:30:00Z · out · linkedin-message · intro · "" · Hi Pat, we met at IMS.
- 2026-09-29T08:51:06+00:00 · note · status replied -> in_conversation (manual)

## Notes
- not a timeline line
"""


def test_timeline_lines_newest_first_in_pacific_time(tmp_path):
    p = tmp_path / "prospects" / "pat.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nid: pat\nname: Pat\n---\n" + BODY, encoding="utf-8")
    t = crm.timelines(tmp_path)["pat"]
    assert [x["when"] for x in t] == ["09-29 01:51", "09-28 14:30", "09-28"]
    assert t[1]["text"].startswith("out · linkedin-message · intro") and "not a timeline line" not in str(t)


def test_a_person_with_no_timeline_has_an_empty_list_and_unknown_people_are_absent(tmp_path):
    p = tmp_path / "prospects" / "sam.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nid: sam\nname: Sam\n---\n# Sam\n\n## Timeline\n", encoding="utf-8")
    t = crm.timelines(tmp_path)
    assert t == {"sam": []}
