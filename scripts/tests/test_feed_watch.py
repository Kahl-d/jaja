"""feed_watch.py (engineering-blog and careers feeds of the dream companies.
`discover` finds each company's RSS or Atom feeds from the <link rel=alternate> tags of a few of its own pages
(robots-first, one fetch each) and proposes them; `run` reads the feeds and lists the posts of the last 7 days that name
a hiring team, a launch or a role, by fixed keyword rules. Nothing outward; untrusted text is data."""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp

import feed_watch as fw  # noqa: E402


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(fw, "PAUSE", 0)

TODAY = date(2026, 10, 2)
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Acme Engineering</title>
<item><title>We're hiring: the Agents team is growing</title><link>https://acme.ai/blog/agents-hiring</link>
<pubDate>Thu, 01 Oct 2026 16:00:00 GMT</pubDate><description>Join us to build evals.</description></item>
<item><title>Introducing Acme Evals 2.0</title><link>https://acme.ai/blog/evals-2</link>
<pubDate>Tue, 29 Sep 2026 16:00:00 GMT</pubDate><description>Now available to everyone.</description></item>
<item><title>Our offsite in Lisbon</title><link>https://acme.ai/blog/offsite</link>
<pubDate>Wed, 30 Sep 2026 16:00:00 GMT</pubDate><description>Photos.</description></item>
<item><title>We're hiring for 2025</title><link>https://acme.ai/blog/old</link>
<pubDate>Mon, 01 Sep 2026 16:00:00 GMT</pubDate><description>Old news.</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Acme Careers</title>
<entry><title>Machine Learning Engineer, Agents</title><link href="https://acme.ai/careers/mle-agents"/>
<updated>2026-09-30T10:00:00Z</updated><summary>A new role on the platform team.</summary></entry>
</feed>"""
HOME = ('<html><head><link rel="alternate" type="application/rss+xml" title="Blog" href="/blog/rss.xml">'
        '<link rel="alternate" type="application/atom+xml" href="https://acme.ai/careers/feed.atom"></head></html>')


class PageHttp(FakeHttp):
    pass


def _http(robots="User-agent: *\nAllow: /\n"):
    r = {("GET", "https://acme.ai/robots.txt"): robots, ("GET", "https://acme.ai/"): HOME,
         ("GET", "https://acme.ai/blog/rss.xml"): RSS, ("GET", "https://acme.ai/careers/feed.atom"): ATOM}
    for path in ("/blog", "/engineering", "/news", "/newsroom"):
        r[("GET", f"https://acme.ai{path}")] = (404, "")
    return FakeHttp(r)


CO = {"name": "Acme", "slug": "acme", "careers_url": "https://jobs.acme.ai/careers"}


def test_discover_reads_the_alternate_links_robots_first_and_checks_each_feed():
    http = _http()
    feeds, notes = fw.discover(http, CO, pause=0)
    assert [u for _, u, _ in http.calls][0] == "https://acme.ai/robots.txt"
    assert feeds == ["https://acme.ai/blog/rss.xml", "https://acme.ai/careers/feed.atom"]


def test_discover_honours_robots():
    http = _http(robots="User-agent: *\nDisallow: /\n")
    feeds, notes = fw.discover(http, CO, pause=0)
    assert feeds == [] and any("robots" in n for n in notes)
    assert [u for _, u, _ in http.calls] == ["https://acme.ai/robots.txt"]


def test_a_link_that_is_not_a_feed_is_not_proposed():
    http = _http()
    http.routes[("GET", "https://acme.ai/careers/feed.atom")] = "<html>not a feed</html>"
    feeds, _ = fw.discover(http, CO, pause=0)
    assert feeds == ["https://acme.ai/blog/rss.xml"]


def test_parse_reads_rss_and_atom():
    rss = fw.parse_feed(RSS)
    assert rss[0]["title"].startswith("We're hiring") and rss[0]["date"] == "2026-10-01"
    atom = fw.parse_feed(ATOM)
    assert atom[0]["link"] == "https://acme.ai/careers/mle-agents" and atom[0]["date"] == "2026-09-30"


def test_classify_by_fixed_rules_within_seven_days(root):
    posts = fw.parse_feed(RSS) + fw.parse_feed(ATOM)
    kept = fw.classify(posts, TODAY, fw.title_filter(root))
    kinds = {p["title"]: p["kinds"] for p in kept}
    assert kinds["We're hiring: the Agents team is growing"] == ["hiring"]
    assert kinds["Introducing Acme Evals 2.0"] == ["launch"]
    assert "role" in kinds["Machine Learning Engineer, Agents"]
    assert "Our offsite in Lisbon" not in kinds and "We're hiring for 2025" not in kinds     # no rule; too old


def test_run_writes_the_report_from_companies_yaml_feeds(root, monkeypatch, capsys):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(yaml.safe_dump({"companies": [dict(CO, feeds=[
        "https://acme.ai/blog/rss.xml", "https://acme.ai/careers/feed.atom"])]}))
    monkeypatch.setattr(fw, "_http", lambda: _http())
    assert fw.main(["run", "--root", str(root), "--today", TODAY.isoformat(), "--write"]) == 0
    text = (root / "state" / "feeds" / f"feed-watch-{TODAY.isoformat()}.md").read_text()
    assert "We're hiring: the Agents team is growing" in text and "Introducing Acme Evals 2.0" in text
    assert "offsite" not in text and "untrusted" in text


def test_discover_proposes_feeds_lines_and_a_dry_run_writes_nothing(root, monkeypatch, capsys):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(yaml.safe_dump({"companies": [CO]}))
    monkeypatch.setattr(fw, "_http", lambda: _http())
    assert fw.main(["discover", "--root", str(root), "--company", "acme"]) == 0
    assert not (root / "state").exists() and "https://acme.ai/blog/rss.xml" in capsys.readouterr().out
    monkeypatch.setattr(fw, "_http", lambda: _http())
    assert fw.main(["discover", "--root", str(root), "--company", "acme", "--propose"]) == 0
    rows = [json.loads(x) for x in (root / "state" / "proposals" / "companies.jsonl").read_text().splitlines()]
    assert {(r["slug"], r["field"]) for r in rows} == {("acme", "feeds")} and len(rows) == 2


def test_run_can_read_the_proposed_feeds_before_they_are_applied(root, monkeypatch):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(yaml.safe_dump({"companies": [CO]}))
    p = root / "state" / "proposals" / "companies.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"slug": "acme", "field": "feeds", "value": "https://acme.ai/blog/rss.xml",
                             "source": "feed_watch"}) + "\n")
    monkeypatch.setattr(fw, "_http", lambda: _http())
    out = fw.watch(root, TODAY, use_proposals=True)
    assert [p["title"] for p in out["posts"]][:2] == ["We're hiring: the Agents team is growing", "Introducing Acme Evals 2.0"]


def test_a_domain_whose_terms_forbid_automated_reading_is_never_fetched():
    http = FakeHttp({})
    feeds, notes = fw.discover(http, {"slug": "linkedin", "careers_url": "https://careers.linkedin.com/"}, pause=0)
    assert feeds == [] and http.calls == [] and any("terms" in n for n in notes)


def test_a_careers_url_on_a_job_host_needs_the_company_domain_and_takes_an_override():
    http = FakeHttp({})
    feeds, notes = fw.discover(http, {"slug": "sim", "careers_url": "https://jobs.ashbyhq.com/sim"}, pause=0)
    assert feeds == [] and http.calls == [] and any("job host" in n and "--domain" in n for n in notes)
    http = _http()
    feeds, _ = fw.discover(http, {"slug": "acme", "careers_url": "https://jobs.ashbyhq.com/acme"}, pause=0, domain="acme.ai")
    assert feeds == ["https://acme.ai/blog/rss.xml", "https://acme.ai/careers/feed.atom"]


def test_a_platform_listed_in_platforms_yaml_is_never_fetched(root):
    (root / "targets").mkdir()
    (root / "targets" / "platforms.yaml").write_text(
        "platforms:\n  - {id: google-careers, name: Google Careers, url: https://www.google.com/about/careers}\n")
    http = FakeHttp({})
    feeds, notes = fw.discover(http, {"slug": "google", "careers_url": "https://www.google.com/about/careers/"},
                               pause=0, js_root=root)
    assert feeds == [] and http.calls == [] and any("terms" in n for n in notes)


def test_a_blog_on_a_subdomain_linked_from_the_home_page_is_followed():
    home = '<html><a href="https://blog.acme.ai/">Blog</a><a href="https://other.example/">x</a></html>'
    r = {("GET", "https://acme.ai/robots.txt"): "User-agent: *\nAllow: /\n", ("GET", "https://acme.ai/"): home,
         ("GET", "https://blog.acme.ai/robots.txt"): "User-agent: *\nAllow: /\n",
         ("GET", "https://blog.acme.ai/"): "<html>no link tags</html>", ("GET", "https://blog.acme.ai/feed"): (404, ""),
         ("GET", "https://blog.acme.ai/rss"): RSS}
    for path in ("/blog", "/engineering", "/news", "/newsroom"):
        r[("GET", f"https://acme.ai{path}")] = (404, "")
    http = FakeHttp(r)
    feeds, _ = fw.discover(http, CO, pause=0)
    assert feeds == ["https://blog.acme.ai/rss"]
    assert not any("other.example" in u for _, u, _ in http.calls)


def test_comment_feeds_are_not_post_feeds():
    page = ('<link rel="alternate" type="application/rss+xml" href="https://blogs.acme.ai/feed/">'
            '<link rel="alternate" type="application/rss+xml" href="https://blogs.acme.ai/comments/feed/">'
            '<link rel="alternate" type="application/rss+xml" href="https://blogs.acme.ai/2014/10/28/a-post/feed/">')
    assert fw.feed_links(page, "https://acme.ai/") == ["https://blogs.acme.ai/feed/"]


def test_a_product_post_is_not_a_role_even_when_the_title_filter_matches_a_word(root):
    # live 2026-10-01: "Jev is now available in LangSmith Evals" was tagged role
    posts = [{"title": "Jev is now available in LangSmith Evals", "link": "x", "date": "2026-09-30", "summary": ""},
             {"title": "Applied AI Engineer, Agents", "link": "y", "date": "2026-09-30", "summary": ""}]
    import fetch_ats as fa
    tf = fa.TitleFilter([fa.title_pattern("evals"), fa.title_pattern("ai engineer")], [])   # as roles.md matched live
    assert tf.matches("Jev is now available in LangSmith Evals")
    kinds = {p["title"]: p["kinds"] for p in fw.classify(posts, TODAY, tf)}
    assert kinds["Jev is now available in LangSmith Evals"] == ["launch"]
    assert kinds["Applied AI Engineer, Agents"] == ["role"]
