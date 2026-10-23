"""check_evidence.py record guards the corpus-keeper relies on (roles/corpus-keeper.md).

facts.yaml `conflicts:` are opened by the corpus-keeper and ruled only by the owner: a ruled entry carries
`ruled: {by: owner, "on": YYYY-MM-DD, basis, ruling}`. evidence.yaml entries carry source, locator,
captured_at and note; the optional `type` is one of file, commit, url, document, owner-statement, and a
owner-statement cites an intake note under profile/_inbox/intake/ that exists. `count` equals the
number of entries. These tests pin the checks on synthetic data.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import JS  # noqa: E402
PROFILE = JS / "profile"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import check_evidence as ce  # noqa: E402

RULED = {"by": "owner", "on": date(2026, 9, 25), "basis": "owner in chat", "ruling": "Founding Engineer."}


def conflict(cid: str = "C01", status: str = "awaiting-owner", **extra) -> dict:
    c = {"id": cid, "topic": "t", "versions": [{"claim": "a", "where": "w", "evidence": ["ev-x-001"]}],
         "proposed_ruling": "p", "status": status}
    c.update(extra)
    return c


# --------------------------------------------------------------------------- conflicts
def test_an_open_and_a_well_ruled_conflict_pass():
    facts = {"conflicts": [conflict("C01"), conflict("C02", "ruled", ruled=dict(RULED))]}
    assert ce.check_conflicts(facts) == []


def test_ruled_needs_the_ruling_block():
    problems = ce.check_conflicts({"conflicts": [conflict("C02", "ruled")]})
    assert len(problems) == 1 and "C02" in problems[0] and "ruled:" in problems[0]


@pytest.mark.parametrize("field,value,needle", [
    ("by", "corpus-keeper", "only the owner rules"),
    ("on", "last week", "not a date"),
    ("basis", "", "basis"),
    ("ruling", " ", "ruling"),
])
def test_ruled_block_fields(field, value, needle):
    block = dict(RULED, **{field: value})
    problems = ce.check_conflicts({"conflicts": [conflict("C03", "ruled", ruled=block)]})
    assert any(needle in p and "C03" in p for p in problems), problems


def test_bare_on_key_is_the_yaml_boolean_trap():
    facts = yaml.safe_load(
        "conflicts:\n"
        "  - id: C04\n    topic: t\n    versions: [{claim: a}]\n    proposed_ruling: p\n    status: ruled\n"
        "    ruled: {by: owner, on: 2026-09-25, basis: chat, ruling: r}\n")
    problems = ce.check_conflicts(facts)
    assert any('quote "on"' in p for p in problems), problems


def test_quoted_on_key_parses_as_a_string_key():
    facts = yaml.safe_load(
        "conflicts:\n"
        "  - id: C04\n    topic: t\n    versions: [{claim: a}]\n    proposed_ruling: p\n    status: ruled\n"
        '    ruled: {by: owner, "on": 2026-09-25, basis: chat, ruling: r}\n')
    assert ce.check_conflicts(facts) == []


def test_ruled_on_accepts_an_iso_string():
    block = dict(RULED, on="2026-09-25")
    assert ce.check_conflicts({"conflicts": [conflict("C05", "ruled", ruled=block)]}) == []


def test_awaiting_conflict_must_not_carry_a_ruling():
    problems = ce.check_conflicts({"conflicts": [conflict("C06", ruled=dict(RULED))]})
    assert any("awaiting-owner" in p and "C06" in p for p in problems)


def test_unknown_status_and_duplicate_or_missing_ids():
    facts = {"conflicts": [conflict("C07", "resolved"), conflict("C08"), conflict("C08"),
                           {"topic": "no id", "versions": [], "status": "awaiting-owner"}]}
    problems = ce.check_conflicts(facts)
    assert any("C07" in p and "resolved" in p for p in problems)
    assert any("duplicate id C08" in p for p in problems)
    assert any("missing id" in p for p in problems)


def test_a_ruled_rule_line_is_checked_too():
    facts = {"rules": [{"rule": "Example demo may be named", "ruled": {"by": "keeper", "on": date(2026, 9, 25),
                                                                   "basis": "chat"}}]}
    problems = ce.check_conflicts(facts)
    assert any("rules[0]" in p and "only the owner rules" in p for p in problems)
    ok = {"rules": [{"rule": "x", "ruled": {"by": "owner", "on": date(2026, 9, 25), "basis": "chat"}}]}
    assert ce.check_conflicts(ok) == []                     # a rule's text is its ruling


# --------------------------------------------------------------------------- condition_met and partial_rulings 
MET = {"by": "owner", "on": date(2026, 9, 25), "basis": "owner in chat; intake note", "words": "We presented it."}
PARTIAL = {"item": "Acme Cloud as the deployment platform", "by": "owner", "on": date(2026, 9, 25),
           "basis": "owner in chat", "ruling": "Cloud: yes, for deployment."}
HELD = dict(RULED, note="held until the talk is confirmed")


def test_a_valid_condition_met_block_passes():
    assert ce.check_conflicts({"conflicts": [conflict("C36", "ruled", ruled=dict(HELD), condition_met=dict(MET))]}) == []


@pytest.mark.parametrize("field,value,needle", [
    ("by", "coordinator", "only the owner rules"),
    ("on", "today", "not a date"),
    ("basis", "", "basis"),
    ("words", " ", "words"),
])
def test_condition_met_fields(field, value, needle):
    block = dict(MET, **{field: value})
    problems = ce.check_conflicts({"conflicts": [conflict("C36", "ruled", ruled=dict(HELD), condition_met=block)]})
    assert any(needle in p and "C36" in p and "condition_met" in p for p in problems), problems


def test_condition_met_needs_a_ruled_conflict_and_a_mapping():
    problems = ce.check_conflicts({"conflicts": [conflict("C36", condition_met=dict(MET))]})
    assert any("C36" in p and "condition_met" in p and "not ruled" in p for p in problems), problems
    problems = ce.check_conflicts({"conflicts": [conflict("C36", "ruled", ruled=dict(HELD), condition_met="yes")]})
    assert any("C36" in p and "condition_met" in p and "mapping" in p for p in problems), problems


def test_condition_met_bare_on_is_the_yaml_boolean_trap():
    facts = yaml.safe_load(
        "conflicts:\n"
        "  - id: C36\n    topic: t\n    versions: [{claim: a}]\n    proposed_ruling: p\n    status: ruled\n"
        '    ruled: {by: owner, "on": 2026-09-25, basis: chat, ruling: r, note: held}\n'
        "    condition_met: {by: owner, on: 2026-09-25, basis: chat, words: w}\n")
    problems = ce.check_conflicts(facts)
    assert any('quote "on"' in p and "condition_met" in p for p in problems), problems


def test_valid_partial_rulings_pass_on_an_open_conflict():
    c = conflict("C09", partial_rulings=[dict(PARTIAL), dict(PARTIAL, item="Widget SDK", ruling="Widget SDK.")])
    assert ce.check_conflicts({"conflicts": [c]}) == []


@pytest.mark.parametrize("field,value,needle", [
    ("by", "keeper", "only the owner rules"),
    ("on", "soon", "not a date"),
    ("basis", "", "basis"),
    ("ruling", "", "ruling"),
    ("item", "", "item"),
])
def test_partial_ruling_fields(field, value, needle):
    c = conflict("C09", partial_rulings=[dict(PARTIAL), dict(PARTIAL, **{field: value})])
    problems = ce.check_conflicts({"conflicts": [c]})
    assert any(needle in p and "C09" in p and "partial_rulings[1]" in p for p in problems), problems
    assert not any("partial_rulings[0]" in p for p in problems), problems


def test_partial_rulings_must_be_a_list_of_mappings():
    problems = ce.check_conflicts({"conflicts": [conflict("C09", partial_rulings=dict(PARTIAL))]})
    assert any("C09" in p and "partial_rulings" in p and "list" in p for p in problems), problems
    problems = ce.check_conflicts({"conflicts": [conflict("C09", partial_rulings=["cloud yes"])]})
    assert any("partial_rulings[0]" in p and "mapping" in p for p in problems), problems


# --------------------------------------------------------------------------- evidence registry
def entry(**extra) -> dict:
    e = {"source": "github.com/example-org/x", "locator": "README", "captured_at": "2026-09-28", "note": "n"}
    e.update(extra)
    return e


def registry(entries: dict, count: int | None = None) -> dict:
    return {"schema_version": 1, "updated": "2026-09-28", "count": len(entries) if count is None else count,
            "evidence": entries}


def test_a_clean_registry_passes(tmp_path):
    reg = registry({"ev-x-001": entry(), "ev-x-002": entry(type="url", discretion="internal-only")})
    assert ce.check_registry(reg, tmp_path) == []


def test_count_must_match(tmp_path):
    problems = ce.check_registry(registry({"ev-x-001": entry()}, count=2), tmp_path)
    assert any("count is 2 but 1" in p for p in problems)


@pytest.mark.parametrize("field", ["source", "locator", "captured_at", "note"])
def test_required_fields(tmp_path, field):
    problems = ce.check_registry(registry({"ev-x-001": entry(**{field: ""})}), tmp_path)
    assert any("ev-x-001" in p and field in p for p in problems)


def test_captured_at_is_a_date(tmp_path):
    problems = ce.check_registry(registry({"ev-x-001": entry(captured_at="yesterday")}), tmp_path)
    assert any("captured_at" in p for p in problems)


def test_type_and_discretion_enums(tmp_path):
    problems = ce.check_registry(registry({"ev-x-001": entry(type="tweet"),
                                           "ev-x-002": entry(discretion="secret")}), tmp_path)
    assert any("ev-x-001" in p and "tweet" in p for p in problems)
    assert any("ev-x-002" in p and "secret" in p for p in problems)


def test_owner_statement_cites_an_existing_intake_note(tmp_path):
    note = tmp_path / "profile" / "_inbox" / "intake" / "2026-09-28-x.md"
    note.parent.mkdir(parents=True)
    note.write_text("Q1 ...\n", encoding="utf-8")
    good = entry(type="owner-statement", source="profile/_inbox/intake/2026-09-28-x.md", locator="Q1")
    assert ce.check_registry(registry({"ev-x-001": good}), tmp_path) == []

    outside = entry(type="owner-statement", source="brain/journal/2026-09-28.md", locator="Q1")
    missing = entry(type="owner-statement", source="profile/_inbox/intake/2026-09-28-nope.md", locator="Q1")
    problems = ce.check_registry(registry({"ev-x-001": outside, "ev-x-002": missing}), tmp_path)
    assert any("ev-x-001" in p and "profile/_inbox/intake/" in p for p in problems)
    assert any("ev-x-002" in p and "does not exist" in p for p in problems)


def test_the_real_registry_is_clean():
    data = yaml.safe_load((PROFILE / "evidence.yaml").read_text(encoding="utf-8"))
    assert ce.check_registry(data, PROFILE.parent) == []


def test_the_real_conflicts_are_well_formed():
    facts = yaml.safe_load((PROFILE / "facts.yaml").read_text(encoding="utf-8"))
    assert ce.check_conflicts(facts) == []


# --------------------------------------------------------------------------- the real facts.yaml (structure only)
@pytest.fixture(scope="module")
def real_facts() -> dict:
    return yaml.safe_load((PROFILE / "facts.yaml").read_text(encoding="utf-8"))


@pytest.mark.real_records
def test_every_real_conflict_is_open_or_ruled_with_a_dated_ruling(real_facts):
    conflicts = real_facts["conflicts"]
    assert conflicts, "the data folder carries conflicts"
    ids = [c["id"] for c in conflicts]
    assert len(ids) == len(set(ids))
    for c in conflicts:
        assert c["status"] in (ce.AWAITING, "ruled"), c["id"]
        if c["status"] == "ruled":
            r = c["ruled"]
            assert r["by"] == ce.OWNER_TAG and str(r["on"])[:4].isdigit(), c["id"]
            assert str(r.get("ruling") or "").strip() and str(r.get("basis") or "").strip(), c["id"]
    assert ce.check_conflicts(real_facts) == []


@pytest.mark.real_records
def test_every_ruled_conflict_with_a_condition_met_block_is_well_formed(real_facts):
    held = [c for c in real_facts["conflicts"] if c.get("condition_met")]
    for c in held:
        met = c["condition_met"]
        assert c["status"] == "ruled" and met["by"] == ce.OWNER_TAG and str(met["words"]).strip(), c["id"]
        assert ce.check_conflicts({"conflicts": [c]}) == []
