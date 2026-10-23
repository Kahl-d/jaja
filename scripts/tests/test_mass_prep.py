"""mass_prep.py: turn scored mass-tier JOBS into application folders with a JD and a coverage matrix, so the
apply_batch run can use them It creates folders at stage `discovered` only: never `applied`,
never a submit. Jobs whose gate is fail are skipped; existing applications are reported, not re-made."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import mass_prep as mp  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

JD = ("Build LLM agents in Python for enterprise customers. Requirements: 3 years building agents with tool use; "
      "evaluation harnesses; Python and TypeScript. Nice to have: retrieval systems.")


def _job(root, n, gate="pass", title="Applied AI Engineer"):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title=f"{title} {n}", source="greenhouse",
                         source_url=f"https://job-boards.greenhouse.io/acme/jobs/{n}", apply_url=None,
                         ats_vendor="greenhouse", posted_at="2026-09-22T09:00:00Z", location="San Francisco, CA",
                         remote="onsite", comp=(None, None, None), employment_type="full-time", jd_text=JD, now=NOW,
                         req_id=str(n))
    fa.upsert_jobs([rec], root, NOW)
    job = fa.load_jobs(root)[rec["id"]]
    job["gate"] = gate
    fa.save_job(root, job)
    return rec["id"]


def run(root, *args):
    return mp.main(["--root", str(root), "--today", NOW.date().isoformat(), *args], now=NOW)


def test_a_job_becomes_a_discovered_application_with_jd_and_matrix(root, capsys):
    job = _job(root, 1)
    assert run(root, job) == 0
    slug = "acme-applied-ai-engineer-1-202609"
    meta, body = fm.load(root / "applications" / slug / "application.md")
    assert meta["stage"] == "discovered"
    types = [e.type for e in tr.parse_events(body)]
    assert "applied" not in types and "submitted" not in types
    assert (root / "applications" / slug / "jd.md").is_file()
    assert (root / "research" / "targets" / f"{slug}.matrix.yaml").is_file()
    out = capsys.readouterr().out
    assert mp.parse_output(out)["made"] == [slug]                   # the made slugs, one per line, after "made:"
    assert "nothing was applied" in out


def test_a_gate_fail_job_is_skipped(root, capsys):
    job = _job(root, 2, gate="fail")
    assert run(root, job) == 0
    assert not (root / "applications").exists() or not list((root / "applications").iterdir())
    assert "skip" in capsys.readouterr().err


def test_an_existing_application_is_reported_not_remade(root, capsys):
    job = _job(root, 3)
    run(root, job)
    capsys.readouterr()
    assert run(root, job) == 0
    captured = capsys.readouterr()
    assert "exists" in captured.err and "acme-applied-ai-engineer-3-202609" not in captured.out.split("made:")[-1]


def test_an_unknown_job_id_is_reported_and_the_rest_runs(root, capsys):
    job = _job(root, 4)
    assert run(root, "no-such-job", job) == 1
    captured = capsys.readouterr()
    assert "no-such-job" in captured.err and "acme-applied-ai-engineer-4-202609" in captured.out


def test_max_caps_the_number_made(root, capsys):
    jobs = [_job(root, n) for n in (5, 6, 7)]
    run(root, "--max", "2", *jobs)
    assert len(list((root / "applications").iterdir())) == 2


def test_from_next_up_reads_mass_table_rows_and_the_mass_queue_section(root, capsys):
    a, b = _job(root, 8), _job(root, 9)
    nxt = root / "hub" / "NEXT-UP.md"
    nxt.parent.mkdir(parents=True, exist_ok=True)
    nxt.write_text("# NEXT-UP\n\n| # | role | tier |\n|---|---|---|\n"
                   f"| 1 | Acme role (`jobs/{a}.md`) | mass |\n| 2 | Dream role (`jobs/zz-dream.md`) | dream |\n\n"
                   f"## Mass queue\n\n- {b} · Acme role 9\n- not a job id line\n")
    assert run(root, "--from-next-up") == 0
    made = sorted(p.name for p in (root / "applications").iterdir())
    assert made == ["acme-applied-ai-engineer-8-202609", "acme-applied-ai-engineer-9-202609"]


def test_from_next_up_with_prose_only_says_so_and_makes_nothing(root, capsys):
    nxt = root / "hub" / "NEXT-UP.md"
    nxt.parent.mkdir(parents=True, exist_ok=True)
    nxt.write_text("# NEXT-UP\n\n**Scored jobs with no package (JOB), tier mass:** Fabrikam AI Engineer 3 (3.9), Initech MLE.\n")
    assert run(root, "--from-next-up") == 0
    assert not (root / "applications").exists()
    assert "no machine-readable mass rows" in capsys.readouterr().err


# ----------------------------------------------------------------------------- visa checks needed; --dry-run
def test_made_companies_without_a_legal_check_are_listed(root, capsys):
    job = _job(root, 10)
    assert run(root, job) == 0
    parsed = mp.parse_output(capsys.readouterr().out)
    assert parsed["made"] == ["acme-applied-ai-engineer-10-202609"] and parsed["visa_checks_needed"] == ["acme"]


def test_a_company_with_a_legal_check_is_not_listed(root, capsys):
    (root / "legal" / "checks").mkdir(parents=True)
    (root / "legal" / "checks" / "acme.md").write_text("---\nverdict: clear\n---\n")
    job = _job(root, 11)
    run(root, job)
    parsed = mp.parse_output(capsys.readouterr().out)
    assert parsed["made"] and parsed["visa_checks_needed"] == []


def test_dry_run_writes_nothing_and_says_what_it_would_make(root, capsys):
    job = _job(root, 12)
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert run(root, "--dry-run", job) == 0
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before
    out = capsys.readouterr().out
    assert "would make acme-applied-ai-engineer-12-202609" in out
    parsed = mp.parse_output(out)
    assert parsed["made"] == [] and parsed["visa_checks_needed"] == ["acme"]
