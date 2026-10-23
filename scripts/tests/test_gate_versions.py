"""gate_versions.py: one line naming the version of each page gate (F46)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_versions as gv  # noqa: E402


def test_every_gate_has_a_version():
    v = gv.versions()
    assert set(gv.GATES) <= set(v) and all(len(x) == 12 for x in v.values())
