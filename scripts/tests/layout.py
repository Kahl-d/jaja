"""The project layout as the tests see it: one object, `JS`, whose children are found in the right root.

The department used to be one folder (code and records side by side). In the repo the code lives in the repo
(CODE_ROOT) and the records in the data folder (DATA_ROOT, JOB_AGENCY_DATA). `JS / "profile"` therefore resolves in
the data folder and `JS / "scripts"` in the repo, so a test says what it means and does not care where each part lives.
`JS` itself is the data folder (what `--root` takes). Old folder names map to their new homes (roles -> agents,
.agents/skills -> skills, AGENCY-CHARTER.md -> docs/).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_paths as _ap  # noqa: E402

CODE_NAMES = {"scripts", "dashboard", "mcp", "agents", "role-references", "lanes", "routines", "playbooks", "schemas", "templates",
              "evals", "docs", "views", "hooks", "skills", "tools", "examples", "AGENTS.md", "CLAUDE.md"}
RENAMES = {"roles": "agents", ".agents": "", "AGENCY-CHARTER.md": "docs/AGENCY-CHARTER.md"}


class Layout(type(Path())):
    def __truediv__(self, key):  # noqa: D105
        parts = Path(str(key)).parts
        if not parts or Path(str(key)).is_absolute():
            return Path(self) / key
        first = parts[0]
        if first in RENAMES:
            new = RENAMES[first]
            rest = parts[1:]
            if first == "roles" and rest[:1] == ("references",):          # roles/references/<x> -> role-references/<x>
                return _ap.CODE_ROOT.joinpath("role-references", *rest[1:])
            if first == ".agents":                           # .agents/skills/<x> -> skills/<x>
                rest = rest[1:] if rest and rest[0] == "skills" else rest
                return _ap.CODE_ROOT.joinpath("skills", *rest)
            return _ap.CODE_ROOT.joinpath(new, *rest)
        if first in CODE_NAMES:
            return _ap.CODE_ROOT / key
        return Path(self) / key

    joinpath = __truediv__


JS = Layout(_ap.DATA_ROOT)
CODE = _ap.CODE_ROOT
DATA = _ap.DATA_ROOT
OS_REAL = _ap.RUNTIME_ROOT
