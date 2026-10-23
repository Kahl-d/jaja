"""himalayas.py: Himalayas public jobs API (keyless, terms: credit Himalayas and link back) -> job
records; skips cleanly without a network; secrets none. The fixtures are SYNTHETIC, shaped like the
live /jobs/api/search response observed 2026-09-25 (pubDate in seconds or ms, country-name
locationRestrictions)."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, load_fixture

import fetch_ats as fa  # noqa: E402
import himalayas  # noqa: E402

URL = "https://himalayas.app/jobs/api/search"


def pages(params):
    return load_fixture("himalayas_search_us.json" if params.get("page", 1) == 1 else "himalayas_search_us_p2.json")


def test_search_sends_documented_params():
    seen = {}

    def serve(params):
        seen.update(params)
        return load_fixture("himalayas_search_us.json")

    himalayas.search(FakeHttp({("GET", URL): serve}), "applied AI engineer")
    assert seen == {"q": "applied AI engineer", "country": "US", "employment_type": "Full Time",
                    "sort": "recent", "page": 1}


def test_normalize_fields_credit_and_link_back():
    job = load_fixture("himalayas_search_us.json")["jobs"][1]
    rec = himalayas.normalize(job, NOW)
    assert rec["id"] == "brightline-robotics-him-5100000001"
    assert rec["title"] == "Applied AI Engineer"
    assert rec["company"] == "Brightline Robotics" and rec["company_slug"] == "brightline-robotics"
    assert rec["source"] == "himalayas" and rec["platform"] == "himalayas" and rec["ats_vendor"] == "unknown"
    assert rec["source_url"] == rec["apply_url"] == \
        "https://himalayas.app/companies/brightline-robotics/jobs/applied-ai-engineer-5100000001"
    assert rec["remote"] == "remote" and rec["location"] == "Remote (United States)"
    assert rec["posted_at"] == "2026-09-23T18:00:00Z"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (150000, 190000, "USD")
    assert rec["employment_type"] == "full-time" and rec["status"] == "discovered"
    assert "We sponsor visas for this role." in rec["_jd_text"]
    assert "Himalayas (https://himalayas.app)" in rec["_body"]          # credit + link back (API terms)


def test_pubdate_in_milliseconds_and_several_countries_put_the_us_first():
    job = load_fixture("himalayas_search_us.json")["jobs"][0]
    rec = himalayas.normalize(job, NOW)
    assert rec["posted_at"] == "2026-09-24T02:00:00Z"
    assert rec["location"] == "Remote (United States, Canada)"


def test_location_text_orders_and_caps_countries():
    assert himalayas.location_text([]) == "Remote (worldwide)"
    assert himalayas.location_text(["Canada", "United States"]) == "Remote (United States, Canada)"
    many = ["Albania", "Andorra", "Australia", "Austria", "United States"]
    assert himalayas.location_text(many) == "Remote (United States, Albania, Andorra, +2 more)"
    assert himalayas.location_text([{"name": "United States", "alpha2": "US"}]) == "Remote (United States)"


def test_hourly_pay_is_not_reported_as_annual_comp():
    job = load_fixture("himalayas_search_us.json")["jobs"][3]
    rec = himalayas.normalize(job, NOW)
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (None, None, None)
    assert rec["location"] == "Remote (worldwide)"


def test_collect_filters_titles_stale_and_expired_and_merges_repeat_hits():
    http = FakeHttp({("GET", URL): pages})
    records, info = himalayas.collect(http, NOW, queries=["AI engineer", "machine learning engineer"])
    assert sorted(r["id"] for r in records) == ["brightline-robotics-him-5100000001", "globex-him-5100000004",
                                                "northwind-labs-him-5100000002"]
    assert info["requests"] == 4                      # two queries x (page 1, page 2); page 2 is all stale: stop
    assert info["results"] == 16 and info["kept"] == 3
    assert info["expired"] == 2 and info["stale"] == 4 and info["filtered"] == 4


def test_paging_stops_at_total_count():
    one_page = dict(load_fixture("himalayas_search_us.json"), totalCount=6)
    http = FakeHttp({("GET", URL): lambda params: one_page})
    himalayas.collect(http, NOW, queries=["AI engineer"], max_pages=5)
    assert len(http.calls) == 1


def test_no_network_skips_cleanly():
    http = FakeHttp({("GET", URL): ConnectionError("Failed to establish a new connection")})
    records, info = himalayas.collect(http, NOW, queries=["AI engineer"])
    assert records == [] and info["skipped"].startswith("no network")
    wrapped = FakeHttp({("GET", URL): fa.FetchError(
        "GET https://himalayas.app/jobs/api/search: HTTPSConnectionPool(host='himalayas.app', port=443): "
        "Max retries exceeded (Caused by NameResolutionError: Failed to resolve 'himalayas.app')")})
    assert himalayas.collect(wrapped, NOW, queries=["AI engineer"])[1]["skipped"].startswith("no network")


def test_an_http_error_is_not_swallowed():
    http = FakeHttp({("GET", URL): fa.FetchError("GET https://himalayas.app/jobs/api/search: HTTP 500")})
    try:
        himalayas.collect(http, NOW, queries=["AI engineer"])
    except fa.FetchError as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("an HTTP 500 must surface as an error, not a silent skip")


def test_cli_dry_run_writes_nothing_and_real_run_writes_records(root, capsys):
    rc = himalayas.main(["--root", str(root), "--query", "AI engineer", "--dry-run", "--today", "2026-09-24"],
                        http=FakeHttp({("GET", URL): pages}), now=NOW)
    assert rc == 0 and not (root / "jobs").exists()
    assert "dry-run (nothing written): 3 new" in capsys.readouterr().out
    rc = himalayas.main(["--root", str(root), "--query", "AI engineer"], http=FakeHttp({("GET", URL): pages}), now=NOW)
    assert rc == 0
    jobs = fa.load_jobs(root)
    assert sorted(jobs) == ["brightline-robotics-him-5100000001", "globex-him-5100000004",
                            "northwind-labs-him-5100000002"]
    assert jobs["globex-him-5100000004"]["platform"] == "himalayas"


def test_cli_offline_is_a_clean_no_op(root, capsys):
    rc = himalayas.main(["--root", str(root)], http=FakeHttp({("GET", URL): ConnectionError("offline")}), now=NOW)
    assert rc == 0 and not (root / "jobs").exists()
    assert "himalayas skipped: no network" in capsys.readouterr().out
