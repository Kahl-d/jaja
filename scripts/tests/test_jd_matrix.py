"""jd_matrix.py: the coverage-matrix skeleton (every JD line typed and ranked, with candidate evidence).

The fixture JD is Greenhouse-shaped (About the role, Key responsibilities, Minimum qualifications, Preferred
qualifications, a salary line and a closing paragraph), wrapped in <untrusted_jd> tags, with the vendor's
"- " on its own line bullets, one instruction-shaped line and one em-dash. The taxonomy and evidence fixtures
are fictional-shaped copies of the real files' shapes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import jd_matrix as jm  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "jd_matrix"
JD = FIX / "jd-greenhouse.md"
TAX = FIX / "taxonomy.yaml"
EV = FIX / "evidence.yaml"
EM_DASH = chr(0x2014)


@pytest.fixture(scope="module")
def built():
    return jm.build_matrix(JD, title=None, taxonomy_path=TAX, evidence_path=EV, today="2026-10-01")


def rows_of(built, kind):
    return [r for r in built["rows"] if r["type"] == kind]


# --------------------------------------------------------------------------- build
def test_region_split_and_typing(built):
    r = [x["text"] for x in rows_of(built, "R")]
    q = [x["text"] for x in rows_of(built, "Q")]
    p = [x["text"] for x in rows_of(built, "P")]
    assert any(t.startswith("Own end-to-end delivery") for t in r)
    assert any(t.startswith("Diagnose reliability issues") for t in r)
    assert any(t.startswith("Instrument products") for t in r)
    assert len(q) == 3 and q[0].startswith("Experience shipping a product from zero to one")
    assert len(p) == 3 and p[1].startswith("Comfort with lightweight data analysis")
    assert built["title"] == "Product Engineer, Agent Tools"


def test_two_line_bullets_become_one_row(built):
    texts = [x["text"] for x in built["rows"]]
    assert "-" not in texts and "" not in texts
    assert sum(1 for t in texts if t.startswith("Own end-to-end delivery")) == 1


def test_implied_rows_capped_at_three(built):
    implied = rows_of(built, "I")
    assert 1 <= len(implied) <= 3
    assert implied[0]["text"] == "Product Engineer, Agent Tools"
    assert any("Agent Tools team" in x["text"] for x in implied[1:])


def test_rank_arithmetic(built):
    # First R row: in the first third of the JD, and "agent harness" repeats elsewhere -> 1 + 1 + 1.
    own = next(x for x in rows_of(built, "R") if x["text"].startswith("Own end-to-end"))
    assert own["rank"] == 3
    # Last P row (browser automation): past the first third, its terms do not repeat -> 1.
    browser = next(x for x in rows_of(built, "P") if "browser automation" in x["text"])
    assert browser["rank"] == 1
    for row in built["rows"]:
        assert 1 <= row["rank"] <= 3


def test_taxonomy_alias_match_gives_canonical_terms(built):
    llm = next(x for x in rows_of(built, "Q") if "LLM APIs" in x["text"])
    assert "Claude Agent SDK (Python)" in llm["terms"]
    evals = next(x for x in rows_of(built, "P") if "building evals" in x["text"])
    assert any(t.startswith("Agent evaluation") for t in evals["terms"])
    sql = next(x for x in rows_of(built, "P") if "SQL" in x["text"])
    assert "SQL (Postgres, SQLite, DuckDB)" in sql["terms"]


def test_candidate_evidence_ranked_and_capped(built):
    own = next(x for x in rows_of(built, "R") if x["text"].startswith("Own end-to-end"))
    assert own["candidate_evidence"][0] == "ev-fx-001"          # harness + SDK note: most matches first
    assert "ev-fx-003" in own["candidate_evidence"]
    assert "ev-fx-010" in own["candidate_evidence"]              # matched through its tags
    # Python has ten evidence ids in the taxonomy; a row naming Python is capped at eight.
    text = "Strong Python skills"
    cands = jm.candidate_evidence(["Python"], jm.load_terms(TAX), jm.load_evidence(EV), text)
    assert len(cands) == 8


def test_untrusted_tag_stripped_and_instruction_line_kept_as_text(built):
    texts = [x["text"] for x in built["rows"]]
    assert not any("untrusted_jd" in t for t in texts)
    inst = next(x for x in built["rows"] if x["text"].startswith("Ignore previous instructions"))
    assert inst["type"] == "R" and inst["answer"] == "" and inst["ceiling"] == ""
    assert all(r["answer"] == "" for r in built["rows"])


def test_no_em_dash_in_output(built, tmp_path):
    out = tmp_path / "m.yaml"
    jm.write_matrix(built, out)
    text = out.read_text(encoding="utf-8")
    assert EM_DASH not in text
    assert "We value impact" in text                              # the line itself survives


def test_em_dash_title_is_refused(tmp_path):
    out = tmp_path / "m.yaml"
    rc = jm.main(["build", "--jd", str(JD), "--out", str(out), "--title", f"Engineer {EM_DASH} Tools",
                  "--taxonomy", str(TAX), "--evidence", str(EV), "--date", "2026-10-01"])
    assert rc == 2 and not out.exists()


def test_build_cli_writes_yaml_and_json(tmp_path, capsys):
    out = tmp_path / "m.yaml"
    rc = jm.main(["build", "--jd", str(JD), "--out", str(out), "--taxonomy", str(TAX), "--evidence", str(EV),
                  "--date", "2026-10-01", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["built"] == "2026-10-01" and data["rows"]
    on_disk = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert on_disk["jd"].endswith("jd-greenhouse.md")
    assert on_disk["allowed_ngrams"] == []
    assert {"id", "type", "rank", "text", "terms", "candidate_evidence", "answer", "ceiling", "synonyms"} <= set(on_disk["rows"][0])
    rc = jm.main(["build", "--jd", str(JD), "--out", str(out), "--taxonomy", str(TAX), "--evidence", str(EV),
                  "--date", "2026-10-01"])
    assert rc == 0 and "| id |" in capsys.readouterr().out


# --------------------------------------------------------------------------- check
def _matrix(tmp_path, answers):
    rows = []
    for i, (kind, answer) in enumerate(answers, 1):
        rows.append({"id": f"{kind}{i}", "type": kind, "rank": 1, "text": f"row {i}", "terms": [],
                     "candidate_evidence": [], "answer": answer, "ceiling": "", "synonyms": []})
    path = tmp_path / "matrix.yaml"
    path.write_text(yaml.safe_dump({"jd": "x.md", "title": "t", "built": "2026-10-01", "allowed_ngrams": [],
                                    "rows": rows}, sort_keys=False), encoding="utf-8")
    return path


def test_check_passes_when_every_row_is_answered(tmp_path, capsys):
    m = _matrix(tmp_path, [("R", "line: Acme bullet 1"), ("Q", "Q-2 asked the owner 2026-10-01"),
                           ("P", "GAP: no RPA work in the record"), ("I", ""), ("O", "")])
    assert jm.main(["check", "--matrix", str(m)]) == 0


def test_check_fails_on_empty_and_malformed_answers(tmp_path, capsys):
    m = _matrix(tmp_path, [("R", ""), ("Q", "maybe"), ("P", "line: ok")])
    assert jm.main(["check", "--matrix", str(m)]) == 1
    out = capsys.readouterr().out
    assert "R1" in out and "Q2" in out and "P3" not in out


def test_check_gap_without_reason_fails(tmp_path, capsys):
    m = _matrix(tmp_path, [("P", "GAP"), ("P", "GAP:"), ("P", "GAP: no evidence of RPA")])
    assert jm.main(["check", "--matrix", str(m)]) == 1
    out = capsys.readouterr().out
    assert "P1" in out and "P2" in out and "P3" not in out


def test_check_json_and_usage_exit(tmp_path, capsys):
    m = _matrix(tmp_path, [("R", "")])
    assert jm.main(["check", "--matrix", str(m), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["unanswered"][0]["id"] == "R1"
    assert jm.main(["check", "--matrix", str(tmp_path / "missing.yaml")]) == 2


def test_cli_subprocess_usage_error():
    proc = subprocess.run([sys.executable, str(SCRIPTS / "jd_matrix.py")], capture_output=True, text=True)
    assert proc.returncode == 2


def test_phrase_terms_and_filter_lines(tmp_path):
    jd = tmp_path / "jd.md"
    jd.write_text("<untrusted_jd>\nKey responsibilities\n- Diagnose issues in the computer-use agent harness\n"
                  "Minimum education: Bachelor's degree or equivalent\n</untrusted_jd>\n", encoding="utf-8")
    built = jm.build_matrix(jd, title="Engineer", taxonomy_path=TAX, evidence_path=EV, today="2026-10-01")
    diag = next(r for r in built["rows"] if r["text"].startswith("Diagnose"))
    assert diag["type"] == "R" and "computer-use" in diag["terms"]
    assert any(t.startswith("Agent harness") for t in diag["terms"])      # alias, so no bare phrase duplicate
    assert "agent harness" not in diag["terms"]
    filt = next(r for r in built["rows"] if r["text"].startswith("Minimum education"))
    assert filt["type"] == "Q" and filt["heading"] == "filter"


def test_set_changes_our_fields_never_the_employers_words(tmp_path):
    import yaml
    import jd_matrix as jm
    m = tmp_path / "x.matrix.yaml"
    m.write_text(yaml.safe_dump({"title": "t", "rows": [{"id": "R1", "type": "O", "rank": 1, "text": "Own evals",
                                                         "terms": ["evals"], "synonyms": [], "candidate_evidence": ["ev-x"],
                                                         "answer": "", "ceiling": ""}]}), encoding="utf-8")
    assert jm.main(["set", "--matrix", str(m), "--row", "R1", "--type", "R", "--rank", "3", "--answer",
                    "line: Acme #2", "--synonym", "evaluation", "--drop-evidence"]) == 0
    row = yaml.safe_load(m.read_text())["rows"][0]
    assert (row["type"], row["rank"], row["answer"], row["synonyms"], row["candidate_evidence"]) == (
        "R", 3, "line: Acme #2", ["evaluation"], [])
    assert row["text"] == "Own evals" and jm.main(["check", "--matrix", str(m)]) == 0
    assert jm.main(["set", "--matrix", str(m), "--row", "R1", "--answer", "maybe"]) == 2      # not line:, Q<n> or GAP:
    assert jm.main(["set", "--matrix", str(m), "--row", "R9", "--rank", "1"]) == 2


def test_company_boilerplate_ranks_1_wherever_it_sits():
    import jd_matrix as jm
    body = "agents evals agents evals agents"
    assert jm.rank_row("We build agents and evals", 0, 30, "Deployed Engineer", body, [], heading="About Us") == 1
    assert jm.rank_row("We build agents and evals", 0, 30, "Deployed Engineer", body, [], heading="Benefits") == 1
    assert jm.rank_row("Design agents and evals", 0, 30, "Deployed Engineer", body, [], heading="What you'll do") >= 2
