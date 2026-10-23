"""adzuna.py: keyed Adzuna search -> job records; skips cleanly without keys; secrets never stored.
The fixture is SYNTHETIC (built from the documented response schema; no key existed to capture)."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, load_fixture

import adzuna  # noqa: E402

URL = "https://api.adzuna.com/v1/api/jobs/us/search/1"
ENV = {"ADZUNA_APP_ID": "test-app-id", "ADZUNA_APP_KEY": "s3cret-key-123"}


def test_skips_gracefully_without_keys():
    http = FakeHttp({})
    records, info = adzuna.collect(http, NOW, env={})
    assert records == [] and http.calls == []
    assert "ADZUNA_APP_ID" in info["skipped"]


def test_search_sends_documented_params():
    seen = {}

    def serve(params):
        seen.update(params)
        return load_fixture("adzuna_search_us.json")

    adzuna.search(FakeHttp({("GET", URL): serve}), "id1", "key1", "machine learning engineer",
                  where="San Francisco")
    assert seen == {"app_id": "id1", "app_key": "key1", "what": "machine learning engineer",
                    "where": "San Francisco", "distance": 80, "results_per_page": 50,
                    "max_days_old": 3, "sort_by": "date", "content-type": "application/json"}


def test_normalize_result_fields():
    res = load_fixture("adzuna_search_us.json")["results"][0]
    rec = adzuna.normalize(res, NOW, secrets=("test-app-id", "s3cret-key-123"))
    assert rec["id"] == "globex-ai-adz-5123456789"
    assert rec["title"] == "Senior Machine Learning Engineer"
    assert rec["company"] == "Globex AI" and rec["company_slug"] == "globex-ai"
    assert rec["source"] == "adzuna" and rec["ats_vendor"] == "unknown"
    assert rec["location"] == "San Francisco, California"
    assert rec["posted_at"] == "2026-09-23T17:12:40Z"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (185000, 240000, "USD")
    assert rec["employment_type"] == "full-time"
    assert rec["source_url"] == "https://www.adzuna.com/land/ad/5123456789?se=dGVzdA&utm_medium=api&v=A1B2C3"
    assert "machine learning engineer to build evaluation pipelines" in rec["_body"]


def test_predicted_salary_is_not_reported_as_comp():
    res = load_fixture("adzuna_search_us.json")["results"][1]
    rec = adzuna.normalize(res, NOW)
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (None, None, None)
    assert rec["remote"] == "remote"
    assert rec["company"] == "Acme Health, Inc."


def test_collect_filters_titles_and_merges_repeat_hits_across_queries():
    http = FakeHttp({("GET", URL): load_fixture("adzuna_search_us.json")})
    records, info = adzuna.collect(http, NOW, env=ENV, queries=[{"what": "machine learning engineer"},
                                                                 {"what": "applied ai engineer"}])
    assert len(http.calls) == 2
    assert sorted(r["id"] for r in records) == ["acme-health-adz-5123456790", "globex-ai-adz-5123456789"]
    assert info["results"] == 6 and info["kept"] == 2


def test_cli_without_keys_is_a_clean_no_op(root, capsys):
    rc = adzuna.main(["--root", str(root)], http=FakeHttp({}), now=NOW, env={})
    assert rc == 0 and not (root / "jobs").exists()
    assert "skipped" in capsys.readouterr().out


def test_cli_never_writes_secrets_to_disk(root):
    http = FakeHttp({("GET", URL): load_fixture("adzuna_search_us.json")})
    rc = adzuna.main(["--root", str(root), "--query", "machine learning engineer"], http=http, now=NOW, env=ENV)
    assert rc == 0
    files = list((root / "jobs").glob("*.md"))
    assert len(files) == 2
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "s3cret-key-123" not in text and "test-app-id" not in text
