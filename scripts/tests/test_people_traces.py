"""people_traces.py: the public traces a role-analyst reads for the Reader model's "the people" bullet:
company-site author, team, blog and talk pages from its own sitemap (robots.txt respected) and GitHub's API (org,
public members, recent repos). No LinkedIn, no scraping, nothing written."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp

import fetch_ats as fa  # noqa: E402
import people_traces as pt  # noqa: E402

CO = {"slug": "acme", "name": "Acme AI", "domain": "acme.ai", "github_org": "acme-ai"}
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://acme.ai/blog/building-agent-evals</loc></url>
<url><loc>https://acme.ai/blog/our-new-office</loc></url>
<url><loc>https://acme.ai/blog/author/jane-doe</loc></url>
<url><loc>https://acme.ai/team</loc></url>
<url><loc>https://acme.ai/talks/agents-at-scale-2026</loc></url>
<url><loc>https://acme.ai/private/notes</loc></url>
<url><loc>https://acme.ai/pricing</loc></url>
<url><loc>https://www.linkedin.com/company/acme</loc></url>
</urlset>"""
GH = "https://api.github.com"


def routes(robots="User-agent: *\nDisallow: /private\nSitemap: https://acme.ai/sitemap.xml\n", org=True):
    r = {("GET", "https://acme.ai/robots.txt"): robots,
         ("GET", "https://acme.ai/sitemap.xml"): SITEMAP,
         ("GET", f"{GH}/orgs/acme-ai/public_members"): [{"login": "janedoe", "html_url": "https://github.com/janedoe"}],
         ("GET", f"{GH}/orgs/acme-ai/repos"): [
             {"name": "agent-evals", "html_url": "https://github.com/acme-ai/agent-evals", "description": "Evals for agents",
              "pushed_at": "2026-09-28T00:00:00Z", "fork": False},
             {"name": "website", "html_url": "https://github.com/acme-ai/website", "description": "", "pushed_at":
              "2026-09-01T00:00:00Z", "fork": False}],
         ("GET", f"{GH}/users/janedoe/repos"): [
             {"name": "tiny-agent", "html_url": "https://github.com/janedoe/tiny-agent", "description": "an agent loop",
              "pushed_at": "2026-09-20T00:00:00Z", "fork": False}]}
    r[("GET", f"{GH}/orgs/acme-ai")] = ({"login": "acme-ai", "html_url": "https://github.com/acme-ai", "name": "Acme AI"}
                                        if org else fa.FetchError("GET orgs/acme-ai: HTTP 404"))
    return FakeHttp(r)


def test_traces_come_from_the_sitemap_and_github_with_their_kind_and_source():
    out = pt.traces(routes(), CO, team="agent", pause=0)
    urls = {t["url"]: t for t in out["traces"]}
    assert urls["https://acme.ai/blog/author/jane-doe"]["kind"] == "author-page"
    assert urls["https://acme.ai/team"]["kind"] == "team-page"
    assert urls["https://acme.ai/talks/agents-at-scale-2026"]["kind"] == "talk"
    assert urls["https://acme.ai/blog/building-agent-evals"]["kind"] == "blog-post"
    assert "https://acme.ai/private/notes" not in urls                       # robots.txt disallows it
    assert "https://acme.ai/pricing" not in urls and not any("linkedin.com" in u for u in urls)
    assert urls["https://github.com/acme-ai"]["kind"] == "github-org"
    assert urls["https://github.com/janedoe"]["kind"] == "github-member"
    assert urls["https://github.com/janedoe/tiny-agent"]["kind"] == "member-repo"
    assert all(t["source"] in ("company-site", "github-api") for t in out["traces"])
    posts = [t["url"] for t in out["traces"] if t["kind"] == "blog-post"]
    assert posts[0] == "https://acme.ai/blog/building-agent-evals"            # the team keyword ranks first


def test_a_missing_github_org_and_a_site_that_disallows_everything_give_notes_not_errors():
    out = pt.traces(routes(robots="User-agent: *\nDisallow: /\n", org=False), CO, pause=0)
    assert [t for t in out["traces"] if t["source"] == "company-site"] == []
    assert not any(t["source"] == "github-api" for t in out["traces"])
    assert any("robots.txt" in n for n in out["notes"]) and any("github" in n.lower() for n in out["notes"])


def test_markdown_render_names_the_rule_that_a_trace_is_a_lead_not_an_identity():
    text = pt.render(pt.traces(routes(), CO, team="agent", pause=0))
    assert text.startswith("# Public traces: Acme AI") and "two independent sources" in text


# An HTTP 429 must not crash the run; that source is skipped with a recorded status.
def _limited(url):
    return fa.FetchError(f"GET {url}: HTTP 429", status=429, retry_after=60.0)


def test_a_rate_limited_github_is_skipped_with_a_status_and_the_site_still_reads():
    http = routes()
    http.routes[("GET", f"{GH}/orgs/acme-ai/public_members")] = _limited("public_members")
    out = pt.traces(http, CO, team="agent", pause=0)
    assert out["sources"]["company-site"] == "ok"
    assert out["sources"]["github-api"].startswith("skipped: rate-limited (HTTP 429")
    assert any(t["source"] == "company-site" for t in out["traces"])
    assert any(t["kind"] == "org-repo" for t in out["traces"])         # rows read before the 429 are kept
    assert out["warnings"] and "github-api" in out["warnings"][0]


def test_a_rate_limited_site_is_skipped_and_github_still_reads():
    http = routes()
    http.routes[("GET", "https://acme.ai/sitemap.xml")] = _limited("sitemap.xml")
    out = pt.traces(http, CO, pause=0)
    assert out["sources"]["company-site"].startswith("skipped: rate-limited")
    assert out["sources"]["github-api"] == "ok"
    assert any(t["kind"] == "github-org" for t in out["traces"])


def test_the_cli_exits_0_and_prints_the_warning_on_a_429(monkeypatch, capsys):
    http = routes()
    http.routes[("GET", f"{GH}/orgs/acme-ai")] = _limited("orgs/acme-ai")
    monkeypatch.setattr(pt, "Http", lambda **kw: http)
    monkeypatch.setattr(pt, "PAUSE", 0)
    rc = pt.main(["--company", "acme", "--domain", "acme.ai", "--github-org", "acme-ai"])
    text = capsys.readouterr().out
    assert rc == 0 and "## Warnings" in text and "rate-limited" in text


def test_the_cli_asks_http_to_honour_retry_after(monkeypatch):
    seen = {}
    monkeypatch.setattr(pt, "Http", lambda **kw: seen.update(kw) or routes())
    monkeypatch.setattr(pt, "PAUSE", 0)
    pt.main(["--company", "acme", "--domain", "acme.ai", "--github-org", "acme-ai", "--json"])
    assert 0 < seen.get("max_retry_after", 0) <= pt.RETRY_AFTER_CAP


# `--company chainco` guessed the org "chainco" (404); the org is "chainco-ai". Discover
# it: companies.yaml github_org, else slug, slug-ai, slug-inc, slughq (one request each), the first whose name or blog
# matches the company; a discovered org is written as a proposal (never silently into companies.yaml).
LC = {"slug": "chainco", "name": "ChainCo", "domain": "chainco.com"}


def _orgs(**found):
    r = {("GET", f"{GH}/orgs/{o}"): fa.FetchError(f"GET orgs/{o}: HTTP 404", status=404)
         for o in ("chainco", "chainco-ai", "chainco-inc", "chaincohq")}
    for o, info in found.items():
        r[("GET", f"{GH}/orgs/{o.replace('_', '-')}")] = info
    return FakeHttp(r)


def test_the_org_is_found_by_its_name_or_blog_after_the_slug_404s():
    http = _orgs(chainco_ai={"login": "chainco-ai", "name": "ChainCo", "blog": "https://www.chainco.com"})
    org, how = pt.discover_org(http, LC, [])
    assert (org, how) == ("chainco-ai", "discovered")
    assert [u for _, u, _ in http.calls] == [f"{GH}/orgs/chainco", f"{GH}/orgs/chainco-ai"]   # stops at the match


def test_an_org_whose_name_and_blog_do_not_match_is_not_taken():
    http = _orgs(chainco_ai={"login": "chainco-ai", "name": "Chain Gang", "blog": "https://chaingang.example"})
    notes = []
    org, how = pt.discover_org(http, LC, notes)
    assert org is None and any("chainco-ai" in n and "does not match" in n for n in notes)


def test_companies_yaml_github_org_wins_without_a_request():
    http = FakeHttp({})
    assert pt.discover_org(http, dict(LC, github_org="chainco-ai"), []) == ("chainco-ai", "companies.yaml")
    assert http.calls == []


def test_a_discovered_org_is_written_as_a_proposal_and_said(tmp_path, monkeypatch, capsys):
    http = _orgs(chainco_ai={"login": "chainco-ai", "name": "ChainCo", "blog": "https://www.chainco.com",
                               "html_url": "https://github.com/chainco-ai"})
    http.routes.update({("GET", f"{GH}/orgs/chainco-ai/repos"): [], ("GET", f"{GH}/orgs/chainco-ai/public_members"): []})
    monkeypatch.setattr(pt, "Http", lambda **kw: http)
    monkeypatch.setattr(pt, "_company", lambda root, slug: {"slug": "chainco", "name": "ChainCo"})   # no site read
    rc = pt.main(["--root", str(tmp_path), "--company", "chainco", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and '"github_org": "chainco-ai"' in out
    rows = (tmp_path / "state" / "proposals" / "companies.jsonl").read_text().splitlines()
    import json as _j
    row = _j.loads(rows[-1])
    assert (row["slug"], row["field"], row["value"], row["source"]) == ("chainco", "github_org", "chainco-ai",
                                                                        "people_traces")
    assert "companies_set.py set chainco github_org chainco-ai" in row["apply"]
    assert not (tmp_path / "targets").exists()                         # companies.yaml itself is never written here
