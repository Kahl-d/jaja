"""pursuits.py: one record per direction into a company written only by this script."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pursuits as pu  # noqa: E402
from js_common import fm  # noqa: E402

APP1 = "acme-ai-engineer-202609"
APP2 = "acme-data-scientist-202609"
SLUG = "acme-via-sam-referrer-202609"
DAY = "2026-09-27"


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def js(tmp_path: Path) -> Path:
    root = tmp_path / "js"
    w(root / "companies" / "acme.md", '---\ntype: company\nslug: acme\nname: "Acme Corp"\n---\n')
    w(root / "prospects" / "sam-referrer.md", "---\nid: sam-referrer\nname: Sam Referrer\ncompany_slug: acme\n---\n")
    w(root / "prospects" / "ira-introducer.md", "---\nid: ira-introducer\nname: Ira Introducer\n---\n")
    for a in (APP1, APP2):
        w(root / "applications" / a / "application.md", f"---\nid: {a}\ncompany_slug: acme\nstage: fit_checked\n---\n")
    w(root / "research" / "targets" / "case-sam-referrer-202609.md", "---\ntype: research\n---\ncase\n")
    w(root / "hub" / "intake" / "2026-09-27-sam-referrer.md", "intake\n")
    (root / "resumes" / "variants" / "acme-referral-master-202609").mkdir(parents=True)
    return root


def run(js: Path, *argv: str) -> int:
    return pu.main([argv[0], "--root", str(js), "--today", DAY, *argv[1:]])


def new_acme(js: Path, *extra: str) -> int:
    return run(js, "new", "--company", "Acme Corp", "--kind", "referral", "--referrer", "sam-referrer",
               "--introducer", "ira-introducer", "--case", "research/targets/case-sam-referrer-202609.md",
               "--intake", "hub/intake/2026-09-27-sam-referrer.md", "--application", APP1, "--application", APP2,
               "--master-variant", "acme-referral-master-202609", "--waiting-on", "owner",
               "--next-action", "Alex reviews the finished bundle (R-6)", "--next-action-at", "2026-09-28", *extra)


def load(js: Path, slug: str = SLUG) -> tuple[dict, str]:
    return fm.load(js / "pursuits" / slug / "pursuit.md")


# ------------------------------------------------------------------------------------------------ new
def test_new_names_the_direction_and_holds_the_canonical_company(js):
    assert new_acme(js) == 0
    meta, body = load(js)
    assert meta["type"] == "pursuit" and meta["id"] == SLUG
    assert meta["title"] == "Acme Corp via Sam Referrer, introduced by Ira Introducer"
    assert (meta["company"], meta["company_slug"], meta["kind"]) == ("Acme Corp", "acme", "referral")
    assert (meta["referrer"], meta["introducer"], meta["contacts"]) == ("sam-referrer", "ira-introducer", [])
    assert meta["applications"] == [APP1, APP2] and meta["master_variant"] == "acme-referral-master-202609"
    assert (meta["stage"], meta["waiting_on"], str(meta["next_action_at"])) == ("intake", "owner", "2026-09-28")
    assert str(meta["created"]) == DAY == str(meta["updated"])
    assert body.index("## Strategy") < body.index("## Decisions") < body.index("## Log")
    assert f"- {DAY} · opened at stage intake" in body


def test_new_stamps_graph_links_to_the_records_it_names(js):
    new_acme(js)
    links = " ".join(load(js)[0]["links"])
    for target in ("companies/acme|", "prospects/sam-referrer|", "prospects/ira-introducer|",
                   f"applications/{APP1}/application|", "research/targets/case-sam-referrer-202609|"):
        assert target in links, target


def test_two_directions_into_one_company_are_two_pursuits(js):
    assert new_acme(js) == 0
    assert run(js, "new", "--company", "acme", "--kind", "direct", "--waiting-on", "nobody") == 0
    assert (js / "pursuits" / "acme-direct-202609" / "pursuit.md").is_file()
    assert new_acme(js) == 2                                               # the same direction twice: refused


@pytest.mark.parametrize("extra,why", [
    (["--application", "no-such-app"], "applications/no-such-app/application.md"),
    (["--case", "research/targets/missing.md"], "research/targets/missing.md"),
    (["--master-variant", "nope"], "resumes/variants/nope"),
    (["--stage", "won"], "stage"),
    (["--waiting-on", "somebody"], "waiting_on"),
    (["--waiting-on", "contact:lee-eng"], "contact:lee-eng"),
    (["--next-action-at", "tomorrow"], "next_action_at"),
])
def test_new_refuses_what_it_cannot_verify_and_writes_nothing(js, capsys, extra, why):
    assert new_acme(js, *extra) == 2
    assert why in capsys.readouterr().err
    assert not (js / "pursuits").exists()


def test_a_person_without_a_record_is_kept_and_named_missing(js, capsys):
    assert run(js, "new", "--company", "acme", "--kind", "referral", "--referrer", "jo-park",
               "--waiting-on", "role:prospector") == 0
    assert "missing: prospects/jo-park.md" in capsys.readouterr().out
    assert load(js, "acme-via-jo-park-202609")[0]["referrer"] == "jo-park"


# ------------------------------------------------------------------------------------------------ set
def test_set_moves_the_stage_and_logs_every_change(js):
    new_acme(js)
    assert run(js, "set", SLUG, "--stage", "review", "--waiting-on", "lane:resume",
               "--next-action", "RESUME answers the panel", "--next-action-at", "2026-09-29") == 0
    meta, body = load(js)
    assert (meta["stage"], meta["waiting_on"], meta["next_action"]) == ("review", "lane:resume", "RESUME answers the panel")
    assert f"- {DAY} · stage intake -> review" in body
    assert f"- {DAY} · waiting_on owner -> lane:resume" in body


def test_set_adds_and_removes_contacts_and_applications(js):
    new_acme(js)
    w(js / "prospects" / "lee-eng.md", "---\nid: lee-eng\nname: Lee Eng\n---\n")
    assert run(js, "set", SLUG, "--add-contact", "lee-eng", "--remove-application", APP2) == 0
    meta, _ = load(js)
    assert meta["contacts"] == ["lee-eng"] and meta["applications"] == [APP1]


def test_closing_needs_a_reason_and_clears_the_next_action(js, capsys):
    new_acme(js)
    assert run(js, "set", SLUG, "--stage", "closed-lost") == 2
    assert "closed_reason" in capsys.readouterr().err
    assert run(js, "set", SLUG, "--stage", "closed-lost", "--reason", "role filled internally") == 0
    meta, _ = load(js)
    assert (meta["stage"], meta["closed_reason"], meta["waiting_on"], meta["next_action"]) == \
        ("closed-lost", "role filled internally", "nobody", None)


def test_a_closed_or_paused_pursuit_moves_only_through_reopen(js, capsys):
    new_acme(js)
    run(js, "set", SLUG, "--stage", "paused")
    assert run(js, "set", SLUG, "--stage", "drafted") == 2
    assert "reopen" in capsys.readouterr().err
    assert run(js, "reopen", SLUG, "--stage", "drafted", "--ruling", "R-6") == 2      # no reason
    assert run(js, "reopen", SLUG, "--stage", "drafted", "--reason", "Sam replied",
               "--quote", "pick it back up", "--waiting-on", "owner") == 0
    meta, body = load(js)
    assert meta["stage"] == "drafted" and meta.get("closed_reason") is None
    assert f'- {DAY} · reopened from paused to drafted: Sam replied · Alex: "pick it back up"' in body


# ------------------------------------------------------------------------------------------------ decisions
def test_a_decision_cites_a_ruling_or_alexs_words_and_is_append_only(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "one master resume to Sam") == 2
    assert "--ruling" in capsys.readouterr().err
    assert run(js, "decision", SLUG, "--text", "one master resume to Sam", "--ruling", "R7") == 2   # malformed id
    assert run(js, "decision", SLUG, "--text", "one master resume to Sam", "--ruling", "R-7") == 0
    first = load(js)[1]
    assert run(js, "decision", SLUG, "--text", "take the recommended decisions",
               "--quote", "take the recommended decisions that we do") == 0
    body = load(js)[1]
    decisions = body.split("## Decisions", 1)[1].split("\n## ", 1)[0]
    assert decisions.strip().splitlines() == [
        f"- {DAY} · R-7 · one master resume to Sam",
        f'- {DAY} · Alex: "take the recommended decisions that we do" · take the recommended decisions']
    assert body.startswith(first.split("## Decisions", 1)[0])



# a quote names its speaker; the owner is the default and anyone else must be on the pursuit
def test_a_decision_quote_names_its_speaker_who_is_alex_by_default(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "two referrers", "--ruling", "R-233",
               "--quote", "it won't hurt to have multiple people doing referrals", "--speaker", "sam-referrer") == 0
    assert run(js, "decision", SLUG, "--text", "one master", "--quote", "one master resume") == 0
    decisions = load(js)[1].split("## Decisions", 1)[1].split("\n## ", 1)[0].strip().splitlines()
    assert decisions == [
        f'- {DAY} · R-233 · Sam Referrer: "it won\'t hurt to have multiple people doing referrals" · two referrers',
        f'- {DAY} · Alex: "one master resume" · one master']


@pytest.mark.parametrize("argv, why", [
    (("--quote", "q", "--speaker", "sam-referrer"), "ruling id"),          # someone else's words are not the owner's yes
    (("--quote", "q", "--speaker", "nobody-here", "--ruling", "R-1"), "not on this pursuit"),
    (("--ruling", "R-1", "--speaker", "sam-referrer"), "--speaker names who said a --quote"),
])
def test_a_speaker_is_on_the_pursuit_and_never_stands_in_for_alex(js, capsys, argv, why):
    new_acme(js)
    before = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "decision", SLUG, "--text", "t", *argv) == 2
    assert why in capsys.readouterr().err
    assert (js / "pursuits" / SLUG / "pursuit.md").read_bytes() == before


def test_a_correction_is_appended_cited_and_leaves_the_original_line(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "multi-referral", "--quote", "it won't hurt to have more referrers") == 0
    original = f'- {DAY} · Alex: "it won\'t hurt to have more referrers" · multi-referral'
    assert run(js, "correct", SLUG, "--of", "it won't hurt", "--speaker", "sam-referrer",
               "--reason", "those were Sam's words", "--ruling", "R-233", "--today", "2026-09-28") == 0
    decisions = load(js)[1].split("## Decisions", 1)[1].split("\n## ", 1)[0].strip().splitlines()
    assert decisions == [original, (
        f"- 2026-09-28 · correction of decision 1 ({DAY}) · R-233 · those were Sam's words · now reads: "
        f'R-233 · Sam Referrer: "it won\'t hurt to have more referrers" · multi-referral')]
    assert "correction recorded" in capsys.readouterr().out


def test_a_second_correction_builds_on_the_first_never_on_the_original(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "multi", "--quote", "more referrers") == 0
    assert run(js, "correct", SLUG, "--of", "more referrers", "--speaker", "sam-referrer", "--reason", "his words",
               "--ruling", "R-233") == 0
    assert run(js, "correct", SLUG, "--of", "more referrers", "--text", "multi, two referrers", "--reason", "r",
               "--ruling", "R-233") == 0
    last = load(js)[1].split("## Decisions", 1)[1].split("\n## ", 1)[0].strip().splitlines()[-1]
    assert last == (f"- {DAY} · correction of decision 1 ({DAY}) · R-233 · r · now reads: "
                    'R-233 · Sam Referrer: "more referrers" · multi, two referrers')
    # the current state is Sam: naming him again changes nothing, and --of finds the decision by its new text
    assert run(js, "correct", SLUG, "--of", "two referrers", "--speaker", "sam-referrer", "--reason", "r",
               "--ruling", "R-1") == 2
    assert "changes nothing" in capsys.readouterr().err
    assert run(js, "correct", SLUG, "--of", "correction", "--text", "x", "--reason", "r", "--ruling", "R-233") == 2
    assert "matches no" in capsys.readouterr().err


def test_a_correction_to_another_speaker_cites_a_ruling_that_the_decision_keeps(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "multi", "--quote", "more referrers") == 0
    before = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "correct", SLUG, "--of", "more referrers", "--speaker", "sam-referrer", "--reason", "his words",
               "--quote", "that was Sam") == 2
    assert "--ruling" in capsys.readouterr().err
    assert (js / "pursuits" / SLUG / "pursuit.md").read_bytes() == before


def test_a_correction_can_replace_the_text_and_keeps_a_ruling(js):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "one master resume to Sam", "--ruling", "R-7") == 0
    assert run(js, "correct", SLUG, "--of", "one master resume", "--text", "one master resume to Sam and Ira",
               "--reason", "both refer", "--quote", "send it to both") == 0
    last = load(js)[1].split("## Decisions", 1)[1].split("\n## ", 1)[0].strip().splitlines()[-1]
    assert last == (f'- {DAY} · correction of decision 1 ({DAY}) · Alex: "send it to both" · both refer · '
                    "now reads: R-7 · one master resume to Sam and Ira")


@pytest.mark.parametrize("argv, why", [
    (("--of", "multi", "--speaker", "sam-referrer", "--reason", "r"), "--ruling"),          # no citation
    (("--of", "multi", "--speaker", "sam-referrer", "--ruling", "R-1"), "--reason"),        # no reason
    (("--of", "nothing like this", "--speaker", "sam-referrer", "--reason", "r", "--ruling", "R-1"), "matches no"),
    (("--of", " · ", "--speaker", "sam-referrer", "--reason", "r", "--ruling", "R-1"), "matches 2"),
    (("--of", "multi", "--reason", "r", "--ruling", "R-1"), "changes nothing"),
    (("--of", "multi", "--speaker", "owner", "--reason", "r", "--ruling", "R-1"), "changes nothing"),
    (("--of", "two", "--speaker", "sam-referrer", "--reason", "r", "--ruling", "R-1"), "quotes nobody"),
    (("--of", "multi", "--speaker", "nobody-here", "--reason", "r", "--ruling", "R-1"), "not on this pursuit"),
    (("--of", "multi", "--text", "x", "--reason", "r\n## Log\n- 2026-01-01 · fake", "--ruling", "R-1"), "line break"),
    (("--of", "multi", "--text", "x", "--reason", "r · s", "--ruling", "R-1"), "separator"),
    (("--of", "multi", "--text", "   ", "--reason", "r", "--ruling", "R-1"), "empty"),
])
def test_a_correction_that_cannot_be_verified_writes_nothing(js, capsys, argv, why):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "multi-referral", "--quote", "more referrers") == 0
    assert run(js, "decision", SLUG, "--text", "two", "--ruling", "R-9") == 0
    before = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "correct", SLUG, *argv) == 2
    assert why in capsys.readouterr().err
    assert (js / "pursuits" / SLUG / "pursuit.md").read_bytes() == before

def test_strategy_holds_alexs_words_quoted_and_dated(js):
    new_acme(js)
    assert run(js, "strategy", SLUG, "--quote", "one main resume goes to Sam", "--source", "intake") == 0
    strategy = load(js)[1].split("## Strategy", 1)[1].split("\n## ", 1)[0]
    assert f'- {DAY} · "one main resume goes to Sam" (Alex, intake)' in strategy


# ------------------------------------------------------------------------------------------------ list
def test_list_shows_states_and_filters_open_ones(js, capsys):
    new_acme(js)
    run(js, "new", "--company", "acme", "--kind", "direct", "--waiting-on", "nobody")
    run(js, "set", "acme-direct-202609", "--stage", "closed-lost", "--reason", "no fit")
    capsys.readouterr()
    assert run(js, "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == ["acme-direct-202609", SLUG]
    assert run(js, "list", "--open") == 0
    out = capsys.readouterr().out
    assert SLUG in out and "acme-direct-202609" not in out
    assert "waiting on owner" in out and "2026-09-28" in out


def test_unknown_pursuit_is_refused(js, capsys):
    assert run(js, "set", "nope", "--stage", "review") == 2
    assert "no pursuit nope" in capsys.readouterr().err


def test_list_by_person_finds_every_pursuit_they_are_on(js, capsys):
    new_acme(js)
    capsys.readouterr()
    assert run(js, "list", "--person", "ira-introducer") == 0
    assert SLUG in capsys.readouterr().out
    assert run(js, "list", "--person", "lee-eng") == 0
    assert SLUG not in capsys.readouterr().out


# the record is one line per entry with " · " between parts; what would make a line ambiguous is refused at write time
@pytest.mark.parametrize("text, argv, why", [
    ("t", ("--quote", 'he said "yes" · then no'), "separator"),
    ("t", ("--quote", "line one\nline two"), "line break"),
    ("t", ("--quote", "", "--ruling", "R-1"), "--quote is empty"),          # never a speaker None: ""
    ("t", ("--quote", "   ", "--ruling", "R-1"), "--quote is empty"),
    ('Sam\'s ask: "one master" · send Friday', ("--ruling", "R-7"), "reads as a quote"),
    ("   ", ("--ruling", "R-7"), "empty"),
    ("a\n## Log\n- 2026-01-01 · fake", ("--ruling", "R-7"), "line break"),
    ("first part\u2028second part", ("--quote", "q"), "line break"),       # splitlines() splits on U+2028
    ("first part\x85second part", ("--quote", "q"), "line break"),
])
def test_a_decision_that_would_read_ambiguously_is_refused(js, capsys, text, argv, why):
    new_acme(js)
    before = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "decision", SLUG, "--text", text, *argv) == 2
    assert why in capsys.readouterr().err
    assert (js / "pursuits" / SLUG / "pursuit.md").read_bytes() == before


def test_a_blank_quote_never_reaches_a_correction_or_a_reopen(js, capsys):
    new_acme(js)
    assert run(js, "decision", SLUG, "--text", "t1", "--ruling", "R-1") == 0
    before = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "correct", SLUG, "--of", "t1", "--text", "t9", "--reason", "r", "--ruling", "R-3",
               "--quote", "") == 2
    assert "--quote is empty" in capsys.readouterr().err
    assert run(js, "set", SLUG, "--stage", "paused") == 0
    paused = (js / "pursuits" / SLUG / "pursuit.md").read_bytes()
    assert run(js, "reopen", SLUG, "--stage", "drafted", "--reason", "back", "--ruling", "R-6", "--quote", " ") == 2
    assert "--quote is empty" in capsys.readouterr().err
    assert (js / "pursuits" / SLUG / "pursuit.md").read_bytes() == paused != before


@pytest.mark.parametrize("name, why", [('Robert "Bob" Jones', "cannot be quoted"), ("Alex", "reads as the owner")])
def test_a_speaker_whose_name_would_misread_is_refused(js, capsys, name, why):
    w(js / "prospects" / "bob-jones.md", f"---\nid: bob-jones\nname: '{name}'\n---\n")
    new_acme(js, "--contact", "bob-jones")
    assert run(js, "decision", SLUG, "--text", "t", "--quote", "his words", "--speaker", "bob-jones",
               "--ruling", "R-1") == 2
    assert why in capsys.readouterr().err
