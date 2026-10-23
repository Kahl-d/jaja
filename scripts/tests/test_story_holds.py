"""Every STAR story in prep/stories/ is check_holds clean.

prep_focus and mass_prep put stories into prep packs the owner reads aloud, so held text in a story reaches an
interview. One test per story: a new story with held text fails by name. Hits are named without their text
(the real holds include private ones), as in test_check_holds.test_the_real_profile_is_clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import check_holds as chk  # noqa: E402
import profile_titles as pt  # noqa: E402
import held_safe as hs  # noqa: E402  (tests/: name a hit without printing a private hold)

from layout import JS  # noqa: E402
REAL_HOLDS = JS / "profile" / "PROFILE-HOLDS.md"
pytestmark = pytest.mark.real_records     # reads the data folder's story bank and holds
STORIES = sorted((JS / "prep" / "stories").glob("*.md"))


def test_the_story_bank_is_not_empty():
    assert STORIES, "prep/stories/ has no stories: missing is not empty"


@pytest.mark.parametrize("story", STORIES, ids=[p.stem for p in STORIES])
def test_story_has_no_held_text(story, capsys):
    code = chk.main([str(story)])
    capsys.readouterr()
    found = chk.check_file(story, pt.load_holds(REAL_HOLDS))
    bad = [f"{story.name}:{h.line}: {h.pass_} {hs.shown(h.held)} ({h.kind} {h.ref})" for h in found if not h.gap]
    assert code == 0 and not bad, f"check_holds exit {code}; {len(bad)} hit(s):\n" + "\n".join(bad)
