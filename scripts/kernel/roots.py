"""The three roots the kernel scripts work against, as one small value.

    code      this repo: agents/, skills/, lanes/, routines/, schemas/, templates/, hooks/, scripts/
    data      your data folder: profile/, applications/, prospects/, jobs/, hub/, state/, config/ ...
    runtime   the approval queue (work/approvals), run receipts (logs/runs), the registries and the secret pointers

`roots(None)` reads them from scripts/agency_paths.py (environment, then ~/.config/jaja/config.yaml).
`roots(some_path)` is the single-tree form every script's `--root` flag and every test uses: one folder that holds
all three, laid out like the repo (agents/, schemas/, applications/, work/approvals/, logs/runs/ ...).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_paths as _ap  # noqa: E402


@dataclass(frozen=True)
class Roots:
    code: Path
    data: Path
    runtime: Path

    @classmethod
    def single(cls, root: str | Path) -> "Roots":
        p = Path(root).resolve()
        return cls(p, p, p)

    @classmethod
    def configured(cls) -> "Roots":
        return cls(_ap.CODE_ROOT, _ap.DATA_ROOT, _ap.RUNTIME_ROOT)


def roots(value: "Roots | str | Path | None" = None) -> Roots:
    """A Roots from None (the configured roots), a Roots, or one folder holding all three."""
    if value is None:
        return Roots.configured()
    if isinstance(value, Roots):
        return value
    return Roots.single(value)


def add_root_args(ap) -> None:
    """The common flags: --root (one folder for all three), or --code / --data / --runtime one by one."""
    ap.add_argument("--root", help="one folder holding code, data and runtime (default: the configured roots)")
    ap.add_argument("--code", help="the code root (default: this repo)")
    ap.add_argument("--data", help="the data folder (default: JAJA_DATA / config data_root)")
    ap.add_argument("--runtime", help="the runtime root (default: JAJA_RUNTIME / <data>/runtime)")


def from_args(args) -> Roots:
    base = roots(args.root) if getattr(args, "root", None) else Roots.configured()
    return Roots(Path(args.code).resolve() if getattr(args, "code", None) else base.code,
                 Path(args.data).resolve() if getattr(args, "data", None) else base.data,
                 Path(args.runtime).resolve() if getattr(args, "runtime", None) else base.runtime)
