#!/usr/bin/env python3
"""serve.py: the job agency dashboard, a local app. Python stdlib http.server, bound to 127.0.0.1 only.

    python3 dashboard/serve.py [--port 8765] [--root JS] [--os-root OS] [--no-open]

Then open http://127.0.0.1:8765/ . The page reads one JSON snapshot (dash_api.build) and pages the jobs table from the
server. Postings (jobs/, about 5,000 files) load once in the background and are kept until Refresh; everything else is
rebuilt whenever an application, prospect, pursuit, NEXT-UP, the clock or the dashboard's tag file changes on disk.

API (JSON):
  GET  /api/snapshot              every view but the jobs table (waits for the first build)
  GET  /api/jobs?q=&fresh=1&...   the jobs table, filtered and paged (dash_api.query_jobs)
  POST /api/refresh               reload postings and rebuild
  POST /api/preview  {action}     the exact command a write would run; runs nothing
  POST /api/write    {action, confirm: true}   runs that command (the agency's own script), then rebuilds
POSTs need the page's token header (X-Dashboard-Token) and a Host of 127.0.0.1 or localhost: another site in the
browser cannot write. Nothing outward exists: no send, post, submit or connector call.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dash_api  # noqa: E402

STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
          #: one shell; the cockpit is its first tab (cockpit.js on the component library ui.js)
          "/ui.js": ("ui.js", "text/javascript; charset=utf-8"), "/ui.css": ("ui.css", "text/css; charset=utf-8"),
          "/cockpit.js": ("cockpit.js", "text/javascript; charset=utf-8"),
          "/cockpit.css": ("cockpit.css", "text/css; charset=utf-8"),
          #: the Portals page
          "/portals.js": ("portals.js", "text/javascript; charset=utf-8"), "/portals.css": ("portals.css", "text/css; charset=utf-8")}
REDIRECTS = {"/cockpit": "/#cockpit"}          # the old standalone route (S6 landed as the first tab)
MAX_BODY = 16 * 1024
WATCH = ("applications", "prospects", "pursuits", "hub/NEXT-UP.md", "clock.yaml", "state/dashboard-tags.yaml",
         "state/package-only.yaml", "targets/companies.yaml", "targets/people-watch.yaml", "touches",
         "state/recruiter/portal-inventory.yaml", "state/recruiter", "state/recruiter/portal-content")   #: the Portals page


class Cache:
    """One snapshot at a time. Postings are cached until refresh(); the rest is rebuilt when the fingerprint of the
    light sources (mtimes of the files under WATCH) changes."""

    def __init__(self, js: Path, os_root: Path) -> None:
        self.js, self.os_root = js, os_root
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] | None = None
        self.jobs_at: float | None = None
        self.snapshot: dict[str, Any] | None = None
        self.print_ = None
        self.error: str | None = None
        self.saved = js / "state" / "cache" / "dashboard" / "snapshot.json"   # the last finished snapshot (ORCH fix 1)
        self.building = False

    def _save(self, snap: dict[str, Any]) -> None:
        import json
        import os
        import tempfile
        self.saved.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.saved.parent)
        os.write(fd, json.dumps({k: v for k, v in snap.items() if k != "_jobs"}, default=str).encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.saved)

    def _refresh(self) -> None:
        try:
            snap = self.get()
            self._save(snap)
        except Exception as exc:  # noqa: BLE001 - the page keeps the saved snapshot and shows the error
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.building = False

    def quick(self) -> dict[str, Any]:
        """Home fast on a cold start: the snapshot in memory when current; else, before the first
        build, the last saved one at once, marked stale with its as_of time, while one background build runs; with
        nothing saved, build now."""
        if self.snapshot is not None and self.fingerprint() == self.print_:
            return self.snapshot
        if self.snapshot is None and self.saved.is_file():
            import json
            try:
                old = json.loads(self.saved.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = None
            if old is not None:
                if not self.building:
                    self.building = True
                    threading.Thread(target=self._refresh, daemon=True).start()
                return {**old, "stale": True, "as_of": old.get("generated_at"), "refresh_error": self.error}
        snap = self.get()
        self._save(snap)
        return snap

    def fingerprint(self) -> tuple:
        out = []
        for rel in WATCH:
            p = self.js / rel
            if p.is_file():
                out.append((rel, p.stat().st_mtime_ns))
            elif p.is_dir():
                out.append((rel, max((f.stat().st_mtime_ns for f in p.rglob("*.md")), default=0),
                            max((f.stat().st_mtime_ns for f in p.glob("*.jsonl")), default=0),
                            sum(1 for _ in p.iterdir())))
        return tuple(out)

    def get(self, force: bool = False) -> dict[str, Any]:
        with self.lock:
            if force or self.jobs is None:
                self.jobs = dash_api.load_jobs(self.js)
                self.jobs_at = time.time()
                self.snapshot = None
            fp = self.fingerprint()
            if self.snapshot is None or fp != self.print_:
                self.snapshot = dash_api.build(self.js, self.os_root, datetime.now(timezone.utc), jobs=self.jobs,
                                               load=False)
                self.snapshot["jobs_loaded_at"] = datetime.fromtimestamp(self.jobs_at or time.time(),
                                                                         timezone.utc).isoformat()
                self.print_ = fp
            return self.snapshot


_COCKPIT: dict[str, Any] = {}


COCKPIT_WATCH = ("outbox", "state/harvest")               # the cockpit's own sources beside the snapshot's
COCKPIT_TTL_S = 300                                         # time-based values (waiting days, expiry, the clock)


def _cockpit_key(cache: Cache, snap: dict[str, Any]) -> tuple:
    """Rebuilt when the snapshot changes, when the outbox, the mail harvest, the approvals or the run receipts change,
    or every five minutes (review CS6 F4: a new approved note and a failed hunt run stayed invisible)."""
    def newest(folder: Path, pattern: str) -> int:
        return max((f.stat().st_mtime_ns for f in folder.glob(pattern)), default=0) if folder.is_dir() else 0
    runs = cache.os_root / "logs" / "runs"
    return (id(snap), *(newest(cache.js / w, "*") for w in COCKPIT_WATCH),
            newest(cache.os_root / "work" / "approvals", "*.md"), newest(runs / "js-hunt-boards", "*.md"),
            newest(runs / "watchdog", "*.md"), int(time.time() // COCKPIT_TTL_S))


def cockpit_view(cache: Cache) -> dict[str, Any]:
    """The cockpit's data, rebuilt when its key changes (dashboard/cockpit.py)."""
    import cockpit
    try:
        snap = cache.get()
    except Exception as exc:  # noqa: BLE001 - the cockpit names it and shows what it can, never a 500
        return {"errors": [f"the records could not be read ({type(exc).__name__}: {exc})"], "next": [], "more": [],
                "pipeline": [], "clock": None, "mode": None}
    key = _cockpit_key(cache, snap)
    if _COCKPIT.get("key") != key:
        try:
            import connectors_doctor as cd
            import gmail_auth
            report = cd.check_all(cache.js, cache.os_root, gmail_auth.config_dir().expanduser(), dict(os.environ),
                                  datetime.now(timezone.utc), None)
            records = report.get("connectors") if isinstance(report, dict) else None
        except Exception:  # noqa: BLE001 - the Machine line says the connectors were not read
            records = None
        _COCKPIT.update({"key": key, "view": cockpit.build(cache.js, cache.os_root, datetime.now(timezone.utc), snap,
                                                            jobs=cache.jobs, connectors=records)})
    return _COCKPIT["view"]


def make_handler(cache: Cache, token: str, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "JobAgencyDashboard/1"

        def log_message(self, fmt: str, *args: Any) -> None:     # quiet: one line per write only
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in hosts

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._json(403, {"error": "host not allowed"})
            url = urlparse(self.path)
            if url.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if url.path in REDIRECTS:
                self.send_response(302)
                self.send_header("Location", REDIRECTS[url.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                body = (HERE / name).read_bytes()
                if name == "index.html":
                    body = body.replace(b"{{TOKEN}}", token.encode())
                    try:                                    # the Obsidian vault name and where the data folder sits in it
                        rel = Path(cache.js).resolve().relative_to(Path(cache.os_root).resolve()).as_posix()
                        vault = Path(cache.os_root).resolve().name
                    except ValueError:
                        rel, vault = "", ""
                    body = body.replace(b'name="dash-vault" content=""', f'name="dash-vault" content="{vault}"'.encode())
                    body = body.replace(b'name="dash-data-rel" content=""', f'name="dash-data-rel" content="{rel}"'.encode())
                return self._send(200, body, ctype)
            try:
                if url.path == "/api/snapshot":
                    snap = cache.quick()
                    return self._json(200, {k: v for k, v in snap.items() if k != "_jobs"})
                if url.path == "/api/jobs":
                    return self._json(200, dash_api.query_jobs(cache.get()["_jobs"], dict(parse_qsl(url.query))))
                if url.path == "/api/cockpit":
                    return self._json(200, cockpit_view(cache))
            except Exception as exc:  # noqa: BLE001 - show the error on the page, keep serving
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok() or self.headers.get("X-Dashboard-Token") != token:
                return self._json(403, {"error": "forbidden"})
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(415, {"error": "JSON only"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            path = urlparse(self.path).path
            try:
                if path == "/api/refresh":
                    cache.get(force=True)
                    return self._json(200, {"ok": True})
                action = body.get("action") or {}
                if path == "/api/preview":
                    return self._json(200, {"command": dash_api.display(dash_api.command_for(action, cache.js))})
                if path == "/api/write":
                    if body.get("confirm") is not True:
                        return self._json(400, {"error": "a write needs confirm: true (the confirm click)"})
                    result = dash_api.run_write(action, cache.js)
                    print(f"write: exit {result['exit']} · {result['command']}", flush=True)
                    return self._json(200, result)
            except dash_api.WriteRefused as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._json(404, {"error": "not found"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", type=Path, default=dash_api.JS_DEFAULT, help="job-search folder (default: this one)")
    ap.add_argument("--os-root", type=Path, default=None, help="runtime root, the Obsidian vault (default: derived)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)
    js = args.root.resolve()
    os_root = (args.os_root or dash_api.jc.os_root_for(js)).resolve()
    cache = Cache(js, os_root)
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(cache, token, args.port))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"job agency dashboard: {url} (root {js}; vault {os_root.name}); Ctrl+C stops it", flush=True)
    threading.Thread(target=cache.get, daemon=True).start()          # load postings in the background
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
