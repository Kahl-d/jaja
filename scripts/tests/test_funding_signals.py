"""funding_signals.py: SEC EDGAR Form D raises as hiring signals (efts full-text search + Form D XML).
Fixtures in fixtures/edgar/ are SYNTHETIC (invented issuers), shaped like the live responses. No test touches the network: the transport is a fake and disc_harness blocks sockets."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)
from disc_harness import NOW

import funding_signals as fs  # noqa: E402
import pipeline as pl  # noqa: E402
from js_common import JS_ROOT, OS_ROOT, fm  # noqa: E402

EDGAR = Path(__file__).resolve().parent / "fixtures" / "edgar"
CONTACT = "jobs.fixture@example.test"
COMPANIES = """\
companies:
  - {name: Quillstone Systems, slug: quillstone-systems, tier: strong, ats_vendor: greenhouse, board_token: quill}
  - {name: Brightline Robotics, slug: brightline-robotics, tier: good, ats_vendor: ashby, board_token: bright}
"""
FACTS = """\
identity:
  emails:
    job_search_primary: {address: jobs.fixture@example.test, status: confirmed}
    not_for_applications:
      - {address: work.fixture@employer.test, why: "employer identity"}
"""
SOURCES = """\
sources:
  - id: greenhouse
    kind: ats-json
    enabled: true
    cadence: daily
    yield_30d: null
    last_run: null
    probation: false
    notes: "x"
  - id: funding
    kind: signal-api
    enabled: {enabled}
    cadence: daily
    yield_30d: null
    last_run: null
    probation: true
    window_days: 30
    forms: [D]
    keywords: ["AI"]
    notes: "x"
"""


def text(name: str) -> str:
    return (EDGAR / name).read_text(encoding="utf-8")


class FakeSec:
    """Serves the fixtures by URL (and the search phrase); records every call and its params."""

    def __init__(self, fts: dict[str, str] | None = None, docs: dict[str, str] | None = None, script=None):
        self.fts = {'"Quillstone Systems"': "fts_target_quillstone.json", '"AI"': "fts_keyword_ai.json"}
        self.fts.update(fts or {})
        self.docs = {"0009000001": "formd_quillstone.xml", "0009000003": "formd_meadowlark.xml",
                     "0009000005": "formd_zeta.xml"}
        self.docs.update(docs or {})
        self.script = list(script or [])      # queued (status, body) or exceptions served before the fixtures
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params or {})))
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if url == fs.FTS_URL:
            return 200, text(self.fts.get(params["q"], "fts_empty.json"))
        for cik, name in self.docs.items():
            if f"/data/{int(cik)}/" in url:
                return 200, text(name)
        raise AssertionError(f"unexpected GET {url}")


class Clock:
    def __init__(self):
        self.t = 100.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def dept(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES, encoding="utf-8")
    (root / "profile").mkdir()
    (root / "profile" / "facts.yaml").write_text(FACTS, encoding="utf-8")
    (root / "companies").mkdir()
    (root / "companies" / "quillstone-systems.md").write_text("---\ncompany: Quillstone Systems\n---\n", encoding="utf-8")
    (root / "jobs").mkdir()
    for i, status in enumerate(["fit_checked", "skipped", "closed"]):
        (root / "jobs" / f"quillstone-systems-{i}.md").write_text(
            f"---\ntype: job\nid: q{i}\ncompany: Quillstone Systems\ncompany_slug: quillstone-systems\nstatus: {status}\n---\nx\n",
            encoding="utf-8")
    (root / "sources.yaml").write_text(SOURCES.replace("{enabled}", "false"), encoding="utf-8")
    return root


def client(root, sec, clock=None, **kw):
    clock = clock or Clock()
    return fs.EdgarClient(root / fs.CACHE_DIR, sec, NOW, sleep=clock.sleep, clock=clock, **kw)


def cfg(**kw):
    c = fs.Config(keywords=["AI"])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ----------------------------------------------------------------------------- contact and fair access
def test_contact_comes_from_the_job_search_address_and_refuses_the_employer_one(dept):
    assert fs.resolve_contact(dept) == (CONTACT, "profile/facts.yaml identity.emails.job_search_primary")
    assert fs.resolve_contact(dept, "other@example.test")[0] == "other@example.test"
    addr, why = fs.resolve_contact(dept, "work.fixture@employer.test")
    assert addr is None and "not_for_applications" in why
    assert fs.resolve_contact(dept, "not-an-address")[0] is None
    assert fs.user_agent(CONTACT) == "job-agency funding_signals.py jobs.fixture@example.test"


def test_no_contact_means_no_request(root, capsys):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES, encoding="utf-8")
    sec = FakeSec()
    assert fs.main(["--root", str(root), "--dry-run"], transport=sec, now=NOW) == 2
    assert sec.calls == [] and "no usable contact" in capsys.readouterr().err


def test_requests_stay_under_ten_a_second_across_hosts(dept):
    assert fs.MIN_INTERVAL >= 0.1
    clock = Clock()
    stamps: list[float] = []
    sec = FakeSec()

    def timed(url, params):
        stamps.append(clock.t)
        return sec(url, params)

    fs.collect(dept, NOW, client(dept, timed, clock), cfg())
    assert len(stamps) >= 5
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= fs.MIN_INTERVAL - 1e-9            # search host and archive host share one throttle


# ----------------------------------------------------------------------------- queries and matching
def test_search_asks_for_the_exact_phrase_form_and_window(dept):
    sec = FakeSec()
    run = fs.collect(dept, NOW, client(dept, sec), cfg(), details=False)
    searches = [p for u, p in sec.calls if u == fs.FTS_URL]
    assert {"q": '"Quillstone Systems"', "forms": "D", "dateRange": "custom", "startdt": "2026-08-26",
            "enddt": "2026-09-24"} in searches
    assert [p["q"] for p in searches] == ['"Quillstone Systems"', '"Brightline Robotics"', '"AI"']
    assert (run.start.isoformat(), run.end.isoformat()) == ("2026-08-26", "2026-09-24")


def test_target_hits_need_the_filer_to_be_the_company(dept):
    run = fs.collect(dept, NOW, client(dept, FakeSec()), cfg(keywords=[]), details=False)
    assert [s["company"] for s in run.signals] == ["Quillstone Systems AI, Inc."]   # trailing "AI" still matches
    c = run.counts
    assert c["mentions_unmatched"] == 1                 # the SPV that names the company is not its raise
    assert c["other_forms_dropped"] == 1                # the D/A amendment is not a new raise


def test_keyword_hits_drop_funds_and_merge_with_target_hits(dept):
    run = fs.collect(dept, NOW, client(dept, FakeSec()), cfg())
    by = {s["company"]: s for s in run.signals}
    assert set(by) == {"Quillstone Systems AI, Inc.", "Meadowlark AI, Inc."}
    q = by["Quillstone Systems AI, Inc."]
    assert q["matched_by"] == ["target:quillstone-systems", "keyword:AI"]     # one accession, listed once
    assert run.counts["funds_dropped"] == 2            # 3(c) item on the hit + Pooled Investment Fund in the XML
    assert run.signals[0]["company"] == "Quillstone Systems AI, Inc."          # targets first


def test_signal_fields_and_department_flags(dept):
    run = fs.collect(dept, NOW, client(dept, FakeSec()), cfg())
    q, m = run.signals
    assert q["form"] == "D" and q["file_date"] == "2026-09-20" and q["cik"] == "9000001"
    assert (q["amount"], q["amount_status"], q["amount_sold"]) == (40000000, "stated", 25000000)
    assert q["industry_group"] == "Other Technology"
    assert q["link"] == "https://www.sec.gov/Archives/edgar/data/9000001/000900000126000001/0009000001-26-000001-index.htm"
    assert (q["target"], q["target_slug"], q["dossier"], q["open_jobs"]) == (True, "quillstone-systems", True, 2)
    assert (m["amount"], m["amount_status"]) == (None, "indefinite")
    assert (m["target"], m["target_slug"], m["dossier"], m["open_jobs"]) == (False, None, False, 0)


def test_no_details_means_amount_not_fetched_never_zero(dept):
    sec = FakeSec()
    run = fs.collect(dept, NOW, client(dept, sec), cfg(), details=False)
    assert all(s["amount"] is None and s["amount_status"] == "not-fetched" for s in run.signals)
    assert all(u == fs.FTS_URL for u, _ in sec.calls)


def test_pagination_asks_for_the_next_hundred(dept):
    page1 = json.loads(text("fts_keyword_ai.json"))
    page1["hits"]["total"]["value"] = 150
    seen = []

    def sec(url, params):
        seen.append(params.get("from"))
        if url != fs.FTS_URL:
            return 200, text("formd_meadowlark.xml")
        if params["q"] != '"AI"':
            return 200, text("fts_empty.json")
        return 200, json.dumps(page1) if not params.get("from") else text("fts_empty.json")

    fs.collect(dept, NOW, client(dept, sec), cfg(max_pages=3), details=False)
    assert seen.count(100) == 1


# ----------------------------------------------------------------------------- failures and cache
def test_a_5xx_is_retried_and_a_429_stops_every_further_request(dept):
    sec = FakeSec(script=[(500, "oops")])
    run = fs.collect(dept, NOW, client(dept, sec), cfg())
    assert run.errors == [] and len(run.signals) == 2
    assert run.requests == 3 + 1 + 3                     # 3 searches, 1 retry of the 500, 3 Form D documents
    sec = FakeSec(script=[(429, "slow down")])
    run = fs.collect(dept, NOW, client(dept, sec, refresh=True), cfg())
    assert len(sec.calls) == 1 and run.signals == []
    assert "HTTP 429" in run.errors[0]


def test_no_network_is_a_clean_skip(dept):
    sec = FakeSec(script=[ConnectionError("Failed to establish a new connection")])
    run = fs.collect(dept, NOW, client(dept, sec), cfg())
    assert run.skipped.startswith("no network") and run.errors == [] and len(sec.calls) == 1


def test_cache_serves_the_second_run_and_offline_mode(dept):
    first = FakeSec()
    fs.collect(dept, NOW, client(dept, first), cfg())
    assert first.calls and (dept / fs.CACHE_DIR).is_dir()
    again = FakeSec()
    run = fs.collect(dept, NOW, client(dept, again), cfg())
    assert again.calls == [] and run.requests == 0 and len(run.signals) == 2
    offline = fs.collect(dept, NOW, client(dept, None, offline=True), cfg())
    assert len(offline.signals) == 2 and offline.skipped is None
    later = FakeSec()                                    # searches expire after max_age_hours; documents never do
    run = fs.collect(dept, NOW + timedelta(hours=13), fs.EdgarClient(dept / fs.CACHE_DIR, later, NOW + timedelta(hours=13),
                                                                  sleep=lambda s: None), cfg())
    assert later.calls and all(u == fs.FTS_URL for u, _ in later.calls)


# ----------------------------------------------------------------------------- outputs, check, CLI
def test_real_run_writes_the_generated_markdown_and_json_twin(dept, capsys):
    assert fs.main(["--root", str(dept), "--today", "2026-09-24"], transport=FakeSec(), now=NOW) == 0
    md = dept / "state" / "signals" / "funding-2026-09-24.md"
    js = md.with_suffix(".json")
    body = md.read_text(encoding="utf-8")
    assert body.startswith(f"<!-- GENERATED by {fs.SELF}")
    assert "| Quillstone Systems AI, Inc. | D | 2026-09-20 | $40,000,000 |" in body
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["counts"]["signals"] == 2 and data["window"] == {"start": "2026-08-26", "end": "2026-09-24"}
    assert data["contact_source"] == "profile/facts.yaml identity.emails.job_search_primary"
    assert CONTACT not in body and CONTACT not in js.read_text(encoding="utf-8")
    assert fs.check(dept, os_root=dept) == []            # config, contact and the twin agree (no .gitignore here)


def test_dry_run_writes_no_signal_file(dept):
    assert fs.main(["--root", str(dept), "--dry-run", "--today", "2026-09-24"], transport=FakeSec(), now=NOW) == 0
    assert not (dept / "state" / "signals").exists()


def test_check_flags_a_bad_config_a_missing_ignore_and_a_broken_twin(dept, tmp_path):
    os_root = tmp_path / "osr"
    os_root.mkdir()
    (os_root / ".gitignore").write_text("vault/\n", encoding="utf-8")
    fs.main(["--root", str(dept), "--today", "2026-09-24"], transport=FakeSec(), now=NOW)
    assert fs.check(dept, os_root) == [f".gitignore lacks {fs.GITIGNORE_LINE} (the EDGAR cache must stay out of git)"]
    (os_root / ".gitignore").write_text(fs.GITIGNORE_LINE + "\n", encoding="utf-8")
    assert fs.check(dept, os_root) == []
    js = dept / "state" / "signals" / "funding-2026-09-24.json"
    data = json.loads(js.read_text(encoding="utf-8"))
    data["signals"].pop()
    js.write_text(json.dumps(data), encoding="utf-8")
    (dept / "sources.yaml").write_text(SOURCES.replace("{enabled}", "false").replace("window_days: 30", "window_days: 0"),
                                       encoding="utf-8")
    problems = fs.check(dept, os_root)
    assert any("window_days" in p for p in problems)
    assert any("counts.signals disagrees" in p for p in problems)
    assert fs.main(["--root", str(dept), "--check"]) == 1


# ----------------------------------------------------------------------------- pipeline stage
def test_pipeline_stage_is_off_unless_enabled(dept, os_root):
    sec = FakeSec()
    res = pl.run_pipeline(dept, os_root, NOW, http=FakeHttp({}), stages=["funding"], edgar=sec, env={})
    assert res.receipt["stages"]["funding"] == {"skipped": "disabled in sources.yaml (the owner enables it)"}
    assert sec.calls == []
    (dept / "sources.yaml").unlink()
    res = pl.run_pipeline(dept, os_root, NOW, http=FakeHttp({}), stages=["funding"], edgar=sec, env={})
    assert "skipped" in res.receipt["stages"]["funding"] and sec.calls == []      # a missing entry is off too


def test_pipeline_stage_when_enabled_writes_signals_and_stamps_last_run(dept, os_root):
    (dept / "sources.yaml").write_text(SOURCES.replace("{enabled}", "true"), encoding="utf-8")
    dry = pl.run_pipeline(dept, os_root, NOW, http=FakeHttp({}), stages=["funding"], edgar=FakeSec(), env={},
                          dry_run=True)
    assert dry.receipt["stages"]["funding"]["signals"] == 2 and not (dept / "state" / "signals").exists()
    res = pl.run_pipeline(dept, os_root, NOW, http=FakeHttp({}), stages=["funding"], edgar=FakeSec(), env={})
    st = res.receipt["stages"]["funding"]
    assert st["signals"] == 2 and st["targets"] == 1 and st["written"] == "state/signals/funding-2026-09-24.md"
    reg = {s["id"]: s for s in fm.parse_yaml((dept / "sources.yaml").read_text(encoding="utf-8"))["sources"]}
    assert reg["funding"]["last_run"] == "2026-09-24T13:00:00Z" and reg["funding"]["yield_30d"] is None
    assert reg["greenhouse"]["last_run"] is None


# ----------------------------------------------------------------------------- committed config
def test_committed_funding_source_is_a_valid_signal_api_entry():
    """The funding entry exists and validates; whether it is on is the owner's switch (a plain true or false)."""
    entry = fs.load_entry(JS_ROOT)
    assert entry is not None and entry["kind"] == "signal-api"
    assert fs.config_from(entry)[1] == []
    assert entry["enabled"] in (True, False)


@pytest.mark.workspace
def test_committed_gitignore_keeps_the_edgar_cache_out_of_git():
    lines = (OS_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert any(ln.endswith("state/cache/") for ln in lines)
