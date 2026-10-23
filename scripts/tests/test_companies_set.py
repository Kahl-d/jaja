"""companies_set.py: the one setter for watchlist fields scripts discover (github_org, ats_vendor,
board_token, careers_url). Line-preserving edits of targets/companies.yaml, validated values, before and after printed;
`proposals` lists what scripts proposed and is not applied yet."""
from __future__ import annotations

import json

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403

import companies_set as cs  # noqa: E402

YAML = """# Watchlist header comment, kept
companies:
  - name: Globex
    slug: globex
    tier: strong
    ats_vendor: ashby
    board_token: "globex"
    careers_url: "https://www.globex.example/careers"
  # the next entry's comment, kept
  - name: Acme
    slug: acme
    tier: dream
    ats_vendor: other
    careers_url: "https://acme.example/careers"
"""


@pytest.fixture
def js(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(YAML, encoding="utf-8")
    return root


def entries(js):
    return {c["slug"]: c for c in yaml.safe_load((js / "targets" / "companies.yaml").read_text())["companies"]}


def test_set_adds_a_field_keeping_every_other_line(js, capsys):
    assert cs.main(["set", "globex", "github_org", "globex-labs", "--root", str(js), "--source", "people_traces"]) == 0
    text = (js / "targets" / "companies.yaml").read_text()
    assert entries(js)["globex"]["github_org"] == "globex-labs"
    assert text.replace("    github_org: globex-labs\n", "") == YAML
    assert "github_org: None -> globex-labs" in capsys.readouterr().out


def test_set_replaces_an_existing_value(js):
    cs.main(["set", "acme", "ats_vendor", "eightfold", "--root", str(js), "--source", "ats_detect"])
    cs.main(["set", "acme", "board_token", "acme.eightfold.ai", "--root", str(js), "--source", "ats_detect"])
    e = entries(js)["acme"]
    assert (e["ats_vendor"], e["board_token"]) == ("eightfold", "acme.eightfold.ai")
    assert (js / "targets" / "companies.yaml").read_text().count("ats_vendor: eightfold") == 1


@pytest.mark.parametrize("field, value", [("github_org", "bad org!"), ("ats_vendor", "myspace"), ("tier", "dream"),
                                          ("careers_url", "ftp://x"), ("board_token", "a b")])
def test_set_refuses_fields_and_values_outside_the_rules(js, capsys, field, value):
    assert cs.main(["set", "acme", field, value, "--root", str(js), "--source", "x"]) == 2
    assert (js / "targets" / "companies.yaml").read_text() == YAML


def test_set_refuses_an_unknown_company_and_dry_run_writes_nothing(js, capsys):
    assert cs.main(["set", "nope", "github_org", "x", "--root", str(js), "--source", "x"]) == 2
    assert cs.main(["set", "acme", "github_org", "acme-hq", "--root", str(js), "--source", "x", "--dry-run"]) == 0
    assert (js / "targets" / "companies.yaml").read_text() == YAML


def test_proposals_lists_what_is_not_applied_yet(js, capsys):
    p = js / "state" / "proposals" / "companies.jsonl"
    p.parent.mkdir(parents=True)
    rows = [{"date": "2026-10-01", "slug": "globex", "field": "github_org", "value": "globex-labs",
             "source": "people_traces", "apply": "python3 scripts/companies_set.py set globex github_org globex-labs"},
            {"date": "2026-10-01", "slug": "acme", "field": "ats_vendor", "value": "other", "source": "ats_detect",
             "apply": "..."}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cs.main(["proposals", "--root", str(js)]) == 0
    out = capsys.readouterr().out
    assert "globex github_org globex-labs" in out and "acme ats_vendor other" not in out   # already the value


def test_apply_sets_every_pending_proposal_of_one_source_and_is_a_dry_run_by_default(js, capsys):
    p = js / "state" / "proposals" / "companies.jsonl"
    p.parent.mkdir(parents=True)
    rows = [{"date": "2026-10-01", "slug": "acme", "field": "ats_vendor", "value": "eightfold", "source": "ats_detect"},
            {"date": "2026-10-01", "slug": "acme", "field": "board_token", "value": "careers.acme.example|acme.example",
             "source": "ats_detect"},
            {"date": "2026-10-01", "slug": "globex", "field": "github_org", "value": "globex-labs", "source": "people_traces"}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cs.main(["apply", "--source", "ats_detect", "--root", str(js)]) == 0
    assert (js / "targets" / "companies.yaml").read_text() == YAML and "dry-run" in capsys.readouterr().out
    assert cs.main(["apply", "--source", "ats_detect", "--root", str(js), "--write"]) == 0
    e = entries(js)
    assert (e["acme"]["ats_vendor"], e["acme"]["board_token"]) == ("eightfold", "careers.acme.example|acme.example")
    assert "github_org" not in e["globex"]                       # another source's proposal is left alone


# After S-8: `feeds` (a list, appended one URL at a time, feed_watch), the Oracle location filters (D3), and `unset` (D1:
# grammarly's stale board token).
def test_feeds_is_a_list_appended_one_url_at_a_time(js, capsys):
    assert cs.main(["set", "acme", "feeds", "https://acme.example/blog/feed/", "--root", str(js), "--source", "feed_watch"]) == 0
    assert cs.main(["set", "acme", "feeds", "https://acme.example/news/feed/", "--root", str(js), "--source", "feed_watch"]) == 0
    assert cs.main(["set", "acme", "feeds", "https://acme.example/blog/feed/", "--root", str(js), "--source", "feed_watch"]) == 0
    assert entries(js)["acme"]["feeds"] == ["https://acme.example/blog/feed/", "https://acme.example/news/feed/"]
    assert "no change" in capsys.readouterr().out
    assert cs.main(["set", "acme", "feeds", "not a url", "--root", str(js), "--source", "x"]) == 2


def test_oracle_location_fields_take_a_regex(js):
    assert cs.main(["set", "acme", "oracle_location", "Springfield", "--root", str(js), "--source", "x"]) == 0
    assert cs.main(["set", "acme", "oracle_exclude_location", "Springfield|Riverton", "--root", str(js), "--source", "x"]) == 0
    assert entries(js)["acme"]["oracle_location"] == "Springfield"
    assert cs.main(["set", "acme", "oracle_location", "(unclosed", "--root", str(js), "--source", "x"]) == 2


def test_unset_removes_a_field_line_and_nothing_else(js, capsys):
    assert cs.main(["unset", "globex", "board_token", "--root", str(js), "--source", "D1"]) == 0
    text = (js / "targets" / "companies.yaml").read_text()
    assert "board_token" not in entries(js)["globex"] and text == YAML.replace('    board_token: "globex"\n', "")
    assert "board_token: globex -> (removed)" in capsys.readouterr().out
    assert cs.main(["unset", "globex", "board_token", "--root", str(js), "--source", "D1"]) == 0   # already gone


def test_an_applied_feed_is_no_longer_pending(js, capsys):
    p = js / "state" / "proposals" / "companies.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"slug": "acme", "field": "feeds", "value": "https://acme.example/blog/feed/",
                             "source": "feed_watch"}) + "\n")
    cs.main(["apply", "--source", "feed_watch", "--root", str(js), "--write"])
    capsys.readouterr()
    cs.main(["proposals", "--root", str(js)])
    assert "0 proposal(s) not applied" in capsys.readouterr().out
