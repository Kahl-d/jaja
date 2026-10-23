"""rubric_check.py: the deterministic half of the resume rubric (R3 section B: B3 B4 B5 B7 B9 B12 B13 B14 B18).

One pass and one fail per predicate on small inline resumes (fictional-shaped, same YAML shape as
resumes/bases/general/resume.yaml), the allow-long exemption, the changed_bullets diff against the base,
exit codes, the JSON shape and em-dash rejection (in the resume, and never in the report itself).
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
import rubric_check as rc  # noqa: E402

EM_DASH = chr(0x2014)

JD = """\
---
title: Product Engineer
---
<untrusted_jd>
Minimum qualifications
- Hands-on experience building with LLM APIs or agent frameworks
- Strong full-stack engineering skills
Preferred qualifications
- Experience building evals or quality harnesses for machine learning systems
</untrusted_jd>
"""

B1 = ("Designed and built the agent harness (Python, Claude Agent SDK) that plans and simulates RF designs "
      "safely. <!-- ev:ev-a-001,ev-a-002 -->")
B2 = ("Made agent quality measurable with an evaluation bench: an LLM judge scored each run on an RF rubric. "
      "<!-- ev:ev-a-003 -->")
B3 = "Built the Watcher, a developer tool that drove the running app through the Chrome DevTools Protocol. <!-- ev:ev-a-004 -->"
X1 = "Turned the chatbot into a voice agent on Contoso at 420 ms p95 voice-to-voice with React in front. <!-- ev:ev-x-001 -->"
X2 = "Gated each release on a golden task set graded by an LLM judge. <!-- ev:ev-x-002 -->"


def resume(bullets_a=(B1, B2, B3), bullets_b=(X1, X2), skills=None, extra_roles=()):
    skills = skills if skills is not None else [
        {"label": "Agent systems", "details": "agent harness (Claude Agent SDK), LLM judge, React <!-- ev:ev-a-001 -->"}]
    roles = [
        {"company": "Acme", "position": "Founding Engineer", "start_date": "2026-02", "end_date": "present",
         "highlights": list(bullets_a)},
        {"company": "Beta", "position": "AI Engineer", "start_date": "2025-08", "end_date": "2025-12",
         "highlights": list(bullets_b)},
    ] + list(extra_roles)
    return {"cv": {"name": "Test Person", "sections": {"Skills": skills, "Experience": roles}}}


def matrix(rows=None, allowed=()):
    rows = rows if rows is not None else [
        {"id": "Q1", "type": "Q", "rank": 3, "text": "Hands-on experience building with LLM APIs or agent frameworks",
         "terms": ["Claude Agent SDK"], "synonyms": [], "answer": "line: Acme bullet 1"},
        {"id": "P1", "type": "P", "rank": 2, "text": "Experience building evals", "terms": ["LLM judge"],
         "synonyms": [], "answer": "line: Acme bullet 2"},
    ]
    return {"jd": "jd.md", "title": "Product Engineer", "built": "2026-10-01", "allowed_ngrams": list(allowed),
            "rows": rows}


@pytest.fixture
def files(tmp_path):
    def write(variant, base=None, mat=None, jd=JD):
        paths = {}
        for name, data in (("resume", variant), ("base", base if base is not None else resume())):
            p = tmp_path / f"{name}.yaml"
            p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            paths[name] = p
        m = tmp_path / "matrix.yaml"
        m.write_text(yaml.safe_dump(mat if mat is not None else matrix(), sort_keys=False, allow_unicode=True),
                     encoding="utf-8")
        paths["matrix"] = m
        j = tmp_path / "jd.md"
        j.write_text(jd, encoding="utf-8")
        paths["jd"] = j
        return paths
    return write


def run(paths, *extra):
    return rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], **_kw(extra))


def _kw(extra):
    kw = {}
    for k, v in extra:
        kw[k] = v
    return kw


def status(report, pid):
    return next(p for p in report["predicates"] if p["id"] == pid)["status"]


def findings(report, pid):
    return next(p for p in report["predicates"] if p["id"] == pid)["findings"]


# --------------------------------------------------------------------------- B3
def test_b3_pass_and_fail(files):
    assert status(run(files(resume())), "B3") == "PASS"
    rows = matrix()["rows"]
    rows[0]["terms"] = ["Kubernetes"]
    rep = run(files(resume(), mat=matrix(rows)))
    assert status(rep, "B3") == "FAIL" and "Kubernetes" in findings(rep, "B3")[0]["detail"]


def test_b3_synonym_satisfies_a_term(files):
    rows = matrix()["rows"]
    rows[0]["terms"] = ["Kubernetes"]
    rows[0]["synonyms"] = ["Chrome DevTools Protocol"]
    assert status(run(files(resume(), mat=matrix(rows))), "B3") == "PASS"


def test_b3_uses_pdf_text_when_given(files, tmp_path):
    rows = matrix()["rows"]
    rows[0]["terms"] = ["Kubernetes"]
    pdf = tmp_path / "page.txt"
    pdf.write_text("Ran Kubernetes clusters.\n", encoding="utf-8")
    assert status(run(files(resume(), mat=matrix(rows)), ("pdf_text", pdf)), "B3") == "PASS"


# --------------------------------------------------------------------------- B4
def test_b4_pass_and_warn(files):
    assert status(run(files(resume())), "B4") == "PASS"
    skills = [{"label": "Agent systems", "details": "agent harness, Kubernetes, React"}]
    rep = run(files(resume(skills=skills)))
    assert status(rep, "B4") == "WARN"
    assert [f["detail"] for f in findings(rep, "B4")] and "Kubernetes" in findings(rep, "B4")[0]["detail"]


# --------------------------------------------------------------------------- B5
def test_b5_pass_and_fail(files):
    assert status(run(files(resume())), "B5") == "PASS"
    rows = matrix()["rows"]
    rows[1]["answer"] = ""
    rep = run(files(resume(), mat=matrix(rows)))
    assert status(rep, "B5") == "FAIL" and "P1" in findings(rep, "B5")[0]["detail"]


# --------------------------------------------------------------------------- B7
def test_b7_pass_and_warn(files):
    assert status(run(files(resume())), "B7") == "PASS"
    long = ("Built and shipped and measured the harness and the bench and the studio and the watcher and the "
            "marketplace and the corpus and the memory over four public releases in one year of work. <!-- ev:ev-l-001 -->")
    rep = run(files(resume(bullets_a=(long, B2))))
    assert status(rep, "B7") == "WARN"
    semi = "Built the harness; designed the bench. <!-- ev:ev-s-001 -->"
    rep = run(files(resume(bullets_a=(semi, B2))))
    assert status(rep, "B7") == "WARN" and "semicolon" in findings(rep, "B7")[0]["detail"]


def test_b7_allow_long_exempts_by_ev_id_set(files):
    long = ("Owned the desktop app end to end from design to production releases: chose with RF engineers what to "
            "automate, then shipped the workflow builder and marketplace and four public versions of the app. "
            "<!-- ev:ev-l-001,ev-l-002 -->")
    rep = run(files(resume(bullets_a=(long, B2))), ("allow_long", ["ev-l-001,ev-l-002"]))
    assert status(rep, "B7") == "PASS"
    rep = run(files(resume(bullets_a=(long, B2))), ("allow_long", ["ev-l-009"]))
    assert status(rep, "B7") == "WARN"


# --------------------------------------------------------------------------- B9
def test_b9_pass_and_fail(files):
    assert status(run(files(resume())), "B9") == "PASS"          # 420 ms p95 carries a unit
    bare = "Cut ingestion latency by 30% and improved throughput. <!-- ev:ev-n-001 -->"
    rep = run(files(resume(bullets_a=(bare, B2))))
    assert status(rep, "B9") == "FAIL" and "30%" in findings(rep, "B9")[0]["detail"]
    baseline = "Cut ingestion latency from 2 s to 600 ms. <!-- ev:ev-n-002 -->"
    assert status(run(files(resume(bullets_a=(baseline, B2)))), "B9") == "PASS"
    counted = "Assembled 17,622 essay sentences labeled for eight themes. <!-- ev:ev-n-003 -->"
    assert status(run(files(resume(bullets_a=(counted, B2)))), "B9") == "PASS"


# --------------------------------------------------------------------------- B12
def test_b12_pass_and_fail(files):
    assert status(run(files(resume())), "B12") == "PASS"
    duty = "Responsible for the agent harness and its tools. <!-- ev:ev-d-001 -->"
    rep = run(files(resume(bullets_a=(duty, B2))))
    assert status(rep, "B12") == "FAIL" and "Responsible for" in findings(rep, "B12")[0]["line"]


# --------------------------------------------------------------------------- B13
def test_b13_pass_and_fail(files):
    assert status(run(files(resume())), "B13") == "PASS"
    slop = "Spearheaded a comprehensive harness to leverage insights. <!-- ev:ev-s-001 -->"
    rep = run(files(resume(bullets_a=(slop, B2))))
    assert status(rep, "B13") == "FAIL"
    hits = " ".join(f["detail"] for f in findings(rep, "B13"))
    assert "spearheaded" in hits.lower() and "comprehensive" in hits.lower() and "leverage" in hits.lower()


def test_b13_em_dash_fails_and_report_has_none(files, capsys):
    dash = f"Built the harness {EM_DASH} the tools reject a bad call. <!-- ev:ev-e-001 -->"
    paths = files(resume(bullets_a=(dash, B2)))
    rep = run(paths)
    assert status(rep, "B13") == "FAIL" and rep["exit"] == 1
    text = rc.render_text(rep)
    assert EM_DASH not in text and "em-dash" in text.lower()
    assert EM_DASH not in json.dumps(rep, ensure_ascii=False)


# --------------------------------------------------------------------------- B14
def test_b14_pass_and_warn_same_counts(files):
    assert status(run(files(resume())), "B14") == "PASS"         # 3 and 2 bullets, two roles
    third = {"company": "Kappa", "position": "Analyst", "start_date": "2021-01", "end_date": "2022-09",
             "highlights": ["Led three people on ETL for 10M records. <!-- ev:ev-g-001 -->",
                            "Mapped HL7 into one model. <!-- ev:ev-g-002 -->"]}
    rep = run(files(resume(bullets_a=(B1, B2), extra_roles=[third])))
    assert status(rep, "B14") == "WARN" and "same bullet count" in findings(rep, "B14")[0]["detail"]


def test_b14_warn_adjacent_first_word(files):
    b = "Built the second thing with the same opener. <!-- ev:ev-f-001 -->"
    rep = run(files(resume(bullets_a=(B3, b))))
    assert status(rep, "B14") == "WARN" and "Built" in findings(rep, "B14")[0]["detail"]


# --------------------------------------------------------------------------- B18
def test_b18_pass_fail_and_allowed_ngrams(files):
    assert status(run(files(resume())), "B18") == "PASS"
    pasted = "Hands-on experience building with LLM APIs or agent frameworks at Acme. <!-- ev:ev-p-001 -->"
    rep = run(files(resume(bullets_a=(pasted, B2))))
    assert status(rep, "B18") == "FAIL"
    assert "experience building with llm apis or" in findings(rep, "B18")[0]["detail"]
    allowed = ["hands on experience building with llm", "on experience building with llm apis",
               "experience building with llm apis or", "building with llm apis or agent",
               "with llm apis or agent frameworks"]
    rep = run(files(resume(bullets_a=(pasted, B2)), mat=matrix(allowed=allowed)))
    assert status(rep, "B18") == "PASS"


# --------------------------------------------------------------------------- changed bullets, exit, JSON, CLI
def test_changed_bullets_diff_against_base(files):
    new = "Added per-user memory to the agent. <!-- ev:ev-c-001 -->"
    paths = files(resume(bullets_a=(B1, new, B3)), base=resume())
    rep = run(paths)
    changed = rep["changed_bullets"]
    assert len(changed) == 1 and changed[0]["text"].startswith("Added per-user memory")
    assert changed[0]["role"] == "Acme / Founding Engineer"
    assert rep["removed_bullets"][0]["text"].startswith("Made agent quality measurable")
    assert run(files(resume()))["changed_bullets"] == []


def test_exit_codes_and_json_shape(files, capsys):
    paths = files(resume())
    args = ["--resume", str(paths["resume"]), "--base", str(paths["base"]), "--matrix", str(paths["matrix"]),
            "--jd", str(paths["jd"])]
    assert rc.main(args) == 0
    text = capsys.readouterr().out
    assert "B3" in text and "changed bullets" in text.lower()
    assert rc.main(args + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"resume", "base", "matrix", "jd", "date", "predicates", "changed_bullets", "removed_bullets", "exit"} <= set(data)
    assert [p["id"] for p in data["predicates"]] == ["B3", "B4", "B5", "B7", "B9", "B12", "B13", "B14", "B18", "B19", "B21", "B22"]
    assert all({"id", "status", "findings"} <= set(p) for p in data["predicates"])
    duty = "Responsible for the harness. <!-- ev:ev-d-001 -->"
    paths = files(resume(bullets_a=(duty, B2)))
    assert rc.main(["--resume", str(paths["resume"]), "--base", str(paths["base"]), "--matrix", str(paths["matrix"]),
                    "--jd", str(paths["jd"])]) == 1
    assert rc.main(["--resume", str(paths["resume"] / "nope"), "--base", str(paths["base"]), "--matrix",
                    str(paths["matrix"]), "--jd", str(paths["jd"])]) == 2


def test_cli_subprocess_usage_error():
    proc = subprocess.run([sys.executable, str(SCRIPTS / "rubric_check.py")], capture_output=True, text=True)
    assert proc.returncode == 2


def test_b4_plural_matches_either_way(files):
    skills = [{"label": "Agent systems", "details": "agent harnesses, LLM judges, React"}]
    rep = run(files(resume(skills=skills)))
    assert status(rep, "B4") == "PASS" and rep["predicates"][1]["notes"] == []


# --------------------------------------------------------------------------- base lines are approved
def test_a_base_line_only_warns_and_a_changed_line_fails(files):
    bare = "Cut ingestion latency by 30% and improved throughput. <!-- ev:ev-n-001 -->"
    page = resume(bullets_a=(bare, B2))
    rep = run(files(page, base=page))                 # the line is on the base: the owner approved it
    assert status(rep, "B9") == "WARN" and rep["exit"] == 0
    assert any("base" in n for n in next(p for p in rep["predicates"] if p["id"] == "B9")["notes"])
    rep = run(files(page))                             # the variant introduced it: a regression
    assert status(rep, "B9") == "FAIL" and rep["exit"] == 1


# --------------------------------------------------------------------------- B19 NDA
def test_b19_nda_terms_fail_on_the_page(files, tmp_path, monkeypatch):
    assert status(run(files(resume())), "B19") == "PASS"
    leak = "Built the Zephyr test bench for the RF team. <!-- ev:ev-n-001 -->"
    rep = run(files(resume(bullets_a=(leak, B2))))
    assert status(rep, "B19") == "FAIL" and "zephyr" in findings(rep, "B19")[0]["detail"].lower()
    holds = tmp_path / "private-holds.txt"
    holds.write_text("# nda\nSecretCo\n", encoding="utf-8")
    monkeypatch.setattr(rc, "PRIVATE_HOLDS", holds)
    leak2 = "Shipped the SecretCo integration. <!-- ev:ev-n-002 -->"
    rep = run(files(resume(bullets_a=(leak2, B2))))
    assert status(rep, "B19") == "FAIL"


# --------------------------------------------------------------------------- mass tier
def test_mass_tier_makes_unanswered_rows_a_warning_but_keeps_b3(files):
    m = matrix()
    for r in m["rows"]:
        r["answer"] = ""
    paths = files(resume(), mat=m)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier="mass")
    assert status(rep, "B5") == "WARN"
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"])
    assert status(rep, "B5") == "FAIL"


# --------------------------------------------------------------------------- B3 calibration (a live mass run)
def _q(text, terms, synonyms=()):
    return {"id": "Q1", "type": "Q", "rank": 3, "text": text, "terms": list(terms), "synonyms": list(synonyms),
            "answer": "line: x"}


def _b3(files, rows, page=None):
    m = matrix()
    m["rows"] = rows
    return run(files(page or resume(), mat=m))


def test_b3_reads_skills_labels(files):
    page = resume(skills=[{"label": "Machine learning and NLP", "details": "PyTorch <!-- ev:ev-s-1 -->"}])
    rep = _b3(files, [_q("Bachelor's in CS or a related field with machine learning", ["machine learning"])], page)
    assert status(rep, "B3") == "PASS"


def test_b3_an_or_list_is_met_by_any_one(files):
    rep = _b3(files, [_q("3 years programming with Python, Go, Scala, CUDA, or Java", ["Python", "Go", "Scala", "CUDA", "Java"])])
    assert status(rep, "B3") == "PASS"


def test_b3_ignores_soft_lowercase_phrases(files):
    rep = _b3(files, [_q("Comfortable in fast-paced, high-stakes environments", ["fast-paced", "high-stakes"])])
    assert status(rep, "B3") == "PASS"


def test_b3_a_long_taxonomy_name_matches_by_its_head(files):
    rep = _b3(files, [_q("Experience with LLMs and AI agents",
                         ["Agent harness engineering (modes, permission callbacks, hooks, gating)"])])
    assert status(rep, "B3") == "PASS"


def test_b3_a_required_tool_alone_still_fails(files):
    rep = _b3(files, [_q("Production experience with Kubernetes", ["Kubernetes"])])
    assert status(rep, "B3") == "FAIL"


def test_b3_a_taxonomy_skill_is_met_by_its_evidence_on_the_page(files, tmp_path, monkeypatch):
    tax = tmp_path / "taxonomy.yaml"
    tax.write_text("groups:\n  eval:\n    - {skill: Log correlation and failure detectors, evidence: [ev-a-004]}\n",
                   encoding="utf-8")
    monkeypatch.setattr(rc, "TAXONOMY", tax)
    rep = _b3(files, [_q("Debug failures across logs in production", ["Log correlation and failure detectors"])])
    assert status(rep, "B3") == "PASS"          # B3's Watcher bullet carries ev-a-004


def test_b21_governed_employer_numbers_follow_the_ledger(tmp_path, monkeypatch):
    """A measured, released ledger row passes; a target FAILs; a number the ledger lacks FAILs too (an
    eval page carried "84 commands, 17 subagents, 22 hooks" past a WARN)."""
    import numbers_ledger
    led = tmp_path / "numbers-ledger.md"
    led.write_text("## Ledger table\n| N-id | number | metric | kind | conditions | source | status |\n"
                   "| NUM-01 | 10 s to 3.8 s | warm-up | measured | dev build | ev-ps-057 | released |\n"
                   "| NUM-02 | 8-12 s to under 5 s | latency | target | checklist | ev-dfl-032, C64 | held |\n", encoding="utf-8")
    monkeypatch.setattr(numbers_ledger, "LEDGER", led)
    monkeypatch.setattr(numbers_ledger, "EVIDENCE", tmp_path / "no-evidence.yaml")   # no discretion data: every id is resume-ok

    def page(line):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.safe_dump({"cv": {"sections": {"Experience": [
            {"company": "Acme Studio", "highlights": [line + " <!-- ev:ev-ps-057 -->"]}]}}}), encoding="utf-8")
        return p
    assert rc.b21(page("Cut warm-up from 10 s to 3.8 s"))["status"] == "PASS"
    assert rc.b21(page("Cut latency from 8-12s to under 5s"))["status"] == "FAIL"
    unk = rc.b21(page("Streamed at 60 fps"))
    assert unk["status"] == "FAIL" and "not in the ledger" in unk["findings"][0]["detail"]


def test_b9_reads_n_of_m_and_count_nouns_as_measured():
    assert rc.bare_numbers("The judge matched a grader on 55 of 56 criteria and fixed 12 defects") == []
