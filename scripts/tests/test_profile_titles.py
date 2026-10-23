"""profile_titles.py and the facts.yaml guards (render titles, corpus links).

`title` in profile/facts.yaml is the record and may carry held variants and conflict notes;
`title_render` is the conservative form PROFILE.md uses. These tests pin the helper, then check the
real facts.yaml: every rendered title is clean of held text and conflict markers, matches PROFILE.md,
and every `corpus:` link resolves.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import profile_titles as pt  # noqa: E402
import held_safe as hs  # noqa: E402  (tests/: compare real holds without printing a private one)

from layout import JS  # noqa: E402
PROFILE = JS / "profile"
if str(PROFILE) not in sys.path:
    sys.path.insert(0, str(PROFILE))
import check_evidence as ce  # noqa: E402


@pytest.fixture(scope="module")
def held() -> list[str]:
    return pt.load_held(PROFILE / "PROFILE-HOLDS.md")


@pytest.fixture(scope="module")
def facts() -> dict:
    return yaml.safe_load((PROFILE / "facts.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the helper
@pytest.mark.real_records
def test_held_strings_come_from_every_holds_table(held):
    assert len(held) > 0, "the real holds list is empty"      # never print the list: it may include private holds
    short = sum(1 for s in held if len(s) < 2)
    assert short == 0, f"{short} held string(s) under two characters"


def test_render_title_prefers_title_render():
    entry = {"title": "Data Analyst (LinkedIn and PhD CVs say 'Data Associate' - C15)", "title_render": "Data Analyst"}
    assert pt.render_title(entry, held=["Data Associate"]) == "Data Analyst"


def test_render_title_falls_back_to_a_clean_title():
    assert pt.render_title({"title": "Founding Engineer"}, held=[]) == "Founding Engineer"


@pytest.mark.parametrize("raw", [
    "Lab Student Assistant - proposed",
    "Product Development Team (internship)",
    "Teaching Assistant or Lab Student Assistant",
    "NLP Researcher - see C05",
    "Data Analyst C15",
])
def test_render_title_refuses_a_raw_title_with_conflict_markers(raw):
    with pytest.raises(pt.UnrenderableTitle):
        pt.render_title({"title": raw}, held=[])


def test_render_title_refuses_held_text_even_in_title_render():
    with pytest.raises(pt.UnrenderableTitle):
        pt.render_title({"title": "x", "title_render": "Healthcare Data Associate"}, held=["Data Associate"])
    with pytest.raises(pt.UnrenderableTitle):
        pt.render_title({"title": "x", "title_render": "NLP Researcher (see C05)"}, held=[])


def test_title_render_may_keep_a_descriptive_parenthesis():
    assert pt.title_problems("Resident Assistant (Student Leader)", held=[]) == []


def test_render_view_swaps_in_the_render_title_and_refuses_the_rest():
    node = {"employment": [
        {"id": "a", "title": "Data Analyst (says 'Data Associate' - C15)", "title_render": "Data Analyst"},
        {"id": "b", "title": "Founding Engineer"},
        {"id": "c", "title": "Lab Student Assistant - proposed; see C18"},
    ]}
    a, b, c = pt.render_view(node, held=["Data Associate"])["employment"]
    assert a == {"id": "a", "title": "Data Analyst", "title_record": node["employment"][0]["title"]}
    assert b == {"id": "b", "title": "Founding Engineer"}
    assert c["title"] is None and c["title_record"].endswith("see C18") and "proposed" in c["title_flag"]
    assert node["employment"][0]["title_render"] == "Data Analyst"          # input untouched


# --------------------------------------------------------------------------- the real facts.yaml
def test_every_rendered_facts_entry_has_a_title_render(facts):
    missing = [path for path, entry in pt.iter_titled(facts)
               if entry.get("discretion") != "internal-only" and "title_render" not in entry]
    assert missing == []


def test_no_title_render_carries_held_text_or_a_conflict_marker(facts, held):
    bad = {path: pt.title_problems(entry["title_render"], held)
           for path, entry in pt.iter_titled(facts) if "title_render" in entry}
    lines = [f"{k}: {hs.scrub('; '.join(v))}" for k, v in bad.items() if v]
    ok = not lines
    assert ok, "\n".join(lines)


@pytest.mark.real_records
def test_title_renders_match_the_profile_wording(facts):
    """Every rendered title equals PROFILE.md, except a new entry the corpus-keeper marked
    `profile_pending` (title_render proposed; profile-editor has not written it yet)."""
    text = (PROFILE / "PROFILE.md").read_text(encoding="utf-8")
    for entry in facts["employment"]:
        if not entry.get("profile_pending"):
            assert f"### {entry['title_render']} · " in text, entry["id"]
    thesis = facts["education"][0]["thesis"]
    assert f"Thesis: {thesis['title_render']} (" in text
    for demo in facts["presentations_and_demos"]:
        if "title_render" in demo and not demo.get("profile_pending"):
            r = demo["title_render"]
            assert r in text or (r[0].lower() + r[1:]) in text, demo["id"]


def test_profile_pending_names_its_handoff(facts):
    """`profile_pending: "<date> <issues.md id>: ..."` says when and where the title went to profile-editor."""
    for section in ("employment", "presentations_and_demos"):
        for entry in facts.get(section) or []:
            if "profile_pending" in entry:
                assert re.match(r"^\d{4}-\d{2}-\d{2} I-\d{3}\b", str(entry["profile_pending"])), entry["id"]
                assert "title_render" in entry, entry["id"]


@pytest.mark.real_records
def test_the_records_keep_their_raw_titles(facts):
    """A record title that names a conflict id points at a conflict that exists in the same facts file."""
    status = {c["id"]: c["status"] for c in facts["conflicts"]}
    for entry in facts["employment"]:
        for cid in re.findall(r"\bC\d{2}\b", entry["title"]):
            assert cid in status, (entry["id"], cid)


def test_check_facts_flags_held_render_titles_and_missing_ones(tmp_path):
    (tmp_path / "corpus" / "x").mkdir(parents=True)
    (tmp_path / "corpus" / "x" / "README.md").write_text("x")
    facts = {"employment": [
        {"id": "e1", "title": "Data Analyst - C15", "title_render": "Data Associate", "corpus": "corpus/x/README.md"},
        {"id": "e2", "title": "Founding Engineer"},
        {"id": "e3", "title": "Hidden talk (2026)", "discretion": "internal-only"},
    ]}
    problems = ce.check_facts(facts, ["Data Associate"], tmp_path)
    assert any("employment[0]" in p and "Data Associate" in p for p in problems)
    assert any("employment[1]" in p and "title_render" in p for p in problems)
    assert not any("employment[2]" in p for p in problems)


# --------------------------------------------------------------------------- corpus links
@pytest.mark.real_records
def test_every_corpus_link_resolves_to_an_existing_corpus_directory(facts):
    links = list(pt.iter_corpus_links(facts))
    assert len(links) >= 22          # 12 employment and education + 5 awards + 4 publications + 1 talk
    for path, rel in links:
        target = PROFILE / rel
        assert re.match(r"^corpus/[a-z0-9-]+/", rel), (path, rel)
        assert target.parent.is_dir() and target.exists(), (path, rel)


@pytest.mark.real_records
def test_awards_publications_and_the_talk_link_their_corpus(facts):
    assert {a["corpus"] for a in facts["awards"]} == {"corpus/awards/README.md"}
    assert {p["corpus"] for p in facts["publications"]} == {"corpus/publications/README.md"}
    talk = next(t for t in facts["presentations_and_demos"] if t["id"] == "talk-two-agents-one-branch")
    assert talk["corpus"] == "corpus/office-space/README.md"


def test_check_facts_flags_a_dangling_corpus_link(tmp_path):
    facts = {"awards": [{"id": "a1", "corpus": "corpus/nope/README.md"}]}
    assert any("corpus/nope/README.md" in p for p in ce.check_facts(facts, [], tmp_path))


def test_check_facts_is_clean_on_the_real_profile(facts, held):
    problems = ce.check_facts(facts, held, PROFILE)
    ok = not problems
    assert ok, hs.scrub("\n".join(map(str, problems)))
