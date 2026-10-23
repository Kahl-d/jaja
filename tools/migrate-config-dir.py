#!/usr/bin/env python3
"""Move the connector tokens of an earlier install to ~/.config/jaja/ and rename their bookkeeping keys.

    python3 tools/migrate-config-dir.py --from ~/.config/<old-folder-name>            # dry run
    python3 tools/migrate-config-dir.py --from ~/.config/<old-folder-name> --old-prefix <old> --apply    # copy, chmod 600, rename keys

The old folder is left in place; delete it yourself once the new one works. Token JSON files get
`<old>_authorized_at` / `<old>_reauth_due` renamed to `agency_authorized_at` / `agency_reauth_due`.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", default=str(Path.home() / ".config" / "jaja"))
    ap.add_argument("--old-prefix", required=True, help="prefix of the old bookkeeping keys")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    src, dst = Path(a.src).expanduser(), Path(a.to).expanduser()
    if not src.is_dir():
        print(f"no such folder: {src}")
        return 1
    n = 0
    for f in sorted(p for p in src.iterdir() if p.is_file()):
        n += 1
        print(("copy " if a.apply else "would copy ") + f.name)
        if not a.apply:
            continue
        dst.mkdir(parents=True, exist_ok=True, mode=0o700)
        out = dst / f.name
        shutil.copy2(f, out)
        if f.suffix == ".json":
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for old, new in ((f"{a.old_prefix}_authorized_at", "agency_authorized_at"), (f"{a.old_prefix}_reauth_due", "agency_reauth_due")):
                        if old in data:
                            data[new] = data.pop(old)
                    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            except (ValueError, OSError):
                pass
        out.chmod(0o600)
    print(f"{n} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
