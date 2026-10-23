"""The approved skills list: name -> (evidence ids, role families), read from the config.

The list is the owner's data, not code: policy.skills_map.skills in the config is a list of
[name, evidence ids, role families]. `evidence ids` is free text naming the records that back the skill (with any
note); `role families` is a space-separated set of short family letters that the owner defines for the kinds of role
the skill supports. Order is the order the skills are listed (the top skills first). No config, no skills: SKILLS is
empty and the importers (stage_batch.py, build_linkedin_md.py) handle that.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402


def _load() -> list[tuple[str, str, str]]:
    rows = cfg.get("policy.skills_map.skills", None) or []
    return [(str(r[0]), str(r[1]) if len(r) > 1 else "", str(r[2]) if len(r) > 2 else "")
            for r in rows if isinstance(r, (list, tuple)) and r]


SKILLS = _load()
