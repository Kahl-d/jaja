"""Test harness for the discovery/tracking scripts (fetch_ats, hn_hiring, adzuna, dedup, h1b_lookup,
fit_gates, tracker, dashboard, pipeline). Imported by every test_disc module with
`from disc_harness import *` so it does not depend on (or collide with) a shared conftest.py.

Network is never used: an autouse fixture blocks sockets, and every fetcher receives a
FakeHttp that serves synthetic fixtures shaped like the public ATS endpoints (see fixtures/disc/)."""
from __future__ import annotations

import json
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "disc"
NOW = datetime(2026, 9, 24, 13, 0, 0, tzinfo=timezone.utc)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*_a, **_k):
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


class FakeHttp:
    """Serves fixture payloads by (METHOD, url). A route value may be a payload or a callable
    taking the params/payload. Unknown routes fail loudly so a test can never go online."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None):
        self.routes: dict[tuple[str, str], Any] = dict(routes or {})
        self.calls: list[tuple[str, str, Any]] = []

    def _serve(self, method: str, url: str, arg: Any) -> Any:
        self.calls.append((method, url, arg))
        if (method, url) not in self.routes:
            raise AssertionError(f"unexpected {method} {url}")
        value = self.routes[(method, url)]
        if isinstance(value, Exception):
            raise value
        return value(arg) if callable(value) else value

    def get_json(self, url: str, params: dict | None = None) -> Any:
        return self._serve("GET", url, params)

    def post_json(self, url: str, payload: dict) -> Any:
        return self._serve("POST", url, payload)

    def get_text(self, url: str, headers: dict | None = None) -> tuple[int, str]:
        """A route value is the page text (status 200) or a (status, text) pair."""
        value = self._serve("GET", url, headers)
        return value if isinstance(value, tuple) else (200, value)

    def download_to(self, url: str, dest: Path, params: dict | None = None) -> Path:
        src = self._serve("DOWNLOAD", url, params)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest


GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LEVER = "https://api.lever.co/v0/postings/{}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"
WD_BASE = "https://chipco.wd5.myworkdayjobs.com/wday/cxs/chipco/CHIPCOExternalCareerSite"


def ats_routes() -> dict[tuple[str, str], Any]:
    """Routes for the five fixture boards (greenhouse, 2x lever, ashby, workday)."""
    p0 = load_fixture("workday_chipco_jobs_p0.json")
    p1 = load_fixture("workday_chipco_jobs_p1.json")
    routes: dict[tuple[str, str], Any] = {
        ("GET", GH.format("labco")): load_fixture("greenhouse_labco.json"),
        ("GET", LEVER.format("robotco")): load_fixture("lever_robotco.json"),
        ("GET", LEVER.format("dronecoai")): load_fixture("lever_dronecoai.json"),
        ("GET", ASHBY.format("modelco")): load_fixture("ashby_modelco.json"),
        ("POST", WD_BASE + "/jobs"): lambda payload: p0 if payload["offset"] == 0 else p1,
    }
    for page in (p0, p1):
        for post in page["jobPostings"]:
            jr = post["bulletFields"][0]
            path = FIXTURES / f"workday_chipco_detail_{jr}.json"
            if path.exists():
                routes[("GET", WD_BASE + post["externalPath"])] = json.loads(path.read_text())
    return routes


def hn_routes() -> dict[tuple[str, str], Any]:
    base = "https://hacker-news.firebaseio.com/v0"
    routes: dict[tuple[str, str], Any] = {
        ("GET", f"{base}/user/whoishiring.json"): load_fixture("hn_user_whoishiring.json"),
    }
    for p in FIXTURES.glob("hn_item_*.json"):
        item_id = p.stem.split("_")[-1]
        routes[("GET", f"{base}/item/{item_id}.json")] = json.loads(p.read_text())
    return routes


COMPANIES_YAML = """\
companies:
  - name: Labco
    slug: labco
    tier: dream
    ats_vendor: greenhouse
    board_token: labco
    careers_url: https://www.labco.example.test/careers
    sponsorship_stance: sponsors
    h1b_approvals_recent: 143
    notes: ""
  - {name: Robotco, slug: robotco, tier: strong, ats_vendor: lever, board_token: robotco}
  - {name: Modelco, slug: modelco, tier: dream, ats_vendor: ashby, board_token: modelco}
  - name: CHIPCO
    slug: chipco
    tier: strong
    ats_vendor: workday
    board_token: chipco.wd5/CHIPCOExternalCareerSite
    careers_url: https://chipco.wd5.myworkdayjobs.com/CHIPCOExternalCareerSite
  - {name: Droneco AI, slug: droneco-ai, tier: fallback, ats_vendor: lever, board_token: dronecoai}
  - {name: Legacy Corp, slug: legacy-corp, tier: fallback, ats_vendor: icims, board_token: legacy}
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An empty department root (folders deliberately absent)."""
    r = tmp_path / "job-search"
    r.mkdir()
    return r


@pytest.fixture
def os_root(tmp_path: Path) -> Path:
    r = tmp_path / "workspace"
    r.mkdir()
    return r


@pytest.fixture
def root_with_targets(root: Path) -> Path:
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    return root


@pytest.fixture
def fake_http() -> Callable[..., FakeHttp]:
    def make(*route_sets: dict) -> FakeHttp:
        merged: dict = {}
        for rs in route_sets:
            merged.update(rs)
        return FakeHttp(merged)

    return make


__all__ = [
    "ASHBY", "COMPANIES_YAML", "FIXTURES", "FakeHttp", "GH", "LEVER", "NOW", "WD_BASE",
    "_no_network", "ats_routes", "fake_http", "hn_routes", "load_fixture", "os_root", "root",
    "root_with_targets",
]
