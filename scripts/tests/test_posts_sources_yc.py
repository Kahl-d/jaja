"""Post sources are LinkedIn, X and YC (charter section 5): the posts lane's
sweep file takes `yc` rows (Work at a Startup and YC founders' hiring posts), the Recruiter's sweep accepts them, and
posts.py ingest leaves them to the sweep instead of reporting them as problems. Synthetic rows; no network."""
from __future__ import annotations

import hashlib
import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import posts  # noqa: E402
import posts_sweep_check as psc  # noqa: E402
import recruiter_sweep as rs  # noqa: E402

WAAS_JOB = "https://www.workatastartup.com/jobs/71234"
YC_JOB = "https://www.ycombinator.com/companies/acme-ai/jobs/Ab12Cd-founding-ai-engineer"
YC_COMPANY = "https://www.ycombinator.com/companies/acme-ai"


def row(**over):
    url = over.pop("url", WAAS_JOB)
    base = {"platform": "yc", "kind": "hiring_post", "url": url, "author": "Jane Doe",
            "author_title": "Co-founder, Acme AI (YC F25)", "author_url": "https://www.linkedin.com/in/jane-doe/",
            "company": "Acme AI", "posted_at": "2026-10-05", "seen_at": "2026-10-06T14:10:00Z",
            "text": "Founding AI engineer: agents for RF design, San Francisco.", "role_hint": "Founding AI Engineer",
            "location_hint": "San Francisco, CA", "query": "waas:newest",
            "lead_id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]}
    base.update(over)
    return base


def write(folder, day, rows):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"sweep-{day}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_yc_is_a_post_platform_in_both_scripts():
    assert "yc" in psc.PLATFORMS and "yc" in rs.POST_PLATFORMS
    assert {"linkedin", "x"} <= set(psc.PLATFORMS) and {"linkedin", "x"} <= set(rs.POST_PLATFORMS)


def test_yc_rows_pass_the_sweep_check(tmp_path):
    rows = [row(), row(url=YC_JOB, lead_id=psc.lead_id(YC_JOB)),
            row(kind="person", url=YC_COMPANY, text="", lead_id=psc.lead_id(YC_COMPANY)),
            row(kind="startup", url="https://acme.ai", lead_id=psc.lead_id("https://acme.ai"))]
    rep = psc.check_file(write(tmp_path, "2026-10-06", rows))
    assert rep["errors"] == [] and rep["rows"] == 4 and rep["new"] == 4


def test_a_yc_row_must_carry_a_yc_url(tmp_path):
    li = "https://www.linkedin.com/posts/jane-doe_hiring-activity-7380000000000000001-AbCd"
    rows = [row(url=li, lead_id=psc.lead_id(li)),                                   # a LinkedIn post on a yc row
            row(url=YC_COMPANY, lead_id=psc.lead_id(YC_COMPANY)),                   # a company page is not a post
            row(platform="threads")]                                               # still refused
    rep = psc.check_file(write(tmp_path, "2026-10-06", rows))
    lines = sorted({e.split(":")[0] for e in rep["errors"]})
    assert lines == ["line 1", "line 2", "line 3"]
    assert "linkedin, x, yc" in " ".join(rep["errors"])


def test_the_recruiter_sweep_accepts_a_yc_row_and_names_the_three_sources():
    assert rs.check_post_row(row()) is None
    assert rs.check_post_row(row(platform="threads")) == "platform 'threads' is not linkedin, x or yc"


def test_posts_ingest_leaves_yc_rows_to_the_sweep():
    assert posts.from_sweep(row()) is None
    platform, out = posts.from_sweep(row(platform="x", url="https://x.com/janedoe/status/1975000000000000001",
                                         author_url="https://x.com/janedoe"))
    assert platform == "x" and out["author_handle"] == "janedoe"
