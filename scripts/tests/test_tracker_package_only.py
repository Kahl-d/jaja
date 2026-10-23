"""tracker.py: package-only folders (an application folder with a package but no application.md) are tagged,
counted apart from applications so the counts match, and a manual apply on one logs cleanly.
The tag lives in state/package-only.yaml (application folders are read only for us); a legacy KIND
marker in a folder is still read and is folded into the state file."""
from __future__ import annotations

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm, write_record  # noqa: E402

NO_PDF = ["--resume", "none", "--reason", "fixture: no PDF in this test"]   # an applied event names its page



def _job(root):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title="Applied AI Engineer", source="greenhouse",
                         source_url="https://job-boards.greenhouse.io/acme/jobs/1", apply_url=None,
                         ats_vendor="greenhouse", posted_at="2026-09-22T09:00:00Z", location="San Francisco, CA",
                         remote="onsite", comp=(200000, 250000, "USD"), employment_type="full-time",
                         jd_text="Build agents.", now=NOW, req_id="1234")
    fa.upsert_jobs([rec], root, NOW)
    return rec["id"]


def _package(root, slug, job_link="[[unknown-job]]", **props):
    folder = root / "applications" / slug
    write_record(folder / "jd.md", {"job": job_link, "company": "Example Labs", "title": "AI Engineer - Agents",
                                    "snapshot_at": "2026-09-20", **props}, "# JD snapshot\n")
    write_record(folder / "fit.md", {"job": job_link, "fit_score": 4.2}, "# authored fit\n")
    return folder


def _state(root):
    p = root / "state" / "package-only.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("slugs", []) if p.is_file() else None


def _application(root, job_id):
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    return root / "applications" / "acme-applied-ai-engineer-202609"


# ----------------------------------------------------------------------------- tag-package-only
def test_tag_marks_only_folders_without_an_application(root, capsys):
    app = _application(root, _job(root))
    pkg = _package(root, "example-labs-ai-engineer-agents-202609")
    assert tr.main(["--root", str(root), "tag-package-only"], now=NOW) == 0
    assert _state(root) == ["example-labs-ai-engineer-agents-202609"]
    assert not (pkg / "KIND").exists() and not (app / "KIND").exists()         # nothing written inside a folder
    out = capsys.readouterr().out
    assert "tagged 1" in out and "applications 1" in out and "folders 2" in out


def test_tag_is_idempotent_and_dry_run_writes_nothing(root, capsys):
    pkg = _package(root, "example-labs-ai-engineer-agents-202609")
    assert tr.main(["--root", str(root), "tag-package-only", "--dry-run"], now=NOW) == 0
    assert _state(root) is None and not (pkg / "KIND").exists()
    assert "would tag 1" in capsys.readouterr().out
    tr.main(["--root", str(root), "tag-package-only"], now=NOW)
    capsys.readouterr()
    assert tr.main(["--root", str(root), "tag-package-only"], now=NOW) == 0
    assert "tagged 0" in capsys.readouterr().out and _state(root) == ["example-labs-ai-engineer-agents-202609"]


def test_tag_reports_a_stale_marker_and_the_application_wins(root, capsys):
    app = _application(root, _job(root))
    (app / "KIND").write_text("package-only\n")
    assert tr.main(["--root", str(root), "tag-package-only"], now=NOW) == 0
    assert "stale" in capsys.readouterr().out
    assert tr.folder_kind(app) == "application"


def test_an_application_record_drops_its_slug_from_the_state_file(root, capsys):
    pkg = _package(root, "acme-applied-ai-engineer-202609")
    tr.main(["--root", str(root), "tag-package-only"], now=NOW)
    assert _state(root) == ["acme-applied-ai-engineer-202609"]
    write_record(pkg / "application.md", {"type": "application", "id": pkg.name}, "# app\n")   # the apply gets logged
    capsys.readouterr()
    assert tr.main(["--root", str(root), "tag-package-only"], now=NOW) == 0
    assert _state(root) == [] and tr.folder_kind(pkg) == "application"
    assert "dropped 1" in capsys.readouterr().out


def test_legacy_kind_markers_are_read_and_folded_into_the_state_file(root, capsys):
    old = _package(root, "example-labs-ai-engineer-agents-202609")
    (old / "KIND").write_text("package-only\n")
    new = _package(root, "alpha-founding-forward-deployed-engineer-202609")
    assert tr.folder_kind(old) == "package-only" and tr.folder_kind(new) == "untagged"
    assert tr.main(["--root", str(root), "tag-package-only"], now=NOW) == 0
    assert _state(root) == ["alpha-founding-forward-deployed-engineer-202609", "example-labs-ai-engineer-agents-202609"]
    assert "legacy KIND marker(s): 1" in capsys.readouterr().out
    assert (old / "KIND").read_text() == "package-only\n"                     # never deleted by the script


def test_folder_kind_untagged_without_marker(root):
    pkg = _package(root, "example-labs-ai-engineer-agents-202609")
    assert tr.folder_kind(pkg) == "untagged"
    tr.main(["--root", str(root), "tag-package-only"], now=NOW)
    assert tr.folder_kind(pkg) == "package-only"


# ----------------------------------------------------------------------------- metrics: counts match
def test_metrics_counts_folders_applications_and_package_only(root):
    _application(root, _job(root))
    _package(root, "example-labs-ai-engineer-agents-202609")
    _package(root, "alpha-founding-forward-deployed-engineer-202609")
    tr.main(["--root", str(root), "tag-package-only"], now=NOW)
    m = tr.metrics(root, NOW)
    assert m["applications"] == 1 and m["package_only"] == 2 and m["folders"] == 3
    assert m["untagged"] == 0
    assert m["applications"] + m["package_only"] + m["untagged"] == m["folders"]


def test_metrics_text_prints_the_folder_line(root, capsys):
    _package(root, "example-labs-ai-engineer-agents-202609")
    tr.main(["--root", str(root), "metrics"], now=NOW)
    assert "folders: 1 (applications 0, package-only 0, untagged 1)" in capsys.readouterr().out


# ----------------------------------------------------------------------------- manual apply on a package folder
def test_manual_apply_adopts_a_package_folder_linked_to_a_known_job(root, capsys):
    job_id = _job(root)
    pkg = _package(root, "acme-ai-engineer-pkg-202609", job_link=f"[[{job_id}]]")
    rc = tr.main(["--root", str(root), "event", "acme-ai-engineer-pkg-202609", "applied", *NO_PDF,
                  "--at", "2026-09-24T10:00:00-07:00", "--evidence", "Owner: submitted on Greenhouse"], now=NOW)
    assert rc == 0
    props, body = fm.load(pkg / "application.md")
    assert props["id"] == "acme-ai-engineer-pkg-202609" and props["job"] == f"[[{job_id}]]"
    assert props["stage"] == "applied"
    types = [e.type for e in tr.parse_events(body)]
    assert types[-1] == "applied" and "Owner: submitted on Greenhouse" in body
    assert "adopted package-only folder" in body
    assert "authored fit" in (pkg / "fit.md").read_text()                      # the package is kept
    assert sorted(p.name for p in (root / "applications").iterdir()) == ["acme-ai-engineer-pkg-202609"]
    assert fa.load_jobs(root)[job_id]["application"] == "[[acme-ai-engineer-pkg-202609]]"
    assert "stage applied" in capsys.readouterr().out


def test_manual_apply_adopts_a_package_folder_with_no_job_record(root):
    pkg = _package(root, "example-labs-ai-engineer-agents-202609")
    rc = tr.main(["--root", str(root), "event", "example-labs-ai-engineer-agents-202609", "applied", *NO_PDF,
                  "--at", "2026-09-24T09:00:00-07:00", "--evidence", "The owner applied by hand"], now=NOW)
    assert rc == 0
    props, _ = fm.load(pkg / "application.md")
    assert props["company"] == "Example Labs" and props["title"] == "AI Engineer - Agents" and props["stage"] == "applied"
    assert (pkg / "outcome.md").is_file()
    assert tr.folder_kind(pkg) == "application"


def test_event_dry_run_on_a_package_folder_writes_nothing(root, capsys):
    pkg = _package(root, "example-labs-ai-engineer-agents-202609")
    rc = tr.main(["--root", str(root), "event", "example-labs-ai-engineer-agents-202609", "applied", *NO_PDF,
                  "--at", "2026-09-24T09:00:00-07:00", "--dry-run"], now=NOW)
    assert rc == 0 and not (pkg / "application.md").exists()
    assert "would adopt" in capsys.readouterr().out


def test_event_on_a_missing_folder_is_a_clear_refusal_not_a_traceback(root, capsys):
    rc = tr.main(["--root", str(root), "event", "no-such-slug", "applied", *NO_PDF, "--evidence", "x"], now=NOW)
    assert rc == 2
    err = capsys.readouterr().err
    assert "no-such-slug" in err and "tracker.py new --job" in err and "Traceback" not in err
