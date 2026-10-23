"""job_alerts.py: Indeed alert mail that links through cts.indeed.com/v3 redirects.

Real Indeed alert mail links
every job through https://cts.indeed.com/v3/<payload>/<signature>, where the payload is a gzip, URL-safe base64 JSON
object {"u": <target URL>, "m": {click metadata}}. The parser read only direct indeed.com viewjob, rc/clk and pagead
links, so the real alert mail gave 0 cards. The payload is read where it sits in the mail (decoded, never fetched): a
target carrying a job key (jk) is that job; a sponsored target with no job key is a job whose record is keyed on what
the mail itself says (title, company, place from the card text, or from the subject of a one-job mail).

Fixtures are SYNTHETIC: the real structure (the one-job "Title (City, ST) @ Company" mail with its plain-text body,
its button and title links and its footer links; a digest with HTML cards) with invented companies, places, job keys,
ad tokens and signatures. The harness blocks the network."""
from __future__ import annotations

import base64
import gzip
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import gmail_pull  # noqa: E402
import job_alerts as ja  # noqa: E402

PLATFORMS = {"indeed": {"id": "indeed", "name": "Indeed", "alert_senders": ["indeed.com"]}}
ANY_TITLE = fa.TitleFilter([re.compile(".")], [])
JK_A = "0a1b2c3d4e5f6a7b"


def cts(target: str, click: str = "viewjob", tk: str = "1synthtk00000001", sig: str = "SYNTHsig0001") -> str:
    """A cts.indeed.com/v3 redirect shaped like the real ones (gzip header with mtime 0, URL-safe base64, no padding)."""
    payload = json.dumps({"u": target, "m": {"clickThrough": "true", "clickType": click, "parentTk": "",
                                             "postClickType": "UNKNOWN", "tk": tk}}, separators=(",", ":"))
    blob = base64.urlsafe_b64encode(gzip.compress(payload.encode("utf-8"), mtime=0)).decode("ascii").rstrip("=")
    return f"https://cts.indeed.com/v3/{blob}/{sig}"


def sponsored(ad: str = "SYNTHadTokenAAAA", jrtk: str = "5-syn1-1-1synthjrtk0001-00aa11bb22cc33dd") -> str:
    return f"https://www.indeed.com/pagead/clk?from=jobi2a&jrtk={jrtk}&mo=r&ad={ad}&rm=2&jsa=1234&camk=SYNTHcamk%3D%3D"


FOOTER_TARGETS = [
    ("https://match.indeed.com/invitations/not-interested?tk=1synthtk00000001&info=SYNTHinfo", "bad-match"),
    ("https://match.indeed.com/invitations/match-rating/1?tk=1synthtk00000001&info=SYNTHinfo&from=i2a-email", "rating"),
    ("https://profile.indeed.com/?from=i2a-email", "edit-profile"),
    ("https://subscriptions.indeed.com/optout?ctt=SYNTHctt&token=SYNTHtoken", "unsubscribe"),
    ("https://www.indeed.com?from=arr_marketing_1_Synth&utm_campaign=arr_marketing_1_Synth", "home"),
    ("https://indeed.com/career-services/resume-help/instant-report/?collectorID=arr_email_1", "Get resume feedback"),
    ("https://www.indeed.com/legal?from=arr_marketing_1_Synth", "terms"),
]


# ----------------------------------------------------------------------------- link rule
def test_a_redirect_to_a_job_key_is_that_job():
    for target in (f"https://www.indeed.com/rc/clk/dl?jk={JK_A}&from=ja&tk=SYNTHtk",
                   f"https://www.indeed.com/viewjob?jk={JK_A}&from=jobi2a&tk=SYNTHtk",
                   f"https://www.indeed.com/pagead/clk?jk={JK_A}&ad=SYNTHad"):
        link = ja.match_job_url(cts(target), "indeed")
        assert link is not None, target
        assert (link.canonical, link.key) == (f"https://www.indeed.com/viewjob?jk={JK_A}", JK_A)


def test_a_sponsored_redirect_without_a_job_key_is_a_job_whose_key_is_hidden():
    link = ja.match_job_url(cts(sponsored()), "indeed")
    assert link is not None and link.key == "" and link.kind == "indeed-redirect"
    assert "SYNTHad" not in link.canonical and "jrtk" not in link.canonical      # no tracking token survives
    again = ja.match_job_url(cts(sponsored(), click="viewjob", sig="SYNTHsig0002"), "indeed")
    assert again.canonical == link.canonical                  # the button and the title link of one card are one job
    other = ja.match_job_url(cts(sponsored(ad="SYNTHadTokenBBBB")), "indeed")
    assert other.canonical != link.canonical                  # another ad is another job


@pytest.mark.parametrize("target,click", FOOTER_TARGETS)
def test_redirects_to_anything_but_a_job_are_not_job_links(target, click):
    assert ja.match_job_url(cts(target, click=click), "indeed") is None


@pytest.mark.parametrize("url", [
    "https://cts.indeed.com/v3/not-base64-at-all!!/SYNTHsig",
    "https://cts.indeed.com/v3/" + base64.urlsafe_b64encode(b"plain text, not gzip").decode().rstrip("=") + "/x",
    "https://cts.indeed.com/v3/" + base64.urlsafe_b64encode(gzip.compress(b"[1, 2, 3]", mtime=0)).decode() + "/x",
    "https://cts.indeed.com/v3/" + base64.urlsafe_b64encode(gzip.compress(b"{" + b" " * 300_000 + b"}", mtime=0)
                                                            ).decode().rstrip("=") + "/x",
    "https://cts.indeed.com/v3/",
])
def test_a_broken_or_oversized_payload_is_not_a_job_link_and_never_raises(url):
    assert ja.match_job_url(url, "indeed") is None


# ----------------------------------------------------------------------------- the one-job match mail
def one_job_row(rid: str = "syn-in-1", ad: str = "SYNTHadTokenAAAA", tk: str = "1synthtk00000001",
                subject: str = "Data Platform Engineer (Fairview, OR) @ Brightwell Analytics") -> dict:
    job = cts(sponsored(ad=ad), tk=tk)
    unsub = cts(FOOTER_TARGETS[3][0], click="unsubscribe", tk=tk)
    body = ("We thought this job for a Data Platform Engineer at Brightwell Analytics in Fairview, OR paying "
            f"$150,000 - $190,000 a year would be a good fit. Check out the job at {job}\nUnsubscribe: {unsub}")
    links = [{"href": job, "text": "I'm interested"},
             {"href": cts(FOOTER_TARGETS[0][0], click="bad-match", tk=tk), "text": "This is a bad match"},
             {"href": job, "text": "Data Platform Engineer (Fairview, OR)"},
             {"href": cts(FOOTER_TARGETS[1][0], click="rating", tk=tk), "text": "No"},
             {"href": cts(FOOTER_TARGETS[2][0], click="edit-profile", tk=tk), "text": "Edit profile"},
             {"href": "https://social.example.test/ExampleBoard", "text": ""},
             {"href": unsub, "text": "Unsubscribe"}]
    return {"id": rid, "from": "Indeed <donotreply@match.indeed.com>", "from_email": "donotreply@match.indeed.com",
            "subject": subject, "body": body, "body_truncated": False, "links": links, "links_truncated": False,
            "internal_date": "2026-09-23T18:52:24Z",
            "classification": {"label": "job_alert", "confidence": 0.9, "level": "L0", "evidence": subject}}


def test_the_one_job_match_mail_is_one_card_read_from_its_subject():
    p = ja.parse_alert(one_job_row(), PLATFORMS, NOW, ANY_TITLE)
    assert p.platform == "indeed" and p.status == "parsed" and p.cards == 1 and not p.unparsed
    (rec,) = p.records
    assert (rec["title"], rec["company"], rec["location"]) == ("Data Platform Engineer", "Brightwell Analytics",
                                                               "Fairview, OR")
    parts = urlsplit(rec["source_url"])
    assert (parts.netloc, parts.path) == ("www.indeed.com", "/jobs")
    assert parse_qs(parts.query) == {"q": ["Data Platform Engineer Brightwell Analytics"], "l": ["Fairview, OR"]}
    assert "SYNTH" not in rec["source_url"] and rec["apply_url"] == rec["source_url"]
    assert rec["id"].startswith("brightwell-analytics-in-") and rec["source"] == "email-alert"
    assert "jd-missing" in rec["tags"] and rec["jd_hash"] is None


def test_the_same_hidden_job_in_two_mails_is_one_record_resighted():
    first = ja.parse_alert(one_job_row("syn-in-1"), PLATFORMS, NOW, ANY_TITLE).records
    second = ja.parse_alert(one_job_row("syn-in-2", tk="1synthtk00000002"), PLATFORMS, NOW, ANY_TITLE).records
    assert first[0]["source_url"] == second[0]["source_url"] and first[0]["id"] == second[0]["id"]
    existing: dict = {}
    assert ja.merge_into(existing, first, NOW)["new"] == [first[0]["id"]]
    assert ja.merge_into(existing, second, NOW)["updated"] == [first[0]["id"]]
    assert len(existing) == 1


def test_a_one_job_mail_whose_subject_is_not_the_job_form_goes_to_review_never_guessed():
    p = ja.parse_alert(one_job_row(subject="A job you might like"), PLATFORMS, NOW, ANY_TITLE)
    assert p.records == [] and p.status == "review"


def test_indeed_marketing_mail_with_only_redirects_to_pages_is_not_parsed_into_jobs():
    links = [{"href": cts(t, click=c), "text": c} for t, c in FOOTER_TARGETS]
    row = {"id": "syn-in-mk", "from": "Indeed Career Services <no-reply@indeed.com>",
           "from_email": "no-reply@indeed.com", "subject": "Free Resume Report",
           "body": "Hello!\nGet resume feedback\nUnsubscribe", "links": links, "internal_date": "2026-09-23T18:00:00Z",
           "classification": {"label": "job_alert"}}
    p = ja.parse_alert(row, PLATFORMS, NOW, ANY_TITLE)
    assert p.records == [] and p.cards == 0 and p.status == "review"


# ----------------------------------------------------------------------------- a digest with HTML cards
DIGEST_HTML = """<html><body>
<table><tr><td><a href="{home}">Indeed</a></td></tr></table>
<table class="job"><tr><td>
  <a href="{a}"><b>Machine Learning Engineer</b></a>
  <div>Quarrystone Labs</div><div>Fairview, OR</div><div>$160,000 - $200,000 a year</div><div>Easily apply</div>
</td></tr></table>
<table class="job"><tr><td>
  <a href="{b}"><b>Applied Scientist, Ranking</b></a>
  <div>Brightwell Analytics</div><div>Lakemont, WA</div><div>Just posted</div>
  <a href="{b}">View job</a>
</td></tr></table>
<p><a href="{unsub}">Unsubscribe</a></p>
</body></html>"""


def digest_row() -> dict:
    a = cts(f"https://www.indeed.com/rc/clk/dl?jk={JK_A}&from=ja&tk=SYNTHtkA")
    b = cts(sponsored(ad="SYNTHadTokenCCCC"), sig="SYNTHsigB")
    html = DIGEST_HTML.format(home=cts(FOOTER_TARGETS[4][0], click="home"), a=a, b=b,
                              unsub=cts(FOOTER_TARGETS[3][0], click="unsubscribe"))
    lines, links, truncated = gmail_pull.alert_html(html, "indeed")
    return {"id": "syn-in-digest", "from": "Indeed <alert@indeed.com>", "from_email": "alert@indeed.com",
            "subject": "2 new machine learning engineer jobs in Fairview, OR", "body": "\n".join(lines),
            "links": links, "links_truncated": truncated, "internal_date": "2026-09-23T15:00:00Z",
            "classification": {"label": "job_alert"}}


def test_a_digest_harvested_with_html_cards_gives_every_job():
    row = digest_row()
    assert sum(1 for i in row["links"] if i.get("card")) == 2          # gmail_pull cards the two redirect jobs
    p = ja.parse_alert(row, PLATFORMS, NOW, ANY_TITLE)
    assert p.read_from == "links" and p.cards == 2 and not p.unparsed
    recs = {r["company"]: r for r in p.records}
    assert recs["Quarrystone Labs"]["source_url"] == f"https://www.indeed.com/viewjob?jk={JK_A}"
    assert recs["Quarrystone Labs"]["title"] == "Machine Learning Engineer"
    hidden = recs["Brightwell Analytics"]
    assert hidden["title"] == "Applied Scientist, Ranking" and hidden["location"] == "Lakemont, WA"
    assert parse_qs(urlsplit(hidden["source_url"]).query) == {
        "q": ["Applied Scientist, Ranking Brightwell Analytics"], "l": ["Lakemont, WA"]}
