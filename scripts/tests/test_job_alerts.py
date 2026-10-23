"""job_alerts.py: job-alert emails from the classified Gmail harvest -> job records.

Fixtures are SYNTHETIC (fixtures/alerts/): card layouts modeled on each platform's plain-text alert
mail; invented companies and ids. Unknown layouts, unmapped senders and mislabeled alerts must land
in the review list, never be guessed into records."""
from __future__ import annotations

import json
import shutil
from datetime import timedelta

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FIXTURES, NOW

import fetch_ats as fa  # noqa: E402
import fit_gates as fg  # noqa: E402
import job_alerts as ja  # noqa: E402
from js_common import read_jsonl, short_hash  # noqa: E402

UUID_A = "-".join(("0f1e2d3c", "4b5a", "6978", "8a9b", "0c1d2e3f4a5b"))   # a UUID job id, built so it is no literal
ALERTS = FIXTURES.parent / "alerts"
ALERTS_08 = FIXTURES.parent / "alerts_08"          # this file's own copy of the alert rows
ROWS = yaml.safe_load((ALERTS_08 / "alert_rows.yaml").read_text(encoding="utf-8"))
PLATFORMS = ja.load_platforms_file(ALERTS / "platforms.yaml")


def row(rid: str) -> dict:
    r = dict(next(x for x in ROWS if x["id"] == rid))
    r["classification"] = {"label": r.pop("label"), "confidence": 0.95, "level": "L0", "evidence": r["subject"]}
    return r


def parse(rid: str):
    return ja.parse_alert(row(rid), PLATFORMS, NOW)


def by_id(parsed) -> dict:
    return {r["id"]: r for r in parsed.records}


def harvest(root, ids=None, name="gmail-2026-09-24.classified.jsonl"):
    folder = root / "state" / "harvest"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / name).open("w", encoding="utf-8") as fh:
        for rid in ids or [x["id"] for x in ROWS]:
            fh.write(json.dumps(row(rid)) + "\n")
    return folder / name


def with_platforms(root):
    (root / "targets").mkdir(parents=True, exist_ok=True)
    shutil.copy(ALERTS / "platforms.yaml", root / "targets" / "platforms.yaml")
    return root


# ----------------------------------------------------------------------------- links
@pytest.mark.parametrize("platform,url,canonical,key,vendor", [
    ("linkedin", "https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&trk=eml",
     "https://www.linkedin.com/jobs/view/4012345678/", "4012345678", None),
    ("linkedin", "https://www.linkedin.com/jobs/view/machine-learning-engineer-at-quillstack-4012345678?refId=x",
     "https://www.linkedin.com/jobs/view/4012345678/", "4012345678", None),
    ("linkedin", "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678&origin=x",
     "https://www.linkedin.com/jobs/view/4012345678/", "4012345678", None),
    ("glassdoor", "https://www.glassdoor.com/partner/jobListing.htm?pos=101&jobListingId=1009876543210&jrtk=5",
     "https://www.glassdoor.com/job-listing/j?jl=1009876543210", "1009876543210", None),
    ("ziprecruiter", "https://www.ziprecruiter.com/c/Northwind-Robotics/Job/Machine-Learning-Engineer/-in-San-Jose,CA"
     "?jid=9d2c1a3b4e5f6a7b&lvk=abc&utm_source=email",
     "https://www.ziprecruiter.com/c/Northwind-Robotics/Job/Machine-Learning-Engineer/-in-San-Jose,CA?jid=9d2c1a3b4e5f6a7b",
     "9d2c1a3b4e5f6a7b", None),
    ("wttj", "https://us.welcometothejungle.com/en/companies/acme-ai/jobs/ml-engineer_sf_ACMEA_x1?utm_source=alert",
     "https://www.welcometothejungle.com/en/companies/acme-ai/jobs/ml-engineer_sf_ACMEA_x1",
     short_hash("acme-ai/ml-engineer_sf_ACMEA_x1", n=8), None),
    ("wttj", "https://app.otta.com/jobs/aB3dE5fG?utm_source=email", "https://app.otta.com/jobs/aB3dE5fG", "aB3dE5fG", None),
    ("hiringcafe", "https://hiring.cafe/viewjob/q8r7s6t5u4", "https://hiringcafe.com/viewjob/q8r7s6t5u4", "q8r7s6t5u4", None),
    ("hiringcafe", "https://job-boards.greenhouse.io/relaylabs/jobs/4512345008?gh_src=hiringcafe",
     "https://job-boards.greenhouse.io/relaylabs/jobs/4512345008", "4512345008", "greenhouse"),
    ("hiringcafe", f"https://jobs.lever.co/acme/{UUID_A}/apply?lever-source=hc",
     f"https://jobs.lever.co/acme/{UUID_A}", "0f1e2d3c", "lever"),
    ("hiringcafe", f"https://jobs.ashbyhq.com/acme/{UUID_A}?utm_source=hc",
     f"https://jobs.ashbyhq.com/acme/{UUID_A}", "0f1e2d3c", "ashby"),
    ("yc", "https://www.workatastartup.com/jobs/81441?utm_source=email", "https://www.workatastartup.com/jobs/81441",
     "81441", None),
    ("builtin", "https://builtin.com/job/senior-machine-learning-engineer/7123456?utm_source=alert",
     "https://builtin.com/job/senior-machine-learning-engineer/7123456", "7123456", None),
    ("indeed", "https://www.indeed.com/rc/clk?jk=0123456789abcdef&from=ja",
     "https://www.indeed.com/viewjob?jk=0123456789abcdef", "0123456789abcdef", None),
    ("wellfound", "https://wellfound.com/jobs/3123456-founding-ai-engineer?utm_campaign=x",
     "https://wellfound.com/jobs/3123456-founding-ai-engineer", "3123456", None),
])
def test_job_links_are_canonical_and_keyed(platform, url, canonical, key, vendor):
    link = ja.match_job_url(url, platform)
    assert link is not None
    assert (link.canonical, link.key, link.vendor) == (canonical, key, vendor)


@pytest.mark.parametrize("platform,url", [
    ("linkedin", "https://www.linkedin.com/comm/jobs/search?keywords=applied%20ai%20engineer&trk=eml"),
    ("linkedin", "https://www.linkedin.com/comm/jobs/alerts"),
    ("glassdoor", "https://www.glassdoor.com/Job/jobs.htm?sc.keyword=machine+learning+engineer"),
    ("ziprecruiter", "https://www.ziprecruiter.com/candidate/suggested-jobs"),
    ("wttj", "https://www.welcometothejungle.com/en/me/alerts"),
    ("linkedin", "https://job-boards.greenhouse.io/relaylabs/jobs/4512345008"),   # LinkedIn mail: LinkedIn links only
])
def test_other_links_are_not_job_anchors(platform, url):
    assert ja.match_job_url(url, platform) is None


# ----------------------------------------------------------------------------- one alert per platform
def test_linkedin_alert_cards():
    p = parse("li-01")
    assert p.status == "parsed" and p.platform == "linkedin" and p.filtered == 1 and not p.unparsed
    recs = by_id(p)
    assert sorted(recs) == ["helio-systems-li-4012345679", "quillstack-li-4012345678"]
    q = recs["quillstack-li-4012345678"]
    assert (q["title"], q["company"], q["location"], q["remote"]) == \
        ("Applied AI Engineer", "Quillstack", "San Francisco, CA (Hybrid)", "hybrid")
    assert q["source"] == "linkedin-alert" and q["platform"] == "linkedin" and q["status"] == "discovered"
    assert q["source_url"] == q["apply_url"] == "https://www.linkedin.com/jobs/view/4012345678/"
    assert q["alert_message_id"] == "li-01" and q["alert_received_at"] == "2026-09-24T06:00:00Z"
    assert q["tags"] == ["jd-missing"] and q["jd_hash"] is None and q["posted_at"] is None
    h = recs["helio-systems-li-4012345679"]
    assert (h["company"], h["location"]) == ("Helio Systems", "Palo Alto, CA")
    assert (h["comp_min"], h["comp_max"], h["comp_currency"]) == (170000, 210000, "USD")


def test_glassdoor_cards_strip_ratings():
    recs = by_id(parse("gd-01"))
    h = recs["helio-systems-gd-1009876543210"]
    assert (h["title"], h["company"], h["location"]) == ("Machine Learning Engineer", "Helio Systems", "San Francisco, CA")
    assert (h["comp_min"], h["comp_max"]) == (150000, 200000)
    assert h["source"] == "glassdoor-alert"
    v = recs["verdant-health-gd-1009876543211"]
    assert (v["title"], v["company"], v["location"], v["remote"]) == ("AI Engineer", "Verdant Health", "Remote", "remote")


def test_ziprecruiter_cards_keep_jid_and_hash_tokens():
    recs = by_id(parse("zr-01"))
    n = recs["northwind-robotics-zr-9d2c1a3b4e5f6a7b"]
    assert (n["company"], n["location"], n["employment_type"]) == ("Northwind Robotics", "San Jose, CA", "full-time")
    assert (n["comp_min"], n["comp_max"]) == (180000, 220000)
    token = "https://www.ziprecruiter.com/ekm/AAHc8f2K9xLongToken_abc123"
    m = recs[f"mosaic-robotics-zr-{short_hash(token, n=8)}"]
    assert (m["title"], m["location"], m["source_url"]) == ("LLM Engineer", "Remote (US)", token)


def test_wttj_and_otta_links():
    recs = by_id(parse("wttj-01"))
    acme = recs[f"acme-ai-wttj-{short_hash('acme-ai/machine-learning-engineer_san-francisco_ACMEA_abcd123', n=8)}"]
    assert (acme["company"], acme["location"], acme["remote"]) == ("Acme AI", "San Francisco · Hybrid", "hybrid")
    parsec = recs["parsec-labs-wttj-ab3de5fg"]
    assert (parsec["title"], parsec["source_url"], parsec["remote"]) == \
        ("AI Engineer, Agents", "https://app.otta.com/jobs/aB3dE5fG", "remote")


def test_hiringcafe_keeps_the_employer_ats_link():
    recs = by_id(parse("hc-01"))
    relay = recs["relay-labs-hc-4512345008"]
    assert (relay["title"], relay["company"], relay["ats_vendor"]) == ("Applied AI Engineer", "Relay Labs", "greenhouse")
    assert relay["source_url"] == "https://job-boards.greenhouse.io/relaylabs/jobs/4512345008"
    cobalt = recs["cobalt-robotics-hc-q8r7s6t5u4"]
    assert (cobalt["title"], cobalt["location"], cobalt["remote"]) == \
        ("Forward Deployed Engineer, AI", "San Francisco, CA · Onsite", "onsite")


def test_yc_cards_drop_the_batch_and_equity():
    recs = by_id(parse("yc-01"))
    b = recs["brightpath-ai-yc-81441"]
    assert (b["company"], b["title"], b["comp_min"], b["comp_max"]) == ("Brightpath AI", "Founding AI Engineer", 150000, 220000)
    loom = recs["loomcraft-yc-xy12abc"]
    assert (loom["title"], loom["location"]) == ("Software Engineer, Agents Infrastructure", "San Francisco, CA / Remote (US)")


def test_generic_parser_for_a_mapped_platform():
    p = parse("bi-01")
    assert p.platform == "builtin" and p.status == "parsed"
    recs = by_id(p)
    s = recs["stratoscale-bi-7123456"]
    assert (s["source"], s["platform"], s["title"], s["company"]) == \
        ("email-alert", "builtin", "Senior Machine Learning Engineer", "Stratoscale")
    assert recs["pinnacle-analytics-bi-7123457"]["location"] == "Remote"


# ----------------------------------------------------------------------------- never guessed
def test_an_alert_without_job_links_goes_to_review():
    p = parse("li-02")
    assert p.status == "review" and not p.records and "no job links" in p.reason


def test_an_ambiguous_card_is_left_for_review_and_the_rest_is_kept():
    p = parse("li-03")
    assert p.status == "partial"
    assert [r["id"] for r in p.records] == ["quillstack-li-4012345678"]
    (lines, reason), = p.unparsed
    assert "Staff Engineer" in lines and "2 title lines" in reason


def test_an_injection_line_is_dropped_and_the_record_flagged():
    p = parse("li-04")
    (rec,) = p.records
    assert (rec["title"], rec["company"]) == ("AI Engineer, Agent Platform", "Tessellate")
    assert rec["tags"] == ["jd-missing", "injection-suspect"]
    assert "sponsorship" not in str(rec.get("sponsorship") or "")


def test_url_first_layout_is_read_after_the_link():
    r = row("bi-01")
    r["body"] = ("Your weekly job matches\n\nhttps://builtin.com/job/ml-engineer/7200001\nML Engineer - Quanta Labs\n"
                 "San Francisco, CA\n\nhttps://builtin.com/job/ai-engineer/7200002\nAI Engineer - Orbit AI\nRemote\n")
    p = ja.parse_alert(r, PLATFORMS, NOW)
    assert sorted((x["company"], x["title"]) for x in p.records) == [("Orbit AI", "AI Engineer"),
                                                                     ("Quanta Labs", "ML Engineer")]


def test_posted_phrases_become_dates_from_the_received_day():
    r = row("li-01")
    r["body"] = ("Applied AI Engineer\nQuillstack\nSan Francisco, CA\n3 days ago\n"
                 "View job: https://www.linkedin.com/comm/jobs/view/4012345678/\n")
    (rec,) = ja.parse_alert(r, PLATFORMS, NOW).records
    assert rec["posted_at"] == "2026-09-21"


def test_alert_records_are_review_gated_until_the_jd_is_read():
    (rec, _) = parse("li-01").records
    out = fg.evaluate(rec, config=fg.default_config())
    assert out["gate"] == "review"
    assert any(r.startswith("jd: ") for r in out["reasons"])
    assert "JD not captured" in rec["_body"] and "<untrusted_alert>" in rec["_body"]
    assert fa.jd_text_from_body(rec["_body"]) == ""


# ----------------------------------------------------------------------------- a whole harvest
def test_collect_over_a_harvest_counts_and_routes_every_message(root):
    with_platforms(root)
    harvest(root)
    run = ja.collect(root, NOW)
    assert run.alerts == 12
    assert len(run.records) == 16 and len({r["id"] for r in run.records}) == 15
    status = {r["message_id"]: r["status"] for r in run.ledger_rows}
    assert status == {"li-01": "parsed", "gd-01": "parsed", "zr-01": "parsed", "wttj-01": "parsed",
                      "hc-01": "parsed", "yc-01": "parsed", "bi-01": "parsed", "li-02": "review",
                      "li-03": "partial", "unk-01": "unmapped", "gd-02": "unlabeled", "li-04": "parsed",
                      "li-old": "stale"}
    assert sorted(item["message_id"] for item in run.review) == ["gd-02", "li-02", "li-03", "unk-01"]
    assert run.platforms["linkedin"] == {"alerts": 5, "cards": 6, "jobs": 4, "filtered": 1, "unparsed": 1, "bare": 0}


def test_merge_resights_the_same_job_and_notes_jobs_known_from_other_sources(root):
    with_platforms(root)
    harvest(root)
    ats = fa.make_record({"name": "Relay Labs", "slug": "relay-labs"}, title="Applied AI Engineer", source="greenhouse",
                         source_url="https://job-boards.greenhouse.io/relaylabs/jobs/4512345008", apply_url=None,
                         ats_vendor="greenhouse", posted_at="2026-09-20", location="Remote - US", remote="remote",
                         comp=(None, None, None), employment_type="full-time", jd_text="Real JD.", now=NOW,
                         req_id="4512345008")
    workset = {ats["id"]: ats}
    out = ja.merge_into(workset, ja.collect(root, NOW).records, NOW)
    assert len(out["new"]) == 14 and out["updated"] == ["quillstack-li-4012345678"] and out["known"] == [ats["id"]]
    kept = workset[ats["id"]]
    assert kept["source"] == "greenhouse" and kept["location"] == "Remote - US" and kept["seen_on"] == ["hiringcafe"]


def test_a_new_tracking_link_for_the_same_card_is_a_resighting(root):
    first = row("zr-01")
    second = dict(row("zr-01"), id="zr-02", body=row("zr-01")["body"].replace("AAHc8f2K9xLongToken_abc123", "ZZotherToken_999"))
    workset: dict = {}
    ja.merge_into(workset, ja.parse_alert(first, PLATFORMS, NOW).records, NOW)
    out = ja.merge_into(workset, ja.parse_alert(second, PLATFORMS, NOW).records, NOW)
    assert out["new"] == [] and len(out["updated"]) == 2 and len(workset) == 2


def test_cli_dry_run_real_run_and_idempotent_rerun(root, capsys):
    with_platforms(root)
    harvest(root)
    assert ja.main(["--root", str(root), "--today", "2026-09-24", "--dry-run"], now=NOW) == 0
    assert not (root / "jobs").exists() and not (root / "state" / "job-alerts.jsonl").exists()
    assert "dry-run (nothing written): 15 new" in capsys.readouterr().out
    assert ja.main(["--root", str(root), "--today", "2026-09-24"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "written: 15 new, 1 re-sighted, 0 already known" in out
    assert len(list((root / "jobs").glob("*.md"))) == 15
    assert len(read_jsonl(root / "state" / "job-alerts.jsonl")) == 13
    review = (root / "state" / "review" / "job-alerts-2026-09-24.md").read_text(encoding="utf-8")
    for rid in ("li-02", "li-03", "unk-01", "gd-02"):
        assert rid in review
    assert "<untrusted_alert>" in review and "Staff Engineer" in review
    # second run: every message is already in the ledger
    assert ja.main(["--root", str(root), "--today", "2026-09-24"], now=NOW) == 0
    assert "0 alerts to parse" in capsys.readouterr().out
    assert len(read_jsonl(root / "state" / "job-alerts.jsonl")) == 13
    # --reparse reads them again and duplicates nothing
    assert ja.main(["--root", str(root), "--today", "2026-09-24", "--reparse"], now=NOW) == 0
    assert "written: 0 new, 16 re-sighted" in capsys.readouterr().out
    assert len(list((root / "jobs").glob("*.md"))) == 15


def test_a_relabeled_alert_is_parsed_on_the_next_run(root):
    with_platforms(root)
    harvest(root, ids=["gd-02"])
    run = ja.collect(root, NOW)
    ja.write_outputs(root, run, NOW)
    relabeled = row("gd-02")
    relabeled["classification"]["label"] = "job_alert"
    (root / "state" / "harvest" / "gmail-2026-09-24.classified.jsonl").write_text(json.dumps(relabeled) + "\n")
    again = ja.collect(root, NOW)
    assert [r["id"] for r in again.records] == ["verdant-health-gd-1009876543299"]


def test_missing_platform_registry_parses_nothing(root, capsys):
    harvest(root)
    assert ja.main(["--root", str(root), "--today", "2026-09-24"], now=NOW) == 1
    assert "targets/platforms.yaml" in capsys.readouterr().out
    assert not (root / "jobs").exists()


def test_no_harvest_is_a_clean_no_op(root, capsys):
    with_platforms(root)
    assert ja.main(["--root", str(root), "--today", "2026-09-24"], now=NOW) == 0
    assert "no classified harvest" in capsys.readouterr().out


def test_an_ats_resighting_of_the_alert_link_fills_the_jd_and_releases_the_hold():
    (relay, _) = parse("hc-01").records                      # source_url is the employer's Greenhouse page
    workset = {relay["id"]: relay}
    ats = fa.make_record({"name": "Relay Labs", "slug": "relay-labs"}, title="Applied AI Engineer", source="greenhouse",
                         source_url=relay["source_url"], apply_url=None, ats_vendor="greenhouse", posted_at="2026-09-20",
                         location="Remote - US", remote="remote", comp=(None, None, None), employment_type="full-time",
                         jd_text="Build agents. We sponsor visas.", now=NOW, req_id="4512345008")
    fa.merge_records(workset, [ats], NOW)
    rec = workset[relay["id"]]
    assert rec["jd_hash"] and "jd-missing" not in rec["tags"] and rec["platform"] == "hiringcafe"
    assert not any(r.startswith("jd: ") for r in fg.evaluate(rec)["reasons"])



# ----------------------------------------------------------------------------- links from the HTML part
GD_URL = "https://www.glassdoor.com/partner/jobListing.htm?pos=10{n}&jobListingId=10098765432{n}&jrtk=5-yul1"
GD_ROW = ('<tr><td><table><tr><td>{company} 4.2 &#9733;</td></tr>'
          '<tr><td><a href="' + GD_URL + '">{title}</a></td></tr>'
          '<tr><td>{place}</td></tr><tr><td>$150K - $200K (Employer est.)</td></tr>'
          '<tr><td><a href="' + GD_URL + '&amp;cta=apply">Easy Apply</a></td></tr></table></td></tr>')


def html_alert_row(mid: str = "gd-html-01") -> dict:
    """An HTML-only Glassdoor-style alert through the real harvester: the title link sits in the middle of each
    card (company above it, place below), which plain text cannot split; the HTML's own card rows can."""
    import comms_helpers as ch
    import gmail_pull as gp
    from email.message import EmailMessage
    cards = [("Helio Systems", "Machine Learning Engineer", "San Francisco, CA"), ("Verdant Health", "AI Engineer", "Remote")]
    html = ("<html><body><table><tr><td>Jobs picked for you</td></tr>"
            + "".join(GD_ROW.format(n=i, company=c, title=t, place=p) for i, (c, t, p) in enumerate(cards))
            + '<tr><td><a href="https://www.glassdoor.com/Job/jobs.htm?sc.keyword=ai">View all jobs</a></td></tr>'
            "</table></body></html>")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Glassdoor Jobs <noreply@glassdoor.com>", ch.OWNER, "2 jobs for you"
    msg.set_content(html, subtype="html")
    gm = {"id": mid, "threadId": mid, "labelIds": ["INBOX"], "internalDate": str(int(NOW.timestamp() * 1000) - 3600000),
          "payload": ch._payload(msg)}
    rec = gp.extract_record(gm, account_email=ch.OWNER, platforms=PLATFORMS)
    rec["classification"] = {"label": "job_alert", "confidence": 0.9, "level": "L0", "evidence": rec["subject"]}
    return rec


def test_an_html_only_alert_is_read_from_its_link_cards():
    r = html_alert_row()
    assert "https://" not in r["body"]                             # the stored text alone has no job links
    p = ja.parse_alert(r, PLATFORMS, NOW)
    assert p.status == "parsed" and p.read_from == "links" and not p.unparsed
    recs = by_id(p)
    assert sorted(recs) == ["helio-systems-gd-100987654320", "verdant-health-gd-100987654321"]
    h = recs["helio-systems-gd-100987654320"]
    assert (h["title"], h["company"], h["location"]) == ("Machine Learning Engineer", "Helio Systems", "San Francisco, CA")
    assert (h["comp_min"], h["comp_max"]) == (150000, 200000)
    assert h["source_url"] == "https://www.glassdoor.com/job-listing/j?jl=100987654320"
    v = recs["verdant-health-gd-100987654321"]
    assert (v["title"], v["company"], v["location"], v["remote"]) == ("AI Engineer", "Verdant Health", "Remote", "remote")


def test_links_are_preferred_over_a_body_cut_inside_a_link():
    r = row("li-01")
    cut = r["body"].index("4012345679") + 7                        # the 4,000-character cap cut the second link
    r["body"], r["body_truncated"] = r["body"][:cut], True
    r["links"] = [
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc", "text": "",
         "card": "Applied AI Engineer\nQuillstack\nSan Francisco, CA (Hybrid)\nActively recruiting"},
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc", "text": "Applied AI Engineer"},
        {"href": "https://www.linkedin.com/comm/company/quillstack", "text": "Quillstack"},
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345679/?trackingId=def", "text": "Machine Learning Engineer, Agents",
         "card": "Machine Learning Engineer, Agents\nHelio Systems · Palo Alto, CA\n$170K-$210K / year"},
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345680/", "text": "Data Engineer",
         "card": "Data Engineer\nLedgerleaf\nRemote"},
        {"href": "https://www.linkedin.com/comm/jobs/alerts", "text": "Manage your job alerts"},
    ]
    r["links_truncated"] = False
    p = ja.parse_alert(r, PLATFORMS, NOW)
    assert p.read_from == "links" and p.status == "parsed" and p.filtered == 1 and not p.truncated
    assert sorted(by_id(p)) == ["helio-systems-li-4012345679", "quillstack-li-4012345678"]
    assert by_id(p)["helio-systems-li-4012345679"]["location"] == "Palo Alto, CA"


def test_a_job_link_without_a_card_is_left_for_review_not_guessed():
    r = row("li-01")
    r["body"] = "Your job alert\nApplied AI Engineer\nQuillstack\nStaff Engineer\n"
    r["links"] = [
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345678/", "text": "Applied AI Engineer",
         "card": "Applied AI Engineer\nQuillstack\nSan Francisco, CA"},
        {"href": "https://www.linkedin.com/comm/jobs/view/4012345690/", "text": "Staff Engineer"},
    ]
    p = ja.parse_alert(r, PLATFORMS, NOW)
    assert p.status == "partial" and [x["id"] for x in p.records] == ["quillstack-li-4012345678"]
    (lines, reason), = p.unparsed
    assert "Staff Engineer" in lines and "no card" in reason


def test_links_without_cards_leave_the_body_in_charge():
    r = row("li-01")
    r["links"] = [{"href": "https://www.linkedin.com/comm/jobs/view/4012345678/", "text": "Applied AI Engineer"}]
    p = ja.parse_alert(r, PLATFORMS, NOW)
    assert p.read_from == "body" and sorted(by_id(p)) == sorted(by_id(parse("li-01")))


def test_a_mislabeled_alert_whose_job_links_are_only_in_links_is_flagged(root):
    with_platforms(root)
    r = html_alert_row("gd-html-02")
    r["classification"]["label"] = "newsletter"
    folder = root / "state" / "harvest"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gmail-2026-09-24.classified.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
    run = ja.collect(root, NOW)
    assert run.unlabeled == 1 and [x["status"] for x in run.review] == ["unlabeled"]


def test_the_ledger_says_where_each_alert_was_read_from(root):
    with_platforms(root)
    folder = root / "state" / "harvest"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gmail-2026-09-24.classified.jsonl").write_text(
        json.dumps(html_alert_row()) + "\n" + json.dumps(row("li-01")) + "\n", encoding="utf-8")
    run = ja.collect(root, NOW)
    assert {r["message_id"]: r["read_from"] for r in run.ledger_rows} == {"gd-html-01": "links", "li-01": "body"}


# ----------------------------------------------------------------------------- an unmapped sender waits for its mapping
GB_PLATFORMS = {**PLATFORMS, "gradientboard": {"id": "gradientboard", "name": "Gradientboard",
                                               "alert_senders": ["gradientboardmail.test"]}}


def gb_row(mid: str = "gb-01", received: str = "2026-09-24T11:40:00Z") -> dict:
    """An alert from a sender targets/platforms.yaml does not map yet (a new platform's digest address)."""
    return {"id": mid, "internal_date": received, "from": "Gradientboard <digest@gradientboardmail.test>",
            "from_email": "digest@gradientboardmail.test", "subject": "Gradientboard: 1 new job",
            "classification": {"label": "job_alert", "confidence": 0.95, "level": "L0", "evidence": "digest"},
            "body": ("Your job matches on Gradientboard\n\n"
                     "Applied AI Engineer - Northwind Robotics\n"
                     "San Jose, CA\n"
                     "https://job-boards.greenhouse.io/northwind/jobs/4512345999?gh_src=gradientboard\n")}


def gb_harvest(root, *rows) -> None:
    folder = root / "state" / "harvest"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gmail-2026-09-24.classified.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_an_unmapped_alert_is_parsed_on_the_first_run_after_its_sender_is_mapped(root):
    gb_harvest(root, gb_row())
    first = ja.collect(root, NOW, platforms=PLATFORMS)
    assert [(r["status"], r["platform"]) for r in first.ledger_rows] == [("unmapped", None)]
    assert first.unmapped == 1 and first.records == [] and [x["status"] for x in first.review] == ["unmapped"]
    ja.write_outputs(root, first, NOW)
    # still unmapped the next day: nothing is ledgered or listed twice; the message waits, it is not "processed"
    day2 = NOW + timedelta(days=1)
    waiting = ja.collect(root, day2, platforms=PLATFORMS)
    assert waiting.ledger_rows == [] and waiting.review == [] and waiting.records == []
    assert (waiting.alerts, waiting.skipped, waiting.unmapped, waiting.waiting) == (0, 0, 0, 1)
    ja.write_outputs(root, waiting, day2)
    # platform-scout maps the sender: the next run parses the waiting alert, no --reparse needed
    day3 = NOW + timedelta(days=2)
    mapped = ja.collect(root, day3, platforms=GB_PLATFORMS)
    assert [r["id"] for r in mapped.records] == ["northwind-robotics-grad-4512345999"]
    assert [(r["status"], r["platform"]) for r in mapped.ledger_rows] == [("parsed", "gradientboard")]
    assert mapped.records[0]["platform"] == "gradientboard" and mapped.records[0]["source"] == "email-alert"
    ja.write_outputs(root, mapped, day3)
    # and from then on it is processed like any parsed alert
    after = ja.collect(root, NOW + timedelta(days=3), platforms=GB_PLATFORMS)
    assert after.records == [] and after.ledger_rows == [] and after.skipped == 1 and after.waiting == 0


def test_a_do_not_map_sender_is_closed_as_ignored_never_left_waiting(root):
    """A re-aggregator board (many off-target mails) that the owner unsubscribes from is marked do-not-map. Its mail is ledgered `ignored` once, so it never sits in the review list as unmapped."""
    lensa = dict(gb_row("ln-01"), **{"from": "Lensa 24 <lensa24@lensa.com>", "from_email": "lensa24@lensa.com"})
    gb_harvest(root, lensa, gb_row("gb-01"))
    run = ja.collect(root, NOW, platforms=PLATFORMS, ignored={"lensa.com": "a re-aggregator; do not map"})
    assert [(r["message_id"], r["status"]) for r in run.ledger_rows] == [("ln-01", "ignored"), ("gb-01", "unmapped")]
    assert run.ignored == 1 and run.unmapped == 1 and [x["status"] for x in run.review] == ["unmapped"]
    assert run.counts()["ignored"] == 1
    ja.write_outputs(root, run, NOW)
    again = ja.collect(root, NOW + timedelta(days=1), platforms=PLATFORMS, ignored={"lensa.com": "x"})
    assert again.ledger_rows == [] and again.skipped == 1 and again.waiting == 1


@pytest.mark.real_records
def test_the_real_platforms_file_maps_jobs2web_and_ignores_lensa():
    from pathlib import Path
    real = __import__("layout").JS
    assert ja.sender_index(ja.load_platforms(real)).get("jobs2web.com") == "company-ats"
    assert "lensa.com" in ja.load_ignored(real)


def test_an_unmapped_alert_older_than_the_window_when_its_sender_is_mapped_is_ledgered_stale(root):
    gb_harvest(root, gb_row())
    ja.write_outputs(root, ja.collect(root, NOW, platforms=PLATFORMS), NOW)
    late = NOW + timedelta(days=16)                    # received 2026-09-24, mapped after the 14-day window
    run = ja.collect(root, late, platforms=GB_PLATFORMS)
    assert run.records == [] and run.stale == 1
    assert [(r["status"], r["platform"]) for r in run.ledger_rows] == [("stale", "gradientboard")]
    ja.write_outputs(root, run, late)
    assert ja.collect(root, late + timedelta(days=1), platforms=GB_PLATFORMS).skipped == 1


def test_a_still_unmapped_alert_that_ages_past_the_window_is_closed_as_stale(root):
    gb_harvest(root, gb_row())
    ja.write_outputs(root, ja.collect(root, NOW, platforms=PLATFORMS), NOW)
    late = NOW + timedelta(days=15)
    run = ja.collect(root, late, platforms=PLATFORMS)
    assert run.waiting == 0 and run.stale == 1 and run.review == []
    assert [(r["status"], r["platform"]) for r in run.ledger_rows] == [("stale", None)]
    ja.write_outputs(root, run, late)
    # mapped afterwards: it stays stale, never parsed
    again = ja.collect(root, late + timedelta(days=1), platforms=GB_PLATFORMS)
    assert again.records == [] and again.skipped == 1 and again.ledger_rows == []


def test_reparse_still_reads_every_message_again(root):
    gb_harvest(root, gb_row())
    ja.write_outputs(root, ja.collect(root, NOW, platforms=PLATFORMS), NOW)
    run = ja.collect(root, NOW + timedelta(days=1), platforms=PLATFORMS, reparse=True)
    assert [r["status"] for r in run.ledger_rows] == ["unmapped"] and run.waiting == 0


def test_cli_summary_counts_alerts_waiting_for_a_sender_mapping(root, capsys):
    with_platforms(root)
    gb_harvest(root, gb_row(), gb_row("gb-02", "2026-09-24T12:40:00Z"))
    assert ja.main(["--root", str(root), "--today", "2026-09-24"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "2 unmapped" in out
    review = (root / "state" / "review" / "job-alerts-2026-09-24.md").read_text(encoding="utf-8")
    item = next(line for line in review.splitlines() if line.startswith("- gb-01 "))
    assert "unmapped" in item and "the next run parses it" in item
    assert ja.main(["--root", str(root), "--today", "2026-09-25"], now=NOW + timedelta(days=1)) == 0
    out = capsys.readouterr().out
    assert "0 alerts to parse (0 already processed)" in out and "2 waiting for a sender mapping" in out


# ----------------------------------------------------------------------------- big-company career-site alert families
@pytest.mark.parametrize("platform,url,canonical,key", [
    ("google-careers", "https://www.google.com/about/careers/applications/jobs/results/123456789012345678-software-engineer-ai?utm_source=alert",
     "https://www.google.com/about/careers/applications/jobs/results/123456789012345678-software-engineer-ai",
     "123456789012345678"),
    ("meta-careers", "https://www.metacareers.com/jobs/1234567890123456/?utm_campaign=alert",
     "https://www.metacareers.com/jobs/1234567890123456/", "1234567890123456"),
    ("meta-careers", "https://www.metacareers.com/v2/jobs/1234567890123456/", "https://www.metacareers.com/jobs/1234567890123456/",
     "1234567890123456"),
    ("amazon-jobs", "https://www.amazon.jobs/en/jobs/2712345/applied-scientist-agents?cmpid=alert",
     "https://www.amazon.jobs/en/jobs/2712345/applied-scientist-agents", "2712345"),
    ("apple-jobs", "https://jobs.apple.com/en-us/details/100127-0836/machine-learning-engineer?team=MLAI",
     "https://jobs.apple.com/en-us/details/100127-0836/machine-learning-engineer", "100127-0836"),
    ("talent-communities", "https://eaton.eightfold.ai/careers/job/687234561234?utm_source=alert",
     "https://eaton.eightfold.ai/careers/job/687234561234", "687234561234"),
    ("talent-communities", "https://eaton.eightfold.ai/careers?pid=687234561234&domain=eaton.com",
     "https://eaton.eightfold.ai/careers/job/687234561234", "687234561234"),
    ("job-newsletters", f"https://jobs.ashbyhq.com/acme/{UUID_A}?utm_source=substack",
     f"https://jobs.ashbyhq.com/acme/{UUID_A}", "0f1e2d3c"),
])
def test_census_family_job_links(platform, url, canonical, key):
    link = ja.match_job_url(url, platform)
    assert link is not None, url
    assert (link.canonical, link.key) == (canonical, key)


def test_census_families_have_short_codes_and_the_generic_source():
    for pid, short in (("google-careers", "gc"), ("meta-careers", "meta"), ("amazon-jobs", "amz"), ("apple-jobs", "apl"),
                       ("talent-communities", "tc"), ("job-newsletters", "nl"), ("mygreenhouse", "mgh"),
                       ("workday-candidate-home", "wdc"), ("hackajob", "hj")):
        assert ja.short_for(pid) == short, pid
        assert ja.source_for(pid) == "email-alert"
    assert ja.match_job_url("https://www.google.com/about/careers/applications/jobs/results?q=ml", "google-careers") is None


# Work at a Startup's job alert mail is the YC channel (no script against WaaS). The live
# registry maps the WaaS sender to yc; ycombinator.com itself carries HN, newsletters and founder notes, never alerts.
def test_the_live_registry_maps_work_at_a_startup_alert_mail_to_yc_and_nothing_else_of_yc():
    from js_common import JS_ROOT
    platforms = ja.load_platforms(JS_ROOT)
    entry = platforms["yc"]
    assert entry["control"] == "alert-mail" and entry["adapter"] == "scripts/job_alerts.py"
    for sender in ("Work at a Startup <digest@workatastartup.com>", "no-reply@workatastartup.com"):
        assert ja.platform_for({"from": sender}, platforms) == "yc"
    for sender in ("Hacker News <hn@ycombinator.com>", "Y Combinator <newsletter@ycombinator.com>"):
        assert ja.platform_for({"from": sender}, platforms) != "yc"
    assert entry.get("alert_sender_confirmed") is False      # the exact address is confirmed on the first real alert
