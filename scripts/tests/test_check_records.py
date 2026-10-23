"""check_records.py: every department record parses, or the file, line and error are reported.

An agent wrote `equity_type: stock options (ISO or NSO: to confirm)` unquoted in offer.md: a strict YAML loader reads none of that frontmatter. check_records.py
parses the frontmatter of every record type with the kernel frontmatter module and a strict PyYAML loader
(duplicate keys, and flow mappings split by unquoted commas). Fixtures are written per test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import check_records as cr  # noqa: E402

OFFER_BAD = """\
---
company: Lumenloop AI
title: Agent Platform Engineer
level: L4 (definition to confirm)
base: 180000
bonus_target: 10%
equity_type: stock options (ISO or NSO: to confirm)
equity_grant: 40000 options
deadline: 2026-10-19
source: offer email 2026-10-12
---
# Offer
"""
OFFER_GOOD = OFFER_BAD.replace("stock options (ISO or NSO: to confirm)", '"stock options (ISO or NSO: to confirm)"')


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(capsys, *args) -> tuple[int, str]:
    rc = cr.main(list(map(str, args)))
    out = capsys.readouterr()
    return rc, out.out + out.err


# --------------------------------------------------------------------------- one file
def test_the_eval_offer_fails_with_its_file_line_and_error(tmp_path, capsys):
    bad = write(tmp_path, "applications/orbit-ai-agent-platform-engineer-202609/offer.md", OFFER_BAD)
    rc, out = run(capsys, bad)
    assert rc == 1
    assert "offer.md:7:" in out and "mapping values are not allowed here" in out and "quote" in out


def test_the_quoted_offer_passes(tmp_path, capsys):
    good = write(tmp_path, "offer.md", OFFER_GOOD)
    rc, out = run(capsys, good)
    assert rc == 0 and "OK" in out


@pytest.mark.parametrize("text, line, words", [
    ("---\ntitle: x\nstatus: open\nstatus: closed\n---\nbody\n", 4, "duplicate key 'status'"),
    ("---\ntitle: x\n", 1, "never closed"),
    ("---\n- a\n- b\n---\nbody\n", 2, "mapping"),
    ("---\ntitle: [unclosed\n---\n", 3, "YAML"),
])
def test_frontmatter_problems_name_the_line(tmp_path, capsys, text, line, words):
    f = write(tmp_path, "rec.md", text)
    problems = cr.check_file(f)
    assert problems and problems[0].line == line and words in problems[0].message, problems
    assert run(capsys, f)[0] == 1


def test_a_markdown_file_without_frontmatter_is_fine(tmp_path):
    assert cr.check_file(write(tmp_path, "notes.md", "# Notes\n\n---\n\nA rule above, not frontmatter.\n")) == []


def test_yaml_files_are_checked_whole(tmp_path):
    assert cr.check_file(write(tmp_path, "clock.yaml", "employment_end: 2026-10-24\nconfirmed: false\n")) == []
    (p,) = cr.check_file(write(tmp_path, "sources.yaml", "sources:\n  - id: x\n    note: a: b\n"))
    assert p.line == 3 and "mapping values" in p.message


def test_an_explicit_empty_value_is_not_a_problem(tmp_path):
    assert cr.check_file(write(tmp_path, "a.md", "---\nrow: {a: , b: null, c: ~}\nlist: [x, y]\n---\n")) == []


# --------------------------------------------------------------------------- many files
def test_all_walks_every_record_type_and_reports_only_the_broken(tmp_path, capsys):
    root = tmp_path / "job-search"
    write(root, "jobs/acme-1.md", "---\nid: acme-1\nstatus: new\n---\nJD\n")
    write(root, "applications/acme-1/offer.md", OFFER_BAD)
    write(root, "applications/acme-1/application.md", "---\nstage: offer\n---\n## Events\n")
    write(root, "prospects/rae-lin.md", "---\nid: rae-lin\nwarmth: 2\n---\n")
    write(root, "state/offers/comparison.md", "---\ntitle: Offers\n---\n| a | b |\n")
    write(root, "legal/offers/acme-1.md", "---\ntitle: x\ntitle: y\n---\n")
    write(root, "clock.yaml", "employment_end: 2026-10-24\n")
    write(root, "scripts/tests/fixtures/broken.yaml", "a: b: c\n")          # code and fixtures are not records
    write(root, "mcp/tests/x.md", "---\nbad: a: b\n---\n")
    rc, out = run(capsys, "--all", "--root", root)
    assert rc == 1
    assert "applications/acme-1/offer.md:7:" in out and "legal/offers/acme-1.md:3:" in out
    assert "scripts/" not in out and "mcp/" not in out
    assert "2 problems in 2 of 7 files" in out


def test_a_folder_argument_checks_every_record_in_it(tmp_path, capsys):
    write(tmp_path, "applications/a/offer.md", OFFER_BAD)
    write(tmp_path, "applications/a/fit.md", "---\nscore: 4.1\n---\n")
    rc, out = run(capsys, tmp_path / "applications")
    assert rc == 1 and "offer.md:7:" in out and "1 problem in 1 of 2 files" in out


def test_json_lists_each_problem(tmp_path, capsys):
    f = write(tmp_path, "offer.md", OFFER_BAD)
    rc, out = run(capsys, "--json", f)
    data = json.loads(out)
    assert rc == 1 and data["ok"] is False and data["files"] == 1
    (p,) = data["problems"]
    assert (p["line"], p["kind"]) == (7, "yaml") and p["path"].endswith("offer.md")


def test_usage_errors_exit_2(tmp_path, capsys):
    assert run(capsys, tmp_path / "missing.md")[0] == 2
    assert run(capsys)[0] == 2                                  # neither paths nor --all


def test_without_pyyaml_the_kernel_parser_still_runs_and_says_strict_checks_were_skipped(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cr, "yaml", None)
    monkeypatch.setattr(cr.fm, "HAVE_YAML", False)
    good = write(tmp_path, "offer.md", OFFER_GOOD)
    rc, out = run(capsys, good)
    assert rc == 0 and "PyYAML" in out and "skipped" in out
    unclosed = write(tmp_path, "open.md", "---\ntitle: x\n")
    assert run(capsys, unclosed)[0] == 1


# --------------------------------------------------------------------------- flow values split by commas (fix 5)
# profile/skills-taxonomy.yaml writes rows as flow mappings; an unquoted comma inside a value ends it, so
# "SQL (Postgres, SQLite, DuckDB)" parses as skill 'SQL (Postgres' plus keys 'SQLite' and 'DuckDB)'.
TAXONOMY = """\
schema_version: 1
groups:
  languages:
    - {skill: Python, level: expert, evidence: [ev-a-001], lenses: [applied-ml]}
    - {skill: SQL (Postgres, SQLite, DuckDB), level: strong, evidence: [ev-a-002]}
    - {skill: "Quoted (a, b)", level: working, note: "fine, quoted"}
  ml:
    - {skill: QLoRA, RLHF / DPO, INT4 quantization, level: familiar, confirm: true}
"""


def test_a_flow_value_split_by_unquoted_commas_is_named_with_its_fix(tmp_path, capsys):
    f = write(tmp_path, "profile/skills-taxonomy.yaml", TAXONOMY)
    problems = cr.check_file(f)
    assert [(p.line, p.kind) for p in problems] == [(5, "flow-split"), (8, "flow-split")]
    assert "'SQL (Postgres'" in problems[0].message and "'SQLite', 'DuckDB)'" in problems[0].message
    assert 'skill: "SQL (Postgres, SQLite, DuckDB)"' in problems[0].message
    assert 'skill: "QLoRA, RLHF / DPO, INT4 quantization"' in problems[1].message
    rc, out = run(capsys, "--all", "--root", tmp_path)
    assert rc == 1 and "profile/skills-taxonomy.yaml:5:" in out and "2 problems in 1 of 1 file" in out


def test_a_split_value_in_frontmatter_is_caught_too(tmp_path):
    (p,) = cr.check_file(write(tmp_path, "offer.md", "---\ntitle: x\nsalary: {base: 180,000, currency: USD}\n---\n"))
    assert (p.kind, p.line) == ("flow-split", 3) and "'000'" in p.message and 'base: "180,000"' in p.message


def test_a_key_without_a_value_at_the_start_of_a_flow_mapping_is_named(tmp_path):
    (p,) = cr.check_file(write(tmp_path, "a.yaml", "row: {orphan, b: c}\n"))
    assert p.kind == "flow-split" and "'orphan'" in p.message


def test_every_split_row_of_the_real_taxonomy_is_flagged():
    """Read-only on profile/skills-taxonomy.yaml: the rows PyYAML turns into extra null keys are exactly the rows
    check_records flags (both lists are empty once the corpus-keeper quotes the values)."""
    import yaml
    path = cr.JS_ROOT / "profile" / "skills-taxonomy.yaml"
    known = {"skill", "level", "last_used", "evidence", "lenses", "confirm", "note", "discretion"}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    split = sorted(row["skill"] for rows in data["groups"].values() for row in rows
                   if any(k not in known and v is None for k, v in row.items()))
    flagged = [p for p in cr.check_file(path) if p.kind == "flow-split"]
    assert len(flagged) == len(split)
    assert sorted(p.message.split("'")[1] for p in flagged) == split


# --------------------------------------------------------------------------- unquoted YAML booleans in string fields
JOB = """\
---
id: acme-1
company: Acme
company_slug: acme
title: ML Engineer
source: greenhouse
source_url: https://example.com/1
first_seen: 2026-09-28T13:00:00Z
last_seen: 2026-09-28T13:00:00Z
status: fit_checked
sponsorship: yes
remote: "no"
ghost_score: 0.2
tags: []
---
# Acme
"""


def test_an_unquoted_boolean_in_a_job_string_field_is_flagged(tmp_path):
    path = write(tmp_path, "jobs/acme-1.md", JOB)
    (prob,) = cr.check_file(path)
    assert (prob.kind, prob.line) == ("unquoted-bool", 11)
    assert "sponsorship: yes" in prob.message and "True" in prob.message and 'sponsorship: "yes"' in prob.message


def test_the_fix_for_an_enum_field_is_its_own_yes_or_no(tmp_path):
    path = write(tmp_path, "jobs/acme-1.md", JOB.replace("sponsorship: yes", "sponsorship: true"))
    (prob,) = cr.check_file(path)
    assert 'write sponsorship: "yes"' in prob.message


def test_quoted_values_and_non_string_fields_pass(tmp_path):
    path = write(tmp_path, "jobs/acme-1.md", JOB.replace("sponsorship: yes", 'sponsorship: "yes"'))
    assert cr.check_file(path) == []


@pytest.mark.parametrize("token", ["no", "No", "TRUE", "false", "on", "Off"])
def test_every_yaml_boolean_spelling_is_caught(tmp_path, token):
    path = write(tmp_path, "jobs/acme-1.md", JOB.replace("sponsorship: yes", f"employment_type: {token}"))
    assert [p.kind for p in cr.check_file(path)] == ["unquoted-bool"]


def test_an_application_string_field_is_checked_but_its_boolean_field_is_not(tmp_path):
    text = ("---\nid: a\njob: '[[acme-1]]'\ncompany: Acme\ntitle: t\nstage: applied\napplied_via: yes\n"
            "sponsorship_confirmed: true\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n# a\n")
    path = write(tmp_path, "applications/a/application.md", text)
    (prob,) = cr.check_file(path)
    assert prob.kind == "unquoted-bool" and "applied_via" in prob.message


def test_other_records_are_not_checked_for_booleans(tmp_path):
    path = write(tmp_path, "prospects/p.md", "---\nname: P\nreplied: yes\n---\n")
    assert cr.check_file(path) == []


def test_the_cli_fails_on_an_unquoted_boolean(tmp_path, capsys):
    write(tmp_path, "jobs/acme-1.md", JOB)
    assert cr.main(["--all", "--root", str(tmp_path)]) == 1
    assert "jobs/acme-1.md:11:" in capsys.readouterr().out


def test_all_skips_hub_prose_but_checks_the_hub_records(tmp_path, capsys):
    # hub briefs, plans, reviews and the archive are prose with a header, never parsed as records (the
    # archive is append-only); hub/NEXT-UP.md (agency_status) and hub/lanes (crm.lane_claims) are read and stay checked
    root = tmp_path / "job-search"
    for d in ("archive/jsp1", "briefs", "plans", "review"):
        write(root, f"hub/{d}/x.md", "---\nbad: a: b\n---\n")
    write(root, "hub/NEXT-UP.md", "---\nupdated: 2026-10-01 (a: b)\n---\n")
    write(root, "hub/lanes/FOCUSED-X.md", "---\nscope: FOCUSED-X\nclaims: a: b\n---\n")   # crm.lane_claims reads it
    rc, out = run(capsys, "--all", "--root", root)
    assert rc == 1 and "hub/NEXT-UP.md:2:" in out and "hub/lanes/FOCUSED-X.md:3:" in out
    assert not [d for d in ("archive", "briefs", "plans", "review") if f"hub/{d}/" in out]
    assert "2 problems in 2 of 2 files" in out
