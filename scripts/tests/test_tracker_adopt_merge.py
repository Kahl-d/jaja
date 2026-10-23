"""tracker.py: `new` once created acme-...-202610 beside the package-only folder ...-202609 that held
fit.md and jd.md for the same job. (1) `new` adopts an existing package-only folder for the job (its jd.md or fit.md
names the job id, else company plus title match) and drops it from state/package-only.yaml. (2) `merge <from> <into>`
moves one record's events into the other; --dry-run prints the plan only; the emptied folder is left to be removed by hand."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import tracker as tr  # noqa: E402
from js_common import fm, write_record  # noqa: E402
from test_tracker_package_only import _job  # noqa: E402

LATER = NOW.replace(month=NOW.month % 12 + 1) if NOW.month < 12 else NOW

NO_PDF = ["--resume", "none", "--reason", "fixture: no PDF in this test"]   # an applied event names its page



def _pkg(root, slug, job_link="[[unknown]]", company="Acme", title="Applied AI Engineer"):
    folder = root / "applications" / slug
    write_record(folder / "jd.md", {"job": job_link, "company": company, "title": title, "snapshot_at": "2026-09-20"},
                 "# JD snapshot\n")
    write_record(folder / "fit.md", {"job": job_link, "fit_score": 4.6}, "# authored fit\n")
    return folder


def _state(root):
    p = root / "state" / "package-only.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("slugs", []) if p.is_file() else []


def test_new_adopts_the_package_folder_of_the_same_job_even_from_another_month(root):
    jid = _job(root)
    pkg = _pkg(root, "acme-applied-ai-engineer-202608", job_link=f"[[{jid}]]")
    tr.tag_package_only(root)
    assert _state(root) == ["acme-applied-ai-engineer-202608"]
    slug, created = tr.new_application(root, jid, NOW)
    assert slug == "acme-applied-ai-engineer-202608" and created
    assert (pkg / "application.md").is_file() and fm.load(pkg / "fit.md")[0]["fit_score"] == 4.6   # the package is kept
    assert sorted(p.name for p in (root / "applications").iterdir()) == ["acme-applied-ai-engineer-202608"]
    assert _state(root) == []


def test_new_adopts_by_company_and_title_when_the_folder_names_no_job(root):
    jid = _job(root)
    pkg = _pkg(root, "acme-applied-ai-202607", job_link=None, company="acme", title="Applied AI Engineer ")
    slug, created = tr.new_application(root, jid, NOW)
    assert slug == pkg.name and (pkg / "application.md").is_file()


def test_new_does_not_adopt_another_jobs_package(root):
    jid = _job(root)
    _pkg(root, "acme-research-engineer-202608", job_link="[[acme-other]]", title="Research Engineer")
    slug, _ = tr.new_application(root, jid, NOW)
    assert slug != "acme-research-engineer-202608"
    assert not (root / "applications" / "acme-research-engineer-202608" / "application.md").exists()


def test_new_dry_run_names_the_folder_and_writes_nothing(root, capsys):
    jid = _job(root)
    pkg = _pkg(root, "acme-applied-ai-engineer-202608", job_link=f"[[{jid}]]")
    assert tr.main(["--root", str(root), "new", "--job", jid, "--dry-run"], now=NOW) == 0
    assert "acme-applied-ai-engineer-202608" in capsys.readouterr().out
    assert not (pkg / "application.md").exists()


def _twins(root):
    """The duplicate-folder shape: a record created beside the package folder of the same job."""
    jid = _job(root)
    pkg = _pkg(root, "acme-applied-ai-engineer-202608", job_link=f"[[{jid}]]")
    dup = root / "applications" / "acme-applied-ai-engineer-202609"
    tr._write_application(root, dup, f"[[{jid}]]", "Acme", "Applied AI Engineer", "acme", None, "discovered",
                          tr.iso_utc(NOW), "created from the job", NOW)
    assert tr.main(["--root", str(root), "event", dup.name, "applied", *NO_PDF, "--source", "manual", "--evidence",
                    "Owner in session: submitted", "--at", "2026-09-28T10:00:00-07:00", "--via", "ats"], now=NOW) == 0
    return jid, pkg, dup


def test_merge_dry_run_prints_the_plan_and_writes_nothing(root, capsys):
    jid, pkg, dup = _twins(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert tr.main(["--root", str(root), "merge", dup.name, pkg.name, "--dry-run"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "move the record" in out and "applied" in out and f"[[{pkg.name}]]" in out and "left to be removed by hand" in out
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_merge_moves_the_events_relinks_the_job_and_deletes_nothing(root, capsys):
    jid, pkg, dup = _twins(root)
    assert tr.main(["--root", str(root), "merge", dup.name, pkg.name], now=NOW) == 0
    props, body = fm.load(pkg / "application.md")
    assert props["id"] == pkg.name and props["stage"] == "applied" and "applied" in body
    assert fm.load(pkg / "fit.md")[0]["fit_score"] == 4.6                        # the package is kept
    assert (dup / "application.md").is_file()                                     # nothing deleted: removed by hand
    job = tr.load_jobs(root)[jid]
    assert job["application"] == f"[[{pkg.name}]]"
    assert "left to be removed by hand" in capsys.readouterr().out


def test_merge_into_an_existing_record_appends_only_the_missing_events(root):
    jid, pkg, dup = _twins(root)
    tr.adopt_package(root, pkg.name, NOW)
    first = fm.load(pkg / "application.md")[1].count("\n- ")
    tr.main(["--root", str(root), "merge", dup.name, pkg.name], now=NOW)
    body = fm.load(pkg / "application.md")[1]
    assert body.count("applied") >= 1 and body.count("\n- ") == first + 2          # discovered + applied from dup
    tr.main(["--root", str(root), "merge", dup.name, pkg.name], now=NOW)            # idempotent
    assert fm.load(pkg / "application.md")[1].count("\n- ") == first + 2


def test_merge_refuses_a_missing_record_or_a_different_job(root, capsys):
    jid, pkg, dup = _twins(root)
    assert tr.main(["--root", str(root), "merge", "nope", pkg.name, "--dry-run"], now=NOW) == 2
    other = _pkg(root, "acme-research-engineer-202608", job_link="[[acme-other]]", title="Research Engineer")
    assert tr.main(["--root", str(root), "merge", dup.name, other.name, "--dry-run"], now=NOW) == 2
