"""channel_report.py (agency charter 0.1 item 7, section 13): applications by channel (person, recruiter, post, cold,
unknown) with n, replies and screens, this week and all time; and the base order comparison (education first against
experience first), read from the resume each application sent. Tiny fixture trees; no network."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from disc_harness import *  # noqa: F401,F403

import channel_report as cr  # noqa: E402

TODAY = date(2026, 10, 6)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def app(root: Path, slug: str, events: list[str], **meta) -> None:
    head = ["---", "type: application", f"company: {slug}"] + [f"{k}: {v}" for k, v in meta.items()]
    body = ["---", "# app", "", "## Events"] + [f"- {e.split(' ', 1)[0]} · {e.split(' ', 1)[1]} · manual · conf=1.00 · t"
                                                for e in events]
    write(root / "applications" / slug / "application.md", "\n".join(head + body) + "\n")


def resume(root: Path, folder: str, first: str) -> None:
    second = "experience" if first == "education" else "education"
    write(root / folder / "resume.yaml", f"cv:\n  name: K\n  sections:\n    summary: [x]\n    {first}: [a]\n    {second}: [b]\n")


def prospect(root: Path, slug: str, **meta) -> None:
    write(root / "prospects" / f"{slug}.md", "---\n" + "".join(f"{k}: {v}\n" for k, v in {"id": slug, **meta}.items())
          + "---\n# p\n")


def tree(tmp_path: Path) -> Path:
    root = tmp_path / "js"
    resume(root, "resumes/bases/general", "education")
    resume(root, "resumes/variants/base76-experience-first-202610-b76", "experience")
    resume(root, "resumes/variants/acme-tailored", "experience")
    prospect(root, "resma", role="other", company_type="startup")
    prospect(root, "keana", role="recruiter", company_type="unknown")
    prospect(root, "chris", role="engineer", company_type="startup")
    prospect(root, "poster", role="founder", company_type="startup", hiring_post_url="https://x.com/a/status/1")
    write(root / "targets" / "agency-recruiters.yaml",
          "agencies: []\nrecruiters:\n  - {name: Resma, prospect: resma, status: active, source: prospects/resma.md}\n")
    write(root / "jobs" / "postjob.md", "---\nid: postjob\nhiring_signal: {url: 'https://x.com/b/status/2'}\n---\n# j\n")
    base, exp = "resumes/bases/general", "resumes/variants/base76-experience-first-202610-b76"
    app(root, "r1", ["2026-10-01T12:00:00Z applied", "2026-10-02T12:00:00Z screen", "2026-10-05T12:00:00Z rejected"],
        path="person", via_prospect="resma", resume_variant=base)                   # recruiter by the list
    app(root, "r2", ["2026-09-20T12:00:00Z applied", "2026-09-21T12:00:00Z screen"],
        path="person", via_prospect="keana", resume_variant=exp)                    # recruiter by role, last week
    app(root, "p1", ["2026-10-03T12:00:00Z applied"], path="person", via_prospect="chris")
    app(root, "h1", ["2026-10-04T12:00:00Z applied", "2026-10-05T12:00:00Z email_in"],
        path="person", via_prospect="poster")                                         # post: the prospect's post
    app(root, "h2", ["2026-10-04T12:00:00Z applied"], path="cold", job="'[[postjob]]'")   # post: the job's signal
    app(root, "c1", ["2026-10-02T12:00:00Z applied", "2026-10-03T12:00:00Z rejection"], path="cold",
        resume_variant=base)                                                          # a reply, not a screen
    app(root, "c2", ["2026-10-05T12:00:00Z applied"], path="cold", resume_variant=exp)
    app(root, "c3", ["2026-10-05T12:00:00Z applied"], path="cold", resume_variant="resumes/variants/acme-tailored")
    app(root, "u1", ["2026-10-05T12:00:00Z applied"])                                # no path recorded
    app(root, "pkg", ["2026-10-01T12:00:00Z discovered"], path="cold")                 # never applied: not counted
    return root


def test_channels_count_n_replies_and_screens(tmp_path):
    rep = cr.report(tree(tmp_path), TODAY)
    allt = rep["channels"]["all"]
    assert allt["recruiter"] == {"n": 2, "replies": 2, "screens": 2}
    assert allt["post"] == {"n": 2, "replies": 0, "screens": 0}
    assert allt["person"] == {"n": 1, "replies": 0, "screens": 0}
    assert allt["cold"] == {"n": 3, "replies": 1, "screens": 0}
    assert allt["unknown"] == {"n": 1, "replies": 0, "screens": 0}
    week = rep["channels"]["week"]                                                  # applied 2026-09-30 .. 2026-10-06
    assert week["recruiter"]["n"] == 1 and week["cold"]["n"] == 3
    assert {r["slug"]: r["channel"] for r in rep["applications"]}["h2"] == "post"


def test_base_order_is_read_from_the_resume_sent(tmp_path):
    rep = cr.report(tree(tmp_path), TODAY)
    orders = {r["slug"]: r["order"] for r in rep["applications"]}
    assert orders["r1"] == "education_first" and orders["r2"] == "experience_first" and orders["p1"] == "unknown"
    ab = rep["base_order"]
    assert ab["base"]["education_first"] == {"n": 2, "replies": 2, "screens": 1}     # r1, c1
    assert ab["base"]["experience_first"] == {"n": 2, "replies": 1, "screens": 1}    # r2, c2
    assert ab["all"]["experience_first"]["n"] == 3                                  # + c3, a tailored page
    assert ab["unknown"] == 4


def test_render_names_small_n_and_writes_with_write(tmp_path, capsys):
    root = tree(tmp_path)
    text = cr.render(cr.report(root, TODAY))
    assert "| recruiter | 2 | 2 | 2 | 10.0 |" in text
    assert "too few to call" in text
    assert cr.main(["--root", str(root), "--today", "2026-10-06"]) == 0
    assert not (root / "state" / "review" / "channel-report-2026-10-06.md").exists()
    assert cr.main(["--root", str(root), "--today", "2026-10-06", "--write"]) == 0
    assert (root / "state" / "review" / "channel-report-2026-10-06.md").is_file()
    assert "recruiter" in capsys.readouterr().out


def test_a_recorded_base_order_wins_over_the_resume(tmp_path):
    root = tree(tmp_path)
    app(root, "m1", ["2026-10-05T12:00:00Z applied"], path="cold", base_order="experience_first",
        resume_variant="resumes/bases/general")                                       # the resume says education
    app(root, "m2", ["2026-10-05T12:00:00Z applied"], path="cold", base_order="education-first")
    orders = {r["slug"]: r["order"] for r in cr.report(root, TODAY)["applications"]}
    assert orders["m1"] == "experience_first" and orders["m2"] == "education_first"


def test_tracker_metrics_carry_channel_and_base_order_next_to_path(tmp_path):
    from datetime import datetime, timezone

    import tracker
    root = tree(tmp_path)
    m = tracker.metrics(root, datetime(2026, 10, 6, 23, 0, tzinfo=timezone.utc))
    assert m["by_channel"]["recruiter"] == {"applied": 2, "responded": 2, "screen": 2, "rate": 1.0}
    assert m["by_channel"]["cold"]["applied"] == 3 and m["by_channel"]["post"]["applied"] == 2
    assert m["by_base_order"]["unknown"]["applied"] == 4
    assert sum(v["applied"] for v in m["by_channel"].values()) == sum(v["applied"] for v in m["by_path"].values())
