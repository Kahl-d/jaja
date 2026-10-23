"""news_watch.py: listing pages (a company careers page) and news pages through web_snapshot.py.

web_snapshot.main is replaced by a fake that writes a snapshot; no Firecrawl call is made and no credit spent."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import news_watch as nw

TODAY = date(2026, 9, 30)
CAREERS = "https://careers.acme.example.test/jobs/results?q=%22AI%20engineer%22"
NEWS = "https://news.example.com/venture"
CONFIG = f"""credits_per_day: 3
pages:
  - id: acme-ai
    kind: listing
    url: "{CAREERS}"
    company: acme
    job_link: "/jobs/results/\\\\d+"
    why: "Acme has no board API"
    tos: {{checked: true, quote: "robots-compliant automated reading is allowed", url: "https://careers.acme.example.test/terms"}}
  - id: venture-news
    kind: news
    url: "{NEWS}"
    why: "funding rounds"
    tos: {{checked: true, quote: "ok", url: "https://news.example.com/terms"}}
  - id: unchecked
    kind: news
    url: "https://other.example.com/"
    why: "not read yet"
    tos: {{checked: false}}
"""
LISTING_MD = """<untrusted_web>
# Search jobs
[Learn more about Data Scientist, Ads](https://careers.acme.example.test/jobs/results/1111111111111-data-scientist)
[AI Engineer, Assistant Apps](https://careers.acme.example.test/jobs/results/1234567890123-ai-engineer-assistant)
[Software Engineer, Agents](/jobs/results/9876543210987-software-engineer-agents?q=x)
[Privacy policy](https://careers.acme.example.test/privacy)
[Benefits](https://careers.acme.example.test/benefits/)
[Look alike](https://evil.example.com/jobs/results/5555555555555-fake)
</untrusted_web>
"""
NEWS_MD = """<untrusted_web>
# Venture
- Tiny AI raises $40M Series B to build agent infrastructure ([story](https://news.example.com/tiny-ai))
- A recipe blog launches a newsletter
- Mystery Labs is hiring 30 engineers after its seed round
</untrusted_web>
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    class _Root(type(tmp_path)):          # a Path that can also carry the fake's call list
        pass
    r = _Root(tmp_path / "js")
    (r / "targets").mkdir(parents=True)
    (r / "jobs").mkdir()
    (r / "targets" / "news-watch.yaml").write_text(CONFIG)
    (r / "targets" / "companies.yaml").write_text("companies:\n  - {name: Tiny AI, slug: tiny-ai, tier: strong}\n")
    calls = []

    def fake_main(argv):
        calls.append(argv)
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Snapshot\n" + (LISTING_MD if "acme.example.test" in argv[1] else NEWS_MD))
        return 0
    monkeypatch.setattr(nw, "_snapshot", fake_main)
    r.calls = calls
    return r


def test_only_pages_whose_terms_were_checked_run_and_each_goes_through_web_snapshot(root):
    out = nw.run(root, today=TODAY)
    assert [c[1] for c in root.calls] == [CAREERS, NEWS]
    assert all(c[0] == "scrape" and "--out" in c for c in root.calls)
    assert out["skipped"] == [{"id": "unchecked", "why": "terms not checked (tos.checked is not true)"}]


def test_listing_pages_become_job_candidates_with_the_record_command(root):
    out = nw.run(root, today=TODAY)
    cands = out["pages"]["acme-ai"]["candidates"]
    # "Learn more about" is stripped; roles.md matches first (the default title filter here)
    assert [c["title"] for c in cands] == ["AI Engineer, Assistant Apps", "Software Engineer, Agents", "Data Scientist, Ads"]
    assert [c["matches_roles"] for c in cands] == [True, True, False]
    assert cands[1]["url"].startswith("https://careers.acme.example.test/jobs/results/9876543210987")
    review = (root / "state" / "review" / "news-watch-2026-09-30.md").read_text()
    assert "jobs_manual.py add --platform company-ats --url https://careers.acme.example.test/jobs" in review
    assert "--company \"acme\"" in review and "Privacy policy" not in review
    assert "Benefits" not in review and "evil.example.com" not in review       # job links only, on the page's host


def test_a_known_job_is_not_offered_again(root):
    (root / "jobs" / "g.md").write_text(
        "---\nid: g\nsource_url: https://careers.acme.example.test/jobs/results/1234567890123-ai-engineer-assistant\n---\n")
    cands = nw.run(root, today=TODAY)["pages"]["acme-ai"]["candidates"]
    assert [c["title"] for c in cands] == ["Software Engineer, Agents", "Data Scientist, Ads"]


def test_news_pages_become_signals_and_flag_watch_list_companies(root):
    sigs = nw.run(root, today=TODAY)["pages"]["venture-news"]["signals"]
    assert len(sigs) == 2
    assert sigs[0]["watch_list"] == ["tiny-ai"] and "raises $40M Series B" in sigs[0]["text"]
    assert sigs[0]["link"] == "https://news.example.com/tiny-ai" and sigs[1]["watch_list"] == []


def test_the_daily_credit_cap_and_the_due_date_hold(root):
    nw.run(root, today=TODAY)
    assert len(root.calls) == 2
    nw.run(root, today=TODAY)                                         # same day: both pages already read
    assert len(root.calls) == 2
    (root / "targets" / "news-watch.yaml").write_text(CONFIG.replace("credits_per_day: 3", "credits_per_day: 1"))
    out = nw.run(root, today=date(2026, 10, 1))
    assert len(root.calls) == 3 and any(s["why"].startswith("daily credit cap") for s in out["skipped"])


def test_a_failed_snapshot_is_recorded_and_the_run_goes_on(root, monkeypatch):
    def fails_after_writing(argv):                    # a refused or failed run may still leave a file behind
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(LISTING_MD)
        return 2
    monkeypatch.setattr(nw, "_snapshot", fails_after_writing)
    out = nw.run(root, today=TODAY)
    assert out["pages"]["acme-ai"]["error"] == "web_snapshot.py exit 2 (refused)"
    rows = [json.loads(line) for line in (root / "state" / "news" / "runs.jsonl").read_text().splitlines()]
    assert {r["id"] for r in rows} == {"acme-ai", "venture-news"} and all(r["rc"] == 2 for r in rows)


def test_dry_run_calls_nothing_and_writes_nothing(root):
    out = nw.run(root, today=TODAY, dry_run=True)
    assert root.calls == [] and out["due"] == ["acme-ai", "venture-news"]
    assert not (root / "state").exists()


def test_check_refuses_a_page_without_a_reason_or_with_an_unknown_kind(root):
    (root / "targets" / "news-watch.yaml").write_text(CONFIG.replace('why: "funding rounds"', 'why: ""'))
    with pytest.raises(nw.NewsError, match="why"):
        nw.load(root)
    (root / "targets" / "news-watch.yaml").write_text(CONFIG.replace("kind: news\n    url: \"" + NEWS, "kind: gossip\n    url: \"" + NEWS))
    with pytest.raises(nw.NewsError, match="kind"):
        nw.load(root)
