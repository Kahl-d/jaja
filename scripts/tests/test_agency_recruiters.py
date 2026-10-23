"""Recruiters who place AI engineers (agency charter): targets/agency-recruiters.yaml lists the
agencies and named recruiters with a cited source, and the morning sweep counts today's posts and mails from them.
Tiny fixture trees for the count; the real list is checked for shape and sources. No network."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS
from layout import JS  # noqa: E402


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _load("recruiter_sweep", "js_sweep_agency_recruiters")
NOW = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
LIST = """
agencies:
  - {slug: contoso-staffing, name: Contoso Staffing, mail_domains: [contoso-staffing.example.test], status: active, source_url: "https://www.contoso-staffing.example.test"}
  - {slug: fabrikam-talent, name: Fabrikam Talent, mail_domains: [fabrikam-talent.example.test], status: hold, source_url: "https://www.fabrikam-talent.example.test"}
recruiters:
  - {name: Sam Rivera, agency: null, prospect: sam-rivera, emails: [sam.rivera@staffing.example.test],
     linkedin_url: "https://www.linkedin.com/in/sam-rivera/", x_handle: null, status: active, source: prospects/sam-rivera.md}
  - {name: Priya Nair, agency: null, prospect: priya-nair, emails: [], linkedin_url: null, x_handle: priyanair,
     status: active, source: prospects/priya-nair.md}
"""


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def mail_row(mid: str, at: str, sender: str) -> dict:
    return {"id": mid, "thread_id": mid, "internal_date": at, "direction": "in", "from": f"X <{sender}>",
            "from_email": sender, "subject": "A role", "classification": {"label": "recruiter_outreach"}}


def lead(url: str, author_url: str | None, company: str, platform: str = "linkedin") -> dict:
    return {"platform": platform, "kind": "hiring_post", "url": url, "author": "Someone", "author_title": None,
            "author_url": author_url, "company": company, "posted_at": None, "seen_at": "2026-10-06T16:00:00Z",
            "text": "We're hiring", "role_hint": None, "location_hint": None, "query": "q",
            "lead_id": sweep.lead_id_for(url)}


def tree(tmp_path: Path, with_list: bool = True) -> tuple[Path, Path, Path]:
    root, os_root = tmp_path / "js", tmp_path / "os"
    (root / "targets").mkdir(parents=True)
    (os_root / "work" / "approvals").mkdir(parents=True)
    if with_list:
        (root / "targets" / "agency-recruiters.yaml").write_text(LIST, encoding="utf-8")
    write_jsonl(root / "state" / "harvest" / "gmail-2026-10-06.classified.jsonl", [
        mail_row("m1", "2026-10-06T16:00:00Z", "Sam.Rivera@staffing.example.test"),        # a named recruiter (case-insensitive)
        mail_row("m2", "2026-10-06T16:10:00Z", "ann@contoso-staffing.example.test"),                # an agency domain
        mail_row("m3", "2026-10-06T16:20:00Z", "stephen@fabrikam-talent.example.test"),           # a held agency: counted as held
        mail_row("m4", "2026-10-06T16:30:00Z", "pat@acme.example.test")])                # nobody on the list
    write_jsonl(root / "state" / "posts" / "sweep-2026-10-06.jsonl", [
        lead("https://www.linkedin.com/posts/a_1", "https://linkedin.com/in/Sam-Rivera", "Globex"),
        lead("https://www.linkedin.com/posts/b_2", None, "Contoso Staffing"),
        lead("https://x.com/PriyaNair/status/1", "https://x.com/PriyaNair", "Initech", platform="x"),
        lead("https://www.linkedin.com/posts/c_3", "https://www.linkedin.com/in/pat/", "Acme")])
    return root, os_root, root / "state" / "recruiter"


def test_sweep_counts_posts_and_mails_from_listed_recruiters(tmp_path):
    root, os_root, out = tree(tmp_path)
    res = sweep.build(root, os_root, NOW, "2026-10-06", out)
    rec = res["recruiters"]
    assert rec["posts"] == 3 and rec["mails"] == 3 and rec["held"] == 1
    assert sorted(rec["who"]) == ["Contoso Staffing", "Fabrikam Talent", "Priya Nair", "Sam Rivera"]
    line = next(x for x in sweep.summary(res) if x.startswith("Recruiters who place AI engineers"))
    assert "posts 3" in line and "mails 3" in line and "held 1" in line
    assert "Recruiters who place AI engineers" in sweep.render(res)


def test_no_list_says_no_data_yet_never_zero(tmp_path):
    root, os_root, out = tree(tmp_path, with_list=False)
    res = sweep.build(root, os_root, NOW, "2026-10-06", out)
    assert res["recruiters"] is None
    line = next(x for x in sweep.summary(res) if x.startswith("Recruiters who place AI engineers"))
    assert "no data yet" in line


@pytest.mark.real_records
def test_the_real_list_has_a_source_on_every_row():
    data = yaml.safe_load((JS / "targets" / "agency-recruiters.yaml").read_text(encoding="utf-8"))
    slugs = {a["slug"] for a in data["agencies"]}
    assert len(slugs) == len(data["agencies"]) >= 10
    for a in data["agencies"]:
        assert a["source_url"].startswith("https://") and a["source_says"] and a["status"] in ("active", "hold")
        assert a["places"] and a["channels"]
    for r in data["recruiters"]:
        assert r["source"] and r["status"] in ("active", "hold") and r["channel"]
        assert r["agency"] is None or r["agency"] in slugs
        if r["prospect"]:                                                    # a record holds the thread
            assert r["source"] == f"prospects/{r['prospect']}.md" and (JS / r["source"]).is_file()


cards = _load("cards", "js_cards_agency_recruiters")


def test_a_recruiter_post_or_mail_becomes_a_tier_0_recruiter_card(tmp_path):
    root, os_root, out = tree(tmp_path)
    res = sweep.build(root, os_root, NOW, "2026-10-06", out)
    by_key = {r["key"]: r for g in res["groups"].values() for r in g or []}
    assert by_key["mail:m1"]["recruiter"] == "Sam Rivera" and by_key["mail:m3"]["recruiter_held"] is True
    deck = cards.build(root, json.loads(json.dumps(res, default=str)))
    rc = [c for c in deck["cards"] if c["title"].startswith("[recruiter ")]
    assert {c["item_key"] for c in rc} == {"mail:m1", "mail:m2", f"lead:{sweep.lead_id_for('https://www.linkedin.com/posts/a_1')}",
                                           f"lead:{sweep.lead_id_for('https://www.linkedin.com/posts/b_2')}",
                                           f"lead:{sweep.lead_id_for('https://x.com/PriyaNair/status/1')}"}
    assert all(c["tier"] == 0 and "outside recruiter who places AI engineers" in " ".join(c["why"]) for c in rc)
    assert not any(c["item_key"] == "mail:m3" for c in deck["cards"])     # Fabrikam Talent is on hold: counted, not carded
    assert next(c for c in rc if c["item_key"] == "mail:m1")["action"] == "reply"
