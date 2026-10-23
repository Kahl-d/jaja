"""every application says whether a person carried it or it went cold, so the funnel can be split by path.
`tracker.py path <slug> --path person|cold|unknown [--via <prospect id>] --source "<evidence>"` writes path,
via_prospect and path_source (schemas/application.yaml); `metrics` adds by_path from the same loop as
response_rate_by_applied_via, "missing" while an applied application has no path. "Applied" keeps its meaning."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import tracker as tr  # noqa: E402
from js_common import OS_ROOT, dept_day, fm  # noqa: E402
from test_tracker import _app  # noqa: E402  (the metrics fixture: a job plus an application with events)

TODAY = dept_day(NOW).isoformat()


def _record(root, slug="acme-a", props_extra=None):
    folder = root / "applications" / slug
    folder.mkdir(parents=True, exist_ok=True)
    props = {"id": slug, "job": "[[acme-1]]", "company": "Acme", "title": "AI Engineer", "stage": "applied",
            "created": "2026-08-01", "updated": "2026-08-01", **(props_extra or {})}
    body = "# app\n\n## Events\n" + tr.format_event("2026-08-02T00:00:00Z", "applied", "manual", 1.0, "e") + "\n"
    (folder / "application.md").write_text(fm.dump(props, body))
    return folder / "application.md"


def _prospect(root, pid="jane-doe"):
    (root / "prospects").mkdir(exist_ok=True)
    (root / "prospects" / f"{pid}.md").write_text(f"---\nid: {pid}\n---\n# {pid}\n")


def _props(root, slug="acme-a"):
    return fm.load(root / "applications" / slug / "application.md")[0]


def _cli(root, *args, now=NOW):
    return tr.main(["--root", str(root), "path", *args], now=now)


# ----------------------------------------------------------------------------- the write path
def test_person_records_all_three_fields_and_bumps_updated(root):
    _record(root)
    _prospect(root)
    out = tr.set_path(root, "acme-a", "person", "jane-doe", "  outbox/M17 referral, 2026-10-03  ", NOW)
    assert out["from"] is None and out["to"] == "person" and set(out["changed"]) == {"path", "via_prospect", "path_source"}
    props = _props(root)
    assert (props["path"], props["via_prospect"], props["path_source"]) == ("person", "jane-doe",
                                                                         "outbox/M17 referral, 2026-10-03")
    assert str(props["updated"]) == TODAY


@pytest.mark.parametrize("via", [None, "", "   "])
def test_person_needs_via(root, via, capsys):
    path = _record(root)
    before = path.read_bytes()
    with pytest.raises(tr.TrackerError, match="needs --via"):
        tr.set_path(root, "acme-a", "person", via, "x", NOW)
    assert _cli(root, "acme-a", "--path", "person", "--source", "x") == 2
    assert "refused: path person needs --via" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("via", ["no-such-person", "../applications/acme-a/application"])
def test_person_via_must_name_an_existing_prospect(root, via, capsys):
    path = _record(root)
    _prospect(root)
    before = path.read_bytes()
    with pytest.raises(tr.TrackerError, match="names no prospect record"):
        tr.set_path(root, "acme-a", "person", via, "x", NOW)
    assert _cli(root, "acme-a", "--path", "person", "--via", via, "--source", "x") == 2
    assert "names no prospect record" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["cold", "unknown"])
def test_cold_and_unknown_refuse_via(root, kind, capsys):
    path = _record(root)
    _prospect(root)
    before = path.read_bytes()
    with pytest.raises(tr.TrackerError, match="takes no --via"):
        tr.set_path(root, "acme-a", kind, "jane-doe", "x", NOW)
    assert _cli(root, "acme-a", "--path", kind, "--via", "jane-doe", "--source", "x") == 2
    assert f"refused: path {kind} takes no --via" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["cold", "unknown"])
def test_cold_and_unknown_write_via_prospect_null(root, kind):
    path = _record(root)
    assert _cli(root, "acme-a", "--path", kind, "--source", "ATS form, no referral field") == 0
    props = _props(root)
    assert "via_prospect" in props and props["via_prospect"] is None and props["path"] == kind
    assert "via_prospect: null" in path.read_text()


@pytest.mark.parametrize("source", [None, "", "   "])
def test_source_is_required_and_non_empty(root, source, capsys):
    path = _record(root)
    before = path.read_bytes()
    with pytest.raises(tr.TrackerError, match="--source must cite the evidence"):
        tr.set_path(root, "acme-a", "cold", None, source, NOW)
    args = ["acme-a", "--path", "cold"] + ([] if source is None else ["--source", source])
    assert _cli(root, *args) == 2
    assert "refused: --source must cite the evidence" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_unknown_path_value_and_missing_record_are_refused(root, capsys):
    _record(root)
    with pytest.raises(tr.TrackerError, match="is not one of person, cold, unknown"):
        tr.set_path(root, "acme-a", "warm", None, "x", NOW)
    with pytest.raises(SystemExit) as exc:                              # argparse choices: usage error, exit 2
        _cli(root, "acme-a", "--path", "warm", "--source", "x")
    assert exc.value.code == 2
    capsys.readouterr()
    assert _cli(root, "nope", "--path", "cold", "--source", "x") == 2
    assert "refused: no application record for 'nope'" in capsys.readouterr().err
    assert not (root / "applications" / "nope").exists()


def test_same_values_again_is_a_no_op(root, capsys):
    path = _record(root)
    _prospect(root)
    assert _cli(root, "acme-a", "--path", "person", "--via", "jane-doe", "--source", "ev") == 0
    capsys.readouterr()
    before = path.read_bytes()
    later = NOW + timedelta(days=3)                                    # a later run must not bump `updated`
    assert _cli(root, "acme-a", "--path", "person", "--via", "jane-doe", "--source", "ev", now=later) == 0
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "acme-a: path person unchanged (same via_prospect and path_source; nothing written)" in out
    assert tr.set_path(root, "acme-a", "person", "jane-doe", "ev", later)["changed"] == {}


def test_different_values_overwrite_and_say_what_changed(root, capsys):
    _record(root)
    _prospect(root)
    assert _cli(root, "acme-a", "--path", "person", "--via", "jane-doe", "--source", "ev one") == 0
    capsys.readouterr()
    later = NOW + timedelta(days=3)
    assert _cli(root, "acme-a", "--path", "cold", "--source", "ev two", now=later) == 0
    out = capsys.readouterr().out
    assert "acme-a: path person -> cold" in out
    assert "  via_prospect: 'jane-doe' -> None" in out and "  path_source: 'ev one' -> 'ev two'" in out
    assert "  path: 'person' -> 'cold'" in out
    props = _props(root)
    assert (props["path"], props["via_prospect"], props["path_source"]) == ("cold", None, "ev two")
    assert str(props["updated"]) == dept_day(later).isoformat()
    assert _cli(root, "acme-a", "--path", "cold", "--source", "ev three", now=later) == 0   # one field differs
    assert capsys.readouterr().out.splitlines()[1:] == ["  path_source: 'ev two' -> 'ev three'"]


def test_a_field_written_by_hand_without_the_other_two_is_completed(root):
    _record(root, props_extra={"path": "cold"})                          # no via_prospect, no path_source on file
    out = tr.set_path(root, "acme-a", "cold", None, "ev", NOW)
    assert set(out["changed"]) == {"via_prospect", "path_source"}
    assert "via_prospect" in _props(root) and _props(root)["path_source"] == "ev"


def test_dry_run_writes_nothing(root, capsys):
    path = _record(root)
    before = path.read_bytes()
    assert _cli(root, "acme-a", "--path", "cold", "--source", "ev", "--dry-run") == 0
    assert path.read_bytes() == before
    assert "acme-a: path None -> cold (dry-run, nothing written)" in capsys.readouterr().out


def test_a_re_projection_keeps_the_fields(root):
    _record(root)
    _prospect(root)
    tr.set_path(root, "acme-a", "person", "jane-doe", "ev", NOW)
    tr.main(["--root", str(root), "project"], now=NOW)
    props = _props(root)
    assert (props["path"], props["via_prospect"], props["path_source"]) == ("person", "jane-doe", "ev")


# ----------------------------------------------------------------------------- the schema
def test_the_schema_names_the_fields_and_the_enum():
    schema = fm.parse_yaml((__import__("agency_paths").SCHEMAS_DIR / "application.yaml").read_text())
    assert {"path", "via_prospect", "path_source"} <= set(schema["fields"])
    assert tuple(schema["enums"]["path"]) == tr.APP_PATHS
    assert not {"path", "via_prospect", "path_source"} & set(schema["required"])     # 88 records, backfill later


def test_lint_accepts_a_written_record_and_flags_a_bad_path(root):
    import lint                                                         # the kernel's lint (system/scripts)
    schema = fm.parse_yaml((__import__("agency_paths").SCHEMAS_DIR / "application.yaml").read_text())
    _record(root)
    tr.set_path(root, "acme-a", "cold", None, "ev", NOW)
    assert lint.validate(_props(root), schema, "a.md", "application") == []
    bad = {**_props(root), "path": "warm"}
    assert [f.code for f in lint.validate(bad, schema, "a.md", "application")] == ["schema-enum"]


# ----------------------------------------------------------------------------- metrics
def _world(root):
    _app(root, "p1", "greenhouse", "referral", [("2026-08-02T00:00:00Z", "applied"),
                                                ("2026-08-06T00:00:00Z", "screen")])
    _app(root, "c1", "greenhouse", "ats", [("2026-08-02T00:00:00Z", "applied"),
                                           ("2026-08-07T00:00:00Z", "rejected")])
    _app(root, "c2", "hn", "ats", [("2026-08-03T00:00:00Z", "applied")])
    _app(root, "u1", "hn", "ats", [("2026-08-03T00:00:00Z", "applied"), ("2026-08-09T00:00:00Z", "interview")])
    _app(root, "m1", "adzuna", "ats", [("2026-08-04T00:00:00Z", "applied")])
    _app(root, "m2", "adzuna", "ats", [("2026-08-01T00:00:00Z", "discovered")])      # never applied: not counted
    _prospect(root)
    tr.set_path(root, "p1", "person", "jane-doe", "ev", NOW)
    tr.set_path(root, "c1", "cold", None, "ev", NOW)
    tr.set_path(root, "c2", "cold", None, "ev", NOW)
    tr.set_path(root, "u1", "unknown", None, "ev", NOW)
    tr.set_path(root, "m2", "cold", None, "ev", NOW)


def test_metrics_splits_applied_by_path_with_missing(root):
    _world(root)
    m = tr.metrics(root, NOW)
    assert m["by_path"] == {
        "cold": {"applied": 2, "responded": 1, "screen": 0, "rate": 0.5},
        "missing": {"applied": 1, "responded": 0, "screen": 0, "rate": 0.0},
        "person": {"applied": 1, "responded": 1, "screen": 1, "rate": 1.0},
        "unknown": {"applied": 1, "responded": 1, "screen": 1, "rate": 1.0},
    }
    assert sum(v["applied"] for v in m["by_path"].values()) == m["funnel"]["applied"] == 5


def test_metrics_text_lines(root, capsys):
    _world(root)
    assert tr.main(["--root", str(root), "metrics"], now=NOW) == 0
    out = capsys.readouterr().out
    assert ("by path: person 1/1 responded (1 screens), cold 1/2 responded (0 screens), "
            "unknown 1/1 responded (1 screens), missing 0/1 responded (0 screens)") in out
    assert ('path missing: 1 applied applications (tracker.py path <slug> --path person|cold|unknown '
            '[--via <prospect id>] --source "<evidence>")') in out
    tr.set_path(root, "m1", "cold", None, "ev", NOW)
    assert tr.main(["--root", str(root), "metrics"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "by path: person 1/1 responded (1 screens), cold 1/3 responded (0 screens), unknown 1/1" in out
    assert "path missing" not in out


def test_metrics_text_on_an_empty_department(root, capsys):
    assert tr.main(["--root", str(root), "metrics"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "by path: no data yet" in out and "path missing" not in out


def test_metrics_json_keeps_the_by_path_key(root, capsys):
    _world(root)
    assert tr.main(["--root", str(root), "metrics", "--json"], now=NOW) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["by_path"]["missing"]["applied"] == 1 and data["by_path"]["person"]["screen"] == 1


def test_applied_and_the_other_buckets_do_not_move(root):
    """The guard: test_tracker.test_metrics' world gives the same funnel, by_source and by_applied_via, with and
    without paths recorded; by_path only splits the applied set those count."""
    _app(root, "a1", "greenhouse", "ats", [("2026-08-02T00:00:00Z", "applied"), ("2026-08-06T00:00:00Z", "screen"),
                                           ("2026-08-08T00:00:00Z", "confirmation_received"),
                                           ("2026-08-16T00:00:00Z", "interview")])
    _app(root, "a2", "greenhouse", "referral", [("2026-08-05T00:00:00Z", "applied"),
                                                ("2026-08-07T00:00:00Z", "rejected")])
    _app(root, "a3", "hn", "ats", [("2026-08-01T12:00:00Z", "applied")])
    _app(root, "a4", "adzuna", "ats", [("2026-08-01T00:00:00Z", "discovered")])
    expected_via = {"ats": {"applied": 2, "responded": 1, "rate": 0.5},
                    "referral": {"applied": 1, "responded": 1, "rate": 1.0}}
    expected_source = {"greenhouse": {"applied": 2, "responded": 2, "rate": 1.0},
                       "hn": {"applied": 1, "responded": 0, "rate": 0.0}}
    before = tr.metrics(root, NOW)
    assert before["funnel"]["applied"] == 3 and before["response_rate_by_applied_via"] == expected_via
    assert before["response_rate_by_source"] == expected_source
    assert before["by_path"] == {"missing": {"applied": 3, "responded": 2, "screen": 1, "rate": 0.667}}
    _prospect(root)
    tr.set_path(root, "a2", "person", "jane-doe", "ev", NOW)
    tr.set_path(root, "a1", "cold", None, "ev", NOW)
    after = tr.metrics(root, NOW)
    for key in ("funnel", "outcomes", "response_rate_by_source", "response_rate_by_applied_via",
                "median_days_in_stage", "apply_latency_hours", "applications"):
        assert after[key] == before[key], key
    assert after["by_path"]["missing"]["applied"] == 1
    assert sum(v["applied"] for v in after["by_path"].values()) == after["funnel"]["applied"] == 3
