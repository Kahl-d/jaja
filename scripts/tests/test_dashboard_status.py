"""dashboard.py: the one generated status section: funnel with folder counts, today's queue,
waiting-on (the owner, companies, people), the clock line; read by the Recruiter (--status) and HOME (the feed)."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import dashboard as db  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402


def run(root, os_root, *extra):
    return db.main(["--root", str(root), "--os-root", str(os_root), *extra], now=NOW)


def _write(path, meta, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump(meta, body), encoding="utf-8")


def _app(root, slug, company, events, next_action_at=None, next_action="x"):
    lines = "\n".join(tr.format_event(at, t, "manual", 1.0, "e") for at, t in events)
    _write(root / "applications" / slug / "application.md",
           {"id": slug, "company": company, "title": f"{company} AI Engineer", "next_action": next_action,
            "next_action_at": next_action_at}, f"# {slug}\n\n## Events\n{lines}\n")


def populate(root, os_root):
    _app(root, "acme-ai-202609", "Acme", [("2026-09-20T10:00:00Z", "discovered"), ("2026-09-22T10:00:00Z", "tailored")],
         next_action_at="2026-09-24", next_action="apply: apply pack, the owner submits (apply-assist)")
    _app(root, "beta-ai-202609", "Beta", [("2026-09-01T10:00:00Z", "discovered"), ("2026-09-10T10:00:00Z", "applied")],
         next_action_at="2026-09-30")
    _app(root, "kappa-ai-202609", "Kappa", [("2026-09-01T10:00:00Z", "applied"), ("2026-09-12T10:00:00Z", "rejected")],
         next_action_at="2026-09-01")
    pkg = root / "applications" / "delta-ai-202609"
    _write(pkg / "jd.md", {"company": "Delta", "title": "AI Engineer"}, "# jd\n")
    (pkg / "KIND").write_text("package-only\n")
    _write(os_root / "work" / "approvals" / "A-2026-0001.md",
           {"id": "A-2026-0001", "action": "send-email", "status": "pending", "project": "job-search",
            "created": "2026-09-23", "expires": "2026-09-30"})


def test_status_absent_is_not_empty(root, os_root):
    assert run(root, os_root) == 0
    data = json.loads((root / "state" / "dashboard.json").read_text())
    st = data["status"]
    assert st["queue"] is None and st["waiting_on"]["companies"] is None and st["funnel"] is None
    text = (root / "DASHBOARD.md").read_text()
    assert "## Status" in text and "Next actions due: no data yet" in text


def test_status_data_queue_waiting_and_funnel(root, os_root):
    populate(root, os_root)
    run(root, os_root)
    st = json.loads((root / "state" / "dashboard.json").read_text())["status"]
    assert [q["application"] for q in st["queue"]] == ["acme-ai-202609"]            # due today, not terminal
    assert st["queue"][0]["due"] == "2026-09-24" and st["queue"][0]["stage"] == "tailored"
    comp = st["waiting_on"]["companies"]
    assert [c["application"] for c in comp] == ["beta-ai-202609"]                    # applied, no reply yet
    assert comp[0]["days_since_activity"] == 14
    assert st["waiting_on"]["owner"]["approvals"] == 1
    f = st["funnel"]
    assert f["folders"] == 4 and f["applications"] == 3 and f["package_only"] == 1 and f["untagged"] == 0
    assert f["applied"] == 2 and f["rejected"] == 1


def test_status_section_renders_first_and_reads_plainly(root, os_root):
    populate(root, os_root)
    run(root, os_root)
    text = (root / "DASHBOARD.md").read_text()
    assert text.index("## Status") < text.index("## Needs Alex")
    section = text[text.index("## Status"):text.index("## Needs Alex")]
    assert "Funnel: folders 4 (applications 3, package-only 1, untagged 0)" in section
    assert "Next actions due (1) (applying is paused: steps before applying wait):" in section and "Acme: Acme AI Engineer" in section
    assert "Waiting on companies (1):" in section and "14 days since last activity" in section
    assert "Waiting on Alex: approvals 1" in section
    assert "—" not in section


def test_status_flag_prints_only_the_section_and_writes_nothing(root, os_root, capsys):
    populate(root, os_root)
    assert run(root, os_root, "--status") == 0
    out = capsys.readouterr().out
    assert out.lstrip().startswith("## Status") or out.splitlines()[0].startswith("Job clock")
    assert "## Status" in out and "## Needs Alex" not in out
    assert not (root / "DASHBOARD.md").exists() and not (root / "state").exists()


def test_home_feed_carries_one_status_row(root, os_root):
    populate(root, os_root)
    run(root, os_root)
    feed = json.loads((root / "state" / "home-feed.json").read_text())
    rows = [r for r in feed["today"] if r["title"].startswith("Job search:")]
    assert rows == [{"title": "Job search: 1 due today while applying is paused, 1 waiting on companies", "link": rows[0]["link"]}]
    assert rows[0]["link"].endswith("DASHBOARD.md")


def test_long_lists_are_capped(root, os_root):
    for n in range(12):
        _app(root, f"co{n:02d}-ai-202609", f"Co{n:02d}", [("2026-09-20T10:00:00Z", "tailored")],
             next_action_at="2026-09-23")
    run(root, os_root)
    text = (root / "DASHBOARD.md").read_text()
    section = text[text.index("## Status"):text.index("## Needs Alex")]
    assert "Next actions due (12: 12 past due, oldest 2026-09-23; 0 due today) (applying is paused: steps before applying wait):" in section
    assert "and 2 more" in section
    assert section.count("· tailored ·") == db.STATUS_CAP
