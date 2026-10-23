"""jobs_cache.py: jobs/ parsed once, kept on disk.

load(js) returns what fetch_ats.load_jobs returns ({id: record with _body, _path, _jd_text}). Parsed records are kept
in state/cache/dashboard/jobs.pickle (gitignored) keyed by file name with each file's mtime_ns and size; a call stats every file
(cheap) and parses only the new or changed ones, drops deleted ones, and rewrites the cache atomically when anything
moved. A cache it cannot read is rebuilt. The files stay the one source; the cache is a derived copy.
"""
from __future__ import annotations

import os
import pickle
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from fetch_ats import jd_text_from_body  # noqa: E402
from js_common import fm  # noqa: E402

VERSION = 1


def parse(path: Path) -> dict[str, Any] | None:
    try:
        meta, body = fm.load(path)
    except (OSError, ValueError):
        return None                                    # an unreadable record is skipped, as iter_records skips it
    rec = dict(meta)
    rec.setdefault("id", path.stem)
    rec["_body"], rec["_jd_text"] = body, jd_text_from_body(body)
    return rec


def load(js: Path) -> dict[str, dict[str, Any]]:
    js = Path(js)
    folder, store = js / "jobs", js / "state" / "cache" / "dashboard" / "jobs.pickle"
    try:
        cached = pickle.loads(store.read_bytes())
        entries = cached["entries"] if cached.get("version") == VERSION else {}
    except Exception:                                  # missing or unreadable: rebuild
        entries = {}
    out, fresh, changed = {}, {}, False
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = entries.get(path.name)
        if hit and hit[0] == key:
            rec = hit[1]
        else:
            rec, changed = parse(path), True
        if rec is None:
            continue
        fresh[path.name] = (key, rec)
        out[path.stem] = {**rec, "_path": path}
    if changed or set(fresh) != set(entries):
        store.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=store.parent)
        os.write(fd, pickle.dumps({"version": VERSION, "entries": fresh}, protocol=pickle.HIGHEST_PROTOCOL))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, store)
    return out
