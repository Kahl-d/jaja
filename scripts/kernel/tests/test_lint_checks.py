"""lint.py against tmp trees laid out like the repo (the `repo` fixture: real schemas and templates, one card, one skill)."""
import re
from pathlib import Path

import lint
import sync
from roots import Roots

TASK = """---
id: T-2026-0001
title: Valid task
type: task
status: open
owner: owner
priority: P1
due: 2026-10-01
created: 2026-09-20
updated: 2026-09-20
---
# Valid task
"""

COMPANY = "---\nslug: acme\nname: Acme\nlast_researched: 2026-09-20\n---\n"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def codes(findings):
    return sorted(f.code for f in findings)


def synced(repo: Path) -> Path:
    sync.main(repo)
    return repo


def test_clean_tree_has_no_findings(repo: Path):
    write(repo, "work/tasks/T-2026-0001-valid.md", TASK)
    synced(repo)
    assert lint.main(repo) == 0


def test_task_missing_owner_is_one_finding(repo: Path):
    synced(repo)
    write(repo, "work/tasks/T-2026-0001-valid.md", TASK.replace("owner: owner\n", ""))
    findings = lint.run(repo)
    assert len(findings) == 1 and findings[0].code == "schema-missing" and "owner" in findings[0].message
    assert lint.main(repo) == 1


def test_enum_and_date_violations(repo: Path):
    synced(repo)
    write(repo, "work/tasks/T-2026-0001-valid.md", TASK.replace("status: open", "status: someday").replace("due: 2026-10-01", "due: next week"))
    assert codes(lint.run(repo)) == ["schema-date", "schema-enum"]


def test_records_are_read_from_the_root_their_type_lives_in(tmp_path: Path, repo: Path):
    """Cards from the code root, approvals and tasks from the runtime root, everything else from the data folder."""
    data, runtime = tmp_path / "data", tmp_path / "runtime"
    write(data, "companies/acme.md", COMPANY.replace("name: Acme\n", ""))
    write(runtime, "work/tasks/T-2026-0001-valid.md", TASK.replace("owner: owner\n", ""))
    write(repo, "agents/bad.md", "---\nname: bad\n---\n")
    roots = Roots(repo, data, runtime)
    found = {f.path for f in lint.check_schemas(roots)}
    assert found == {"companies/acme.md", "work/tasks/T-2026-0001-valid.md", "agents/bad.md"}


def test_broken_wikilink_is_one_finding(repo: Path):
    synced(repo)
    write(repo, "notes/identity.md", "---\ntitle: Identity\n---\nWho.\n")
    write(repo, "notes/goals.md", "---\ntitle: Goals\n---\nSee [[identity]] and [[nonexistent-page]] and [[identity|alias]].\n")
    findings = lint.run(repo)
    assert len(findings) == 1 and findings[0].code == "broken-link" and "nonexistent-page" in findings[0].message


def test_wikilink_with_a_file_extension_resolves(repo: Path):
    synced(repo)
    write(repo, "views/prospects.base", "views: []\n")
    write(repo, "notes/goals.md", "---\ntitle: Goals\n---\nSee [[prospects.base]] and [[missing.base]].\n")
    findings = lint.run(repo)
    assert [f.code for f in findings] == ["broken-link"] and "missing.base" in findings[0].message


def test_a_path_qualified_link_resolves_only_to_that_path(repo: Path):
    synced(repo)
    write(repo, "companies/acme.md", COMPANY)
    write(repo, "applications/acme-ml/application.md", "---\nid: acme-ml\n---\n")
    write(repo, "notes/goals.md",
          "[[companies/acme|acme]] [[applications/acme-ml/application|acme-ml]] [[jobs/acme|wrong folder]] [[acme-ml/application]]\n")
    broken = [f.message for f in lint.check_links(repo)]
    assert len(broken) == 1 and "jobs/acme" in broken[0]        # the basename exists elsewhere: still broken; a tail like [[acme-ml/application]] resolves


def test_links_field_must_be_a_list_of_wikilinks(repo: Path):
    base = "---\nid: O-2026-0001\nchannel: email\nstatus: drafted\ncreated: 2026-09-26\n"
    write(repo, "outbox/O-2026-0001.md", base + "links:\n- '[[a/b|b]]'\n---\nx\n")
    assert [f for f in lint.check_schemas(repo) if f.code == "schema-links"] == []
    write(repo, "outbox/O-2026-0001.md", base + "links: '[[a/b]]'\n---\nx\n")
    assert codes(f for f in lint.check_schemas(repo) if f.code == "schema-links") == ["schema-links"]
    write(repo, "outbox/O-2026-0001.md", base + "links:\n- plain text\n---\nx\n")
    assert codes(f for f in lint.check_schemas(repo) if f.code == "schema-links") == ["schema-links"]


def test_long_skill_description_is_one_finding(repo: Path):
    write(repo, "skills/brief/SKILL.md", f"---\nname: brief\ndescription: {'x' * 400}\n---\n# Brief\n")
    synced(repo)
    findings = lint.run(repo)
    assert len(findings) == 1 and findings[0].code == "skill-description-length"
    write(repo, "skills/brief/SKILL.md", "---\nname: other\ndescription: ok\n---\n# Brief\n")
    synced(repo)
    assert [f.code for f in lint.run(repo)] == ["skill-name-mismatch"]


def test_agents_md_line_budget(repo: Path):
    synced(repo)
    write(repo, "AGENTS.md", "line\n" * 201)
    assert [f.code for f in lint.run(repo)] == ["agents-md-lines"]


def test_generated_drift(repo: Path):
    assert sorted({f.code for f in lint.run(repo)}) == ["generated-missing"]
    synced(repo)
    assert lint.run(repo) == []
    agent = repo / ".claude" / "agents" / "researcher.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "edited by hand\n", encoding="utf-8")
    findings = lint.run(repo)
    assert [f.code for f in findings] == ["generated-edited"] and ".claude/agents/researcher.md" in findings[0].path


def test_frontmatter_parse_error_is_reported(repo: Path):
    synced(repo)
    write(repo, "work/tasks/T-2026-0002-bad.md", "---\nid: [oops\n---\n")
    assert [f.code for f in lint.run(repo)] == ["frontmatter-error"]


def test_fixture_directories_are_not_linted(repo: Path):
    synced(repo)
    write(repo, "evals/role/fixtures/case-01/fit.md", "---\ntitle: Fit\n---\nSee [[made-up-company-3310]].\n")
    assert lint.run(repo) == []


def test_routines_are_schema_checked(repo: Path):
    synced(repo)
    write(repo, "routines/bad.yaml", "name: bad\npurpose: Missing most fields.\n")
    findings = lint.run(repo)
    assert findings and all(f.path == "routines/bad.yaml" for f in findings)


def test_a_model_routine_enabled_needs_enabled_by(repo: Path):
    synced(repo)
    base = ("name: r\npurpose: p\nruns_on: desktop\nengine: claude\nschedule: '0 7 * * *'\naction: skill:brief\ntier: T1\n"
            "owner: owner\nmodel: sonnet\n")
    write(repo, "routines/r.yaml", base + "enabled: true\n")
    assert [f.code for f in lint.run(repo)] == ["routine-enabled"]
    write(repo, "routines/r.yaml", base + "enabled: true\nenabled_by: A-2026-0001\n")
    assert lint.run(repo) == []
    write(repo, "routines/r.yaml", base.replace("model: sonnet\n", "") + "enabled: false\n")
    assert [f.code for f in lint.run(repo)] == ["routine-model"]


def test_task_hard_deadline_without_derived_from_is_a_finding(repo: Path):
    synced(repo)
    path = write(repo, "work/tasks/T-2026-0001-valid.md", TASK.replace("due: 2026-10-01\n", "due: 2026-10-01\nhard_deadline: 2026-10-03\n"))
    findings = lint.run(repo)
    assert [f.code for f in findings] == ["schema-rule"] and "derived_from" in findings[0].message
    path.write_text(path.read_text(encoding="utf-8").replace("hard_deadline: 2026-10-03\n", 'hard_deadline: 2026-10-03\nderived_from: "EAD + 6 months"\n'), encoding="utf-8")
    assert lint.run(repo) == []


def test_task_id_owner_and_tier_are_checked(repo: Path):
    synced(repo)
    bad = TASK.replace("id: T-2026-0001", "id: T-26-1").replace("owner: owner", "owner: bob").replace("priority: P1\n", "priority: P1\ntier: T9\n")
    write(repo, "work/tasks/T-2026-0001-valid.md", bad)
    assert codes(lint.run(repo)) == ["schema-enum", "schema-pattern", "schema-pattern"]
    write(repo, "work/tasks/T-2026-0001-valid.md", TASK.replace("owner: owner", "owner: role:legal-visa").replace("priority: P1\n", "priority: P1\ntier: T3\n"))
    assert lint.run(repo) == []


ROLE_CARD = ("---\nname: sender\ntitle: Sender\ndepartment: agency\npurpose: Sends approved items.\n{tools}"
             "tier_max: T3\nmodel: haiku\nstatus: probation\n---\n# Sender\n")


def test_role_with_empty_tools_allow_is_a_finding(repo: Path):
    card = write(repo, "agents/sender.md", ROLE_CARD.format(tools="tools_allow: []\n"))
    findings = [f for f in lint.run(repo) if f.path == "agents/sender.md"]
    assert [f.code for f in findings] == ["role-tools-empty"]
    card.write_text(ROLE_CARD.format(tools=""), encoding="utf-8")        # missing: the schema already requires it
    assert [f.code for f in lint.run(repo) if f.path == "agents/sender.md"] == ["schema-missing"]
    card.write_text(ROLE_CARD.format(tools="tools_allow: [Read, Edit, Bash]\n"), encoding="utf-8")
    synced(repo)
    assert lint.run(repo) == []


# ------------------------------------------------------------------------------ tag vocabulary
def pin_tags(repo: Path, enforcement: str) -> None:
    vocab = repo / "schemas" / "tags.yaml"
    vocab.write_text(re.sub(r"(?m)^enforcement: \w+", f"enforcement: {enforcement}", vocab.read_text()))


def test_unknown_record_tag_is_a_warning_while_enforcement_is_warning(repo: Path):
    pin_tags(repo, "warning")
    synced(repo)
    write(repo, "companies/acme.md", COMPANY.replace("---\n", "---\ntags: [job-search, made-up]\n", 1))
    assert lint.run(repo) == []
    warn = lint.warnings(repo)
    assert [w.code for w in warn] == ["tag-unknown"] and "made-up" in warn[0].message


def test_unknown_record_tag_fails_the_run_once_enforcement_is_error(repo: Path):
    pin_tags(repo, "error")
    synced(repo)
    write(repo, "companies/acme.md", COMPANY.replace("---\n", "---\ntags: [job-search, made-up]\n", 1))
    assert [f.code for f in lint.run(repo)] == ["tag-unknown"]
    write(repo, "companies/acme.md", COMPANY.replace("---\n", "---\ntags: [job-search, domain/career, project/job-search, referrer]\n", 1))
    assert lint.run(repo) == []


def test_tags_on_free_notes_are_not_checked(repo: Path):
    pin_tags(repo, "error")
    synced(repo)
    write(repo, "research/notes.md", "---\ntags: [anything-goes]\n---\nA note.\n")
    assert lint.run(repo) == []


def test_tag_vocabulary_reads_the_code_root_schema(repo: Path):
    vocab = lint.load_tag_vocabulary(repo)
    assert "job-search" in vocab["allowed"] and lint.tag_known("project/job-search", vocab)
    assert lint.load_tag_vocabulary(repo / "empty-folder")["enforcement"] == "off"


# ------------------------------------------------------------------------------ rulings registry
def rulings(repo: Path, body: str) -> None:
    write(repo, "hub/rulings.yaml", "rulings:\n" + body)


def test_no_rulings_registry_is_fine(repo: Path):
    synced(repo)
    assert lint.check_rulings(repo) == []


def test_ruling_ids_must_be_well_formed_and_unique(repo: Path):
    rulings(repo, "- {id: R-1, date: 2026-01-02, status: standing}\n- {id: R-1, status: standing}\n"
                  "- {id: bad id, status: standing}\n- {id: R-3, date: soon, status: someday}\n- {id: R-4a, status: history}\n")
    msgs = [f.message for f in lint.check_rulings(repo)]
    assert any("duplicate" in m for m in msgs) and any("'bad id'" in m for m in msgs)
    assert any("not an ISO date" in m for m in msgs) and any("status 'someday'" in m for m in msgs)
    assert not any("R-4a" in m for m in msgs)


def test_ruling_scope_and_citations_are_warnings(repo: Path):
    write(repo, "lanes/hunt/AGENT.md", "x")
    write(repo, "agents/researcher.md", (repo / "agents" / "researcher.md").read_text(encoding="utf-8") + "\nSee R-1 and R-77.\n")
    rulings(repo, "- {id: R-1, status: standing, scope: {lanes: [hunt, ghost-lane, all]}}\n")
    synced(repo)
    assert lint.check_rulings(repo) == []
    warn = lint.warnings(repo)
    assert sorted(w.code for w in warn) == ["ruling-cited", "ruling-scope"]
    assert any("R-77" in w.message and "R-1" not in w.message.replace("R-1 ", "") or "R-77" in w.message for w in warn)
    assert any("ghost-lane" in w.message for w in warn)


def test_a_programme_lane_is_a_known_ruling_scope(repo: Path):
    write(repo, "hub/programme.json", '{"programme": "p", "lanes": [{"scope": "SETUP"}]}')
    rulings(repo, "- {id: R-1, status: standing, scope: {lanes: [setup]}}\n")
    assert lint.check_ruling_scopes(repo) == []
