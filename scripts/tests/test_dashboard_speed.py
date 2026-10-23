"""Speed: Home must build in under 5 s cold. (a) jobs/ is parsed once and kept on disk in
state/cache/dashboard/, keyed by each file's mtime and size, so a restart re-parses only what changed; (b) one snapshot
build reads jobs/ once and computes tracker.metrics once (fresh10, interviews and the funnel share them)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import jobs_cache  # noqa: E402
from test_dashboard_api import NOW, tree  # noqa: E402,F401


def job(js: Path, jid: str, title: str) -> Path:
    p = js / "jobs" / f"{jid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nid: {jid}\ntitle: {title}\ncompany: X\n---\n## Job description (snapshot)\n\n<untrusted_jd>\nBuild agents.\n</untrusted_jd>\n")
    return p


def test_the_cache_reparses_only_changed_files(tmp_path, monkeypatch):
    js = tmp_path / "js"
    a, b = job(js, "a-1", "AI Engineer"), job(js, "b-1", "ML Engineer")
    first = jobs_cache.load(js)
    assert set(first) == {"a-1", "b-1"} and first["a-1"]["_jd_text"].strip() == "Build agents."
    assert (js / "state" / "cache" / "dashboard" / "jobs.pickle").is_file()
    parsed = []
    real = jobs_cache.parse
    monkeypatch.setattr(jobs_cache, "parse", lambda p: parsed.append(p.name) or real(p))
    assert jobs_cache.load(js)["b-1"]["title"] == "ML Engineer" and parsed == []          # nothing changed: no parse
    b.write_text(b.read_text().replace("ML Engineer", "Staff ML Engineer"))
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 10**9))
    a.unlink()
    out = jobs_cache.load(js)
    assert parsed == ["b-1.md"] and out["b-1"]["title"] == "Staff ML Engineer" and "a-1" not in out


def test_a_corrupt_cache_is_rebuilt(tmp_path):
    js = tmp_path / "js"
    job(js, "a-1", "AI Engineer")
    jobs_cache.load(js)
    (js / "state" / "cache" / "dashboard" / "jobs.pickle").write_bytes(b"not a pickle")
    assert set(jobs_cache.load(js)) == {"a-1"}


def test_one_build_reads_jobs_once_and_metrics_once(tree, monkeypatch):
    import dash_api as da
    import fetch_ats as fa
    import tracker as tr
    os_root, js = tree
    jobs = da.load_jobs(js)
    calls = {"metrics": 0}
    real_metrics = tr.metrics

    def counting(root, now):
        calls["metrics"] += 1
        return real_metrics(root, now)
    monkeypatch.setattr(tr, "metrics", counting)

    def no_reread(folder):
        if Path(folder).name == "jobs":
            raise AssertionError("jobs/ re-read during the build")
        return real_iter(folder)
    real_iter = fa.iter_records
    monkeypatch.setattr(fa, "iter_records", no_reread)
    snap = da.build(js, os_root, NOW, jobs=jobs)
    assert snap["crm"]["jobs"] is not None and calls["metrics"] == 1


def test_segment_evidence_uses_the_loaded_jobs(tmp_path, monkeypatch):
    import segment_companies as sc
    js = tmp_path / "js"
    job(js, "newco-1", "Founding AI Engineer")
    jobs = jobs_cache.load(js)
    real = sc.iter_records
    monkeypatch.setattr(sc, "iter_records", lambda folder: (_ for _ in ()).throw(AssertionError("re-read"))
                        if Path(folder).name == "jobs" else real(folder))
    ev = sc.Evidence(js, jobs=jobs)
    assert ev.decide("X", "x")[0] == "startup"          # company X's founding seat, from the loaded records


def test_warm_paths_parse_prospects_once_until_a_file_changes(tmp_path, monkeypatch):
    import strategy_review as sr
    import js_common
    from company_index import CompanyIndex
    js = tmp_path / "js"
    (js / "prospects").mkdir(parents=True)
    p = js / "prospects" / "ana.md"
    p.write_text("---\nid: ana\nname: Ana\ncompany_slug: acme\nwarmth: 2\n---\n# Ana\n")
    reads = []
    real = js_common.iter_records
    monkeypatch.setattr(js_common, "iter_records", lambda folder: reads.append(Path(folder).name) or real(folder))
    ix = CompanyIndex.load(js)
    assert sr.warm_paths(js, ix, "acme") == ["ana"] and sr.warm_paths(js, ix, "acme") == ["ana"]
    assert reads.count("prospects") == 1
    p.write_text(p.read_text().replace("warmth: 2", "warmth: 0"))
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10**9))
    assert sr.warm_paths(js, ix, "acme") == [] and reads.count("prospects") == 2


def test_the_calendar_is_read_once_in_the_background_and_reaches_the_band(tree, monkeypatch):
    import threading
    import crm
    import dash_api as da
    os_root, js = tree
    seen = []

    def window(root, now):
        seen.append(threading.current_thread() is not threading.main_thread())
        return [{"start": "2026-10-05T15:00:00-07:00", "end": "2026-10-05T15:30:00-07:00", "title": "Acme AI <> Alex"}]
    monkeypatch.setattr(crm, "calendar_window", window)
    snap = da.build(js, os_root, NOW, jobs=da.load_jobs(js))
    assert seen == [True] and snap["crm"]["band48"]["calendar_read"] is True
    cal = [r for r in snap["crm"]["band48"]["rounds"] if r["source"] == "calendar"]
    assert len(cal) == 1 and cal[0]["company"] and cal[0]["title"] is None      # the event reached the band; its words
                                                                                # never do (SETUP-FINAL review R3)


def _slow_build(seconds, marker):
    import time as _t

    def build(js, os_root, now, jobs=None, load=True):
        _t.sleep(seconds)
        return {"generated_at": "2026-10-02T05:00:00Z", "marker": marker, "_jobs": [{"id": "x"}]}
    return build


def test_a_cold_start_serves_the_saved_snapshot_at_once_and_refreshes_behind(tree, monkeypatch):
    import json
    import time
    import serve
    os_root, js = tree
    saved = js / "state" / "cache" / "dashboard" / "snapshot.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"generated_at": "2026-10-01T20:00:00Z", "marker": "old"}))
    monkeypatch.setattr(serve.dash_api, "build", _slow_build(1.0, "new"))
    monkeypatch.setattr(serve.dash_api, "load_jobs", lambda js: {})
    cache = serve.Cache(js, os_root)
    t = time.perf_counter()
    first = cache.quick()
    assert time.perf_counter() - t < 0.5 and first["marker"] == "old" and first["stale"] is True
    assert first["as_of"] == "2026-10-01T20:00:00Z"
    for _ in range(40):
        if cache.quick().get("marker") == "new":
            break
        time.sleep(0.1)
    fresh = cache.quick()
    assert fresh["marker"] == "new" and not fresh.get("stale")
    on_disk = json.loads(saved.read_text())
    assert on_disk["marker"] == "new" and "_jobs" not in on_disk


def test_without_a_saved_snapshot_the_first_request_builds(tree, monkeypatch):
    import serve
    os_root, js = tree
    monkeypatch.setattr(serve.dash_api, "build", _slow_build(0.0, "built"))
    monkeypatch.setattr(serve.dash_api, "load_jobs", lambda js: {})
    snap = serve.Cache(js, os_root).quick()
    assert snap["marker"] == "built" and not snap.get("stale")
