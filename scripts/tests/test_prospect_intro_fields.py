"""prospects.py and build_graph.py: the introduction fields (people, how they are connected, who asked whom
to interact with whom). introduced_by is the prospect id of the person who made the introduction; asked_by is the
prospect id of the person who asked the owner to talk to this person. `set` accepts only an existing prospect id other
than the record's own, and an empty value clears the field. build_graph draws "introduced" from introduced_by (the
relation text stays the fallback, one edge when both say the same) and "asked" from asked_by."""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import prospects as pr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_graph as bg  # noqa: E402

TODAY = date(2026, 10, 4)


# --------------------------------------------------------------------------- prospects.py set
@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / "a" / "b" / "c" / "data"
    js.mkdir(parents=True)
    r = cu.Roots(js=js, os=tmp_path / "os")
    for pid in ("sam-rivera", "priya-nair", "jordan-lee"):
        meta = pr.new_meta(pid, pid.replace("-", " ").title(), source="referral", today=TODAY)
        jc.write_record(r.prospects / f"{pid}.md", meta, "# x\n\n## Timeline\n")
    return r


def _load(roots, slug):
    return jc.read_record(roots.prospects / f"{slug}.md")[0]


def _cli(roots, *args):
    return pr.main(["--root", str(roots.js), "--today", TODAY.isoformat(), *args])


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_set_accepts_an_existing_prospect_id(roots, key):
    fields, _, _, _ = pr.parse_assignments([f"{key}=sam-rivera"])
    pr.set_fields(roots, "priya-nair", fields=fields, today=TODAY)
    assert _load(roots, "priya-nair")[key] == "sam-rivera"


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_the_cli_sets_the_field_and_exits_zero(roots, key, capsys):
    assert _cli(roots, "set", "priya-nair", f"{key}=jordan-lee") == 0
    assert _load(roots, "priya-nair")[key] == "jordan-lee"


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_set_refuses_an_unknown_prospect_id(roots, key, capsys):
    before = (roots.prospects / "priya-nair.md").read_text(encoding="utf-8")
    assert _cli(roots, "set", "priya-nair", f"{key}=no-such-person") != 0
    err = json.loads(capsys.readouterr().out)["error"]
    assert "no-such-person" in err and key in err
    assert (roots.prospects / "priya-nair.md").read_text(encoding="utf-8") == before
    with pytest.raises(ValueError):
        pr.set_fields(roots, "priya-nair", fields={key: "no-such-person"}, today=TODAY)


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_set_refuses_the_records_own_id(roots, key, capsys):
    assert _cli(roots, "set", "priya-nair", f"{key}=priya-nair") != 0
    err = json.loads(capsys.readouterr().out)["error"]
    assert key in err and "its own id" in err
    assert not _load(roots, "priya-nair").get(key)


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_set_refuses_a_value_that_is_not_an_id(roots, key):
    with pytest.raises(ValueError):
        pr.parse_assignments([f"{key}=../core/identity"])


@pytest.mark.parametrize("key", ["introduced_by", "asked_by"])
def test_an_empty_value_clears_the_field(roots, key, capsys):
    assert _cli(roots, "set", "priya-nair", f"{key}=sam-rivera") == 0
    assert _cli(roots, "set", "priya-nair", f"{key}=") == 0
    assert not _load(roots, "priya-nair").get(key)


# --------------------------------------------------------------------------- build_graph.py edges
def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def _person(js: Path, pid: str, extra: str = "") -> None:
    w(js / "prospects" / f"{pid}.md", f"---\ntype: prospect\nid: {pid}\nname: {pid.replace('-', ' ').title()}\n"
                                      f"status: triaged\npriority: P2\nwarmth: 1\n{extra}---\n")


@pytest.fixture
def js(tmp_path: Path) -> Path:
    root = tmp_path / "os" / "a" / "b" / "c" / "data"
    _person(root, "sam-rivera")
    _person(root, "pat-morgan")
    # both the field and the matching relation text: one edge
    _person(root, "priya-nair", "introduced_by: sam-rivera\nasked_by: pat-morgan\n"
                                      "relation: personal; introduced by sam-rivera\n")
    # only the field
    _person(root, "jordan-lee", "introduced_by: pat-morgan\n")
    # only the relation text: the fallback still draws the edge
    _person(root, "rae-quinn", "relation: introduced by pat-morgan\n")
    return root


def _edges(g, relation):
    return sorted((e.src, e.dst) for e in g.edges if e.relation == relation)


def test_introduced_by_plus_a_matching_relation_gives_one_edge(js):
    g = bg.load(js, TODAY.isoformat())
    assert _edges(g, "introduced").count(("sam-rivera", "priya-nair")) == 1


def test_introduced_by_alone_draws_the_edge_and_the_relation_stays_the_fallback(js):
    g = bg.load(js, TODAY.isoformat())
    intro = _edges(g, "introduced")
    assert ("pat-morgan", "jordan-lee") in intro and ("pat-morgan", "rae-quinn") in intro
    assert len(intro) == 3


def test_introduced_by_wins_over_a_relation_that_names_someone_else(js):
    _person(js, "lin-ortiz", "introduced_by: sam-rivera\nrelation: introduced by pat-morgan\n")
    g = bg.load(js, TODAY.isoformat())
    intro = _edges(g, "introduced")
    assert ("sam-rivera", "lin-ortiz") in intro and ("pat-morgan", "lin-ortiz") not in intro


def test_asked_by_draws_an_asked_edge_person_to_person(js):
    g = bg.load(js, TODAY.isoformat())
    asked = [e for e in g.edges if e.relation == "asked"]
    assert [(e.src_kind, e.src, e.dst_kind, e.dst) for e in asked] == [("person", "pat-morgan", "person", "priya-nair")]
    assert asked[0].source == "prospects/priya-nair.md"
