"""platform_watch.py: the monthly sweep of the official MCP registry API for new job-search servers: snapshot, diff against the last one, publisher-official servers into platforms.yaml candidates, the rest to the
review list; plus the AI Dev Jobs freshness recheck."""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone

import yaml

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp

import platform_watch as pw  # noqa: E402

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def server(name, desc="job search", status="active", latest=True, site=None):
    return {"server": {"name": name, "title": name.split("/")[-1], "description": desc, "version": "1.0.0",
                       "websiteUrl": site, "remotes": [{"type": "streamable-http", "url": f"https://{name.split('/')[0]}/mcp"}]},
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": status, "isLatest": latest,
                                                                     "publishedAt": "2026-09-20T00:00:00Z"}}}


def routes(pages):
    def serve(params):
        term, cursor = params.get("search"), params.get("cursor")
        return pages.get((term, cursor), {"servers": [], "metadata": {"count": 0}})
    return FakeHttp({("GET", pw.REGISTRY): serve,
                     ("GET", pw.AIDEVJOBS): {"jobs": [{"published_at": "2026-09-14T03:00:00Z"}]}})


def make_root(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "platforms.yaml").write_text(textwrap.dedent("""
        platforms:
          - {id: dice-connector, name: Dice connector, url: https://mcp.dice.com/mcp, alert_senders: []}
          - {id: himalayas, name: Himalayas, url: https://himalayas.app, alert_senders: []}
          - {id: wellfound, name: Wellfound, url: https://wellfound.com/jobs, alert_senders: []}

        # Platforms seen but not yet vetted.
        candidates:
          - {id: old, name: Old, url: '', why: w, access_guess: [], checked_on: 2026-09-01, next_step: n}

        blocked_tools: []
        """).lstrip(), encoding="utf-8")
    return tmp_path


def test_the_sweep_snapshots_diffs_and_files_publisher_official_servers(tmp_path):
    root = make_root(tmp_path)
    prev = root / "state" / "platform_watch"
    prev.mkdir(parents=True)
    (prev / "registry-2026-09-01.json").write_text(json.dumps({"servers": {"io.github.someone/old-jobs": {}}}))
    pages = {("job", None): {"servers": [server("com.wellfound/jobs", site="https://wellfound.com"),
                                         server("io.github.someone/new-jobs"),
                                         server("ai.jobero/job-search", status="deprecated")],
                             "metadata": {"nextCursor": "c1", "count": 3}},
             ("job", "c1"): {"servers": [server("io.github.someone/old-jobs")], "metadata": {"count": 1}}}
    out = pw.run(root, routes(pages), NOW, terms=["job"])
    assert out["seen"] == 3 and out["deprecated"] == 1
    assert out["new"] == ["com.wellfound/jobs", "io.github.someone/new-jobs"] and out["gone"] == []
    assert out["candidates_added"] == ["mcp-com-wellfound-jobs"]      # a portal we know; dice-connector already names its MCP
    data = yaml.safe_load((root / "targets" / "platforms.yaml").read_text())
    cand = [c for c in data["candidates"] if c["id"] == "mcp-com-wellfound-jobs"][0]
    assert "com.wellfound/jobs" in cand["why"] and cand["checked_on"] == "2026-10-01"
    snap = json.loads((prev / "registry-2026-10-01.json").read_text())
    assert set(snap["servers"]) == {"com.wellfound/jobs", "io.github.someone/new-jobs", "io.github.someone/old-jobs"}
    review = (root / "state" / "review" / "platform-watch-2026-10-01.md").read_text()
    assert "io.github.someone/new-jobs" in review and "untrusted" in review.lower()
    assert out["aidevjobs_newest"] == "2026-09-14" and out["aidevjobs_stale_days"] == 17


def test_a_dry_run_writes_nothing_and_a_second_run_adds_no_duplicate(tmp_path):
    root = make_root(tmp_path)
    pages = {("job", None): {"servers": [server("com.wellfound/jobs")], "metadata": {"count": 1}}}
    out = pw.run(root, routes(pages), NOW, terms=["job"], dry_run=True)
    assert out["candidates_added"] == ["mcp-com-wellfound-jobs"] and not (root / "state").exists()
    pw.run(root, routes(pages), NOW, terms=["job"])
    again = pw.run(root, routes(pages), NOW, terms=["job"])
    assert again["candidates_added"] == [] and again["new"] == []


def test_a_portal_whose_entry_already_names_its_mcp_is_not_filed_again(tmp_path):
    root = make_root(tmp_path)
    p = root / "targets" / "platforms.yaml"
    p.write_text(p.read_text().replace(
        "  - {id: himalayas, name: Himalayas, url: https://himalayas.app, alert_senders: []}",
        "  - {id: himalayas, name: Himalayas, url: https://himalayas.app, alert_senders: [], notes: 'official MCP at himalayas.app/mcp'}"))
    pages = {("job", None): {"servers": [server("app.himalayas/jobs")], "metadata": {"count": 1}}}
    out = pw.run(root, routes(pages), NOW, terms=["job"], dry_run=True)
    assert out["new"] == ["app.himalayas/jobs"] and out["candidates_added"] == []
