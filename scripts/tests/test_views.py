"""The Obsidian vault (vault root = workspace, config in workspace/.obsidian/; front page domains/TRACKERS.md).

Every views/*.base must parse, filter on its record folder by a vault-relative path, name only real
fields, and label columns with `note.<field>` / `formula.<name>` keys: Obsidian silently ignores a
bare `<field>:` display name (found 2026-09-26). The bookmarks must point at files that exist.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

import comms_helpers  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import outbox as ob
import prospects as pr

TODAY = date(2026, 9, 24)
pytestmark = pytest.mark.workspace              # the vault-level views, bookmarks and front page
VAULT = jc.OS_ROOT
VIEWS = jc.JS_ROOT / "views"


def _prospect_fields():
    return set(pr.new_meta("x", "X", source="gmail-backlog", today=TODAY))


def _application_fields():
    return set(jc.fm.parse_yaml((__import__("agency_paths").SCHEMAS_DIR / "application.yaml").read_text())["fields"])


def _outbox_fields(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    path = ob.create_draft(cu.Roots(js=js, os=tmp_path / "os"), prospect=None, channel="email", kind="official",
                           to="x@y.example", subject="Hi", body="Dear team,\n\nA question.\n\nBest regards,\nAlex",
                           today=TODAY)
    return set(jc.read_record(path)[0]) | {"approval", "sent_at"}


def _schema_file(name):
    """The repo's schema, or (the owner's vault only) the workspace kernel's, for schemas the agency does not ship."""
    here = __import__("agency_paths").SCHEMAS_DIR / f"{name}.yaml"
    return here if here.is_file() else jc.OS_ROOT / "system" / "schemas" / f"{name}.yaml"


def _schema_fields(*names):
    out = set()
    for name in names:
        out |= set(jc.fm.parse_yaml(_schema_file(name).read_text())["fields"])
    return out


def _job_fields():
    import fetch_ats as fa
    from datetime import datetime, timezone
    rec = fa.make_record({"name": "A", "slug": "a"}, title="T", source="greenhouse", source_url="https://x.example/1",
                         apply_url=None, ats_vendor="greenhouse", posted_at=None, location="SF", remote="onsite",
                         comp=(1, 2, "USD"), employment_type="full-time", jd_text="x",
                         now=datetime(2026, 9, 26, tzinfo=timezone.utc), req_id=None)
    return _schema_fields("job") | set(rec) | {"gate", "gate_reasons", "gate_evidence"}


def _build_graph():
    import build_graph
    return build_graph


PHD = jc.JS_ROOT.parent / "phd-applications"
# view file -> (the folder its records live in, a function returning the real field names)
CASES = {VIEWS / "prospects.base": (jc.JS_ROOT / "prospects", _prospect_fields),
         VIEWS / "follow-ups.base": (jc.JS_ROOT / "prospects", _prospect_fields),
         VIEWS / "applications.base": (jc.JS_ROOT / "applications", _application_fields),
         VIEWS / "outbox.base": (jc.JS_ROOT / "outbox", None),
         VIEWS / "jobs.base": (jc.JS_ROOT / "jobs", _job_fields),
         VIEWS / "pursuits.base": (jc.JS_ROOT / "pursuits", lambda: _schema_fields("pursuit")),
         VIEWS / "companies.base": (jc.JS_ROOT / "graph" / "companies", lambda: set(_build_graph().COMPANY_FIELDS)),
         VIEWS / "people.base": (jc.JS_ROOT / "graph" / "people", lambda: set(_build_graph().PERSON_FIELDS)),
         }
if _schema_file("phd-program").is_file() and (PHD / "views" / "phd.base").is_file():     # the owner's vault only
    CASES[PHD / "views" / "phd.base"] = (PHD, lambda: _schema_fields("phd-program", "phd-application", "faculty",
                                                                      "recommender"))


def test_every_view_file_is_covered():
    assert sorted((VAULT / "domains").glob("**/views/*.base")) == sorted(CASES)


@pytest.mark.parametrize("path", sorted(CASES), ids=lambda p: p.name)
def test_view_parses_filters_its_folder_and_names_only_real_fields(path, tmp_path):
    name = path.name
    folder, fields_of = CASES[path]
    fields = fields_of() if fields_of else _outbox_fields(tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rel = folder.relative_to(VAULT).as_posix()
    assert data["filters"]["and"][0] == f'file.inFolder("{rel}")'
    formulas = set(data.get("formulas") or {})

    def real(prop):
        if prop.startswith("formula."):
            return prop.split(".", 1)[1] in formulas
        if prop.startswith("note."):
            prop = prop.split(".", 1)[1]
        return prop.startswith("file.") or prop in fields

    assert data["views"] and all(v["type"] == "table" and v["name"] for v in data["views"])
    for view in data["views"]:
        for prop in view.get("order", []) + [s["property"] for s in view.get("sort", [])]:
            assert real(prop), (name, view["name"], prop)
        group = (view.get("groupBy") or {}).get("property")
        assert group is None or real(group), (name, view["name"], group)
    for key in data.get("properties") or {}:
        assert key.split(".", 1)[0] in ("note", "formula", "file"), f"{name}: bare display-name key {key!r} is ignored"
        assert real(key), (name, key)


def test_vault_bookmarks_point_at_files_that_exist():
    bookmarks = json.loads((VAULT / ".obsidian" / "bookmarks.json").read_text())
    paths = [item["path"] for top in bookmarks["items"] for item in top.get("items", [top])]
    assert paths and all((VAULT / p).exists() for p in paths), [p for p in paths if not (VAULT / p).exists()]


def test_front_page_embeds_only_views_that_exist():
    import re
    page = (VAULT / "domains" / "TRACKERS.md").read_text(encoding="utf-8")
    embeds = re.findall(r"!\[\[([^\]#|]+\.base)#([^\]|]+)\]\]", page)
    assert embeds
    by_name = {p.name: p for p in CASES}
    for base, view in embeds:
        views = {v["name"] for v in yaml.safe_load(by_name[base].read_text(encoding="utf-8"))["views"]}
        assert view in views, (base, view)
