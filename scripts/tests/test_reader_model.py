"""reader_model.py: the Reader model is checked for form before any page is written."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reader_model as rm  # noqa: E402

GOOD = """# Role Package
## 1 Title and lens
AI engineer.
## 6 Reader model
- **Reader:** the applied AI team lead; the hiring manager decides.
- **Wants**
W1 | "ship agents customers rely on" | https://example.com/blog/agents, 2026-08-14
W2 | "evals that catch regressions before release" | https://example.com/talk, 2026-06-02
W3 | "engineers who own the product end to end" | https://example.com/careers, 2026-09-30
- **Fears:** demo-only work.
- **Proof they trust:** shipped product with users.
- **Their words**
agents customers rely on | agent harness in production | ev-acme-005
regressions | regression gates | ev-proj-063
- **Answer table**
W1 | Built the agent harness behind the shipped app | ev-acme-005
W2 | Built the evaluation bench that grades every release candidate | ev-proj-063
W3 | Owned the app end to end | ev-acme-001
## 8 Questions for the owner (at most 5)
None.
## Sources
"""


def write(tmp_path, text, name="pkg.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_complete_reader_model_is_clean(tmp_path):
    res = rm.check(write(tmp_path, GOOD))
    assert res["errors"] == [] and res["wants"] == 3


def test_missing_section_is_an_error(tmp_path):
    res = rm.check(write(tmp_path, "## 1 Title and lens\nx\n## 6 Hiring-manager persona\ny\n"))
    assert res["errors"] and "Reader model" in res["errors"][0]


def test_a_want_without_quote_source_or_date_is_flagged(tmp_path):
    bad = GOOD.replace('W2 | "evals that catch regressions before release" | https://example.com/talk, 2026-06-02',
                       "W2 | evals | the talk")
    errs = rm.check(write(tmp_path, bad))["errors"]
    assert any("W2: the want is not quoted" in e for e in errs)
    assert any("W2: no source URL" in e for e in errs) and any("W2: no date" in e for e in errs)


def test_an_unanswered_want_must_be_asked(tmp_path):
    no_w3 = GOOD.replace("W3 | Owned the app end to end | ev-acme-001\n", "")
    errs = rm.check(write(tmp_path, no_w3))["errors"]
    assert any("W3: no answer row and no question" in e for e in errs)
    asked = no_w3.replace("None.", "1. W3: which product did you own end to end, and where is the proof?")
    res = rm.check(write(tmp_path, asked))
    assert res["errors"] == [] and any("W3: no true line yet" in w for w in res["warnings"])


def test_answer_lines_are_looked_for_on_the_page(tmp_path):
    resume = write(tmp_path, "highlights:\n- Built the agent harness behind the shipped app <!-- ev:ev-acme-005 -->\n",
                   "resume.yaml")
    res = rm.check(write(tmp_path, GOOD), resume)
    assert any(w.startswith("W2: the answer line is not on") for w in res["warnings"])
    assert not any(w.startswith("W1:") for w in res["warnings"])


def test_cli_exit_codes(tmp_path):
    assert rm.main(["check", str(write(tmp_path, GOOD))]) == 0
    assert rm.main(["check", str(write(tmp_path, "## 1 x\n", "bad.md"))]) == 1
    assert rm.main(["check", str(tmp_path / "missing.md")]) == 2


def test_w1_low_on_the_page_is_a_warning(tmp_path):
    import yaml
    low = {"cv": {"sections": {"Experience": [
        {"company": "A", "summary": "x", "highlights": ["Owned the app end to end <!-- ev:ev-acme-001 -->", "y", "z",
                                                        "Built the agent harness behind the shipped app"]},
        {"company": "B", "summary": "q", "highlights": ["r", "s"]}]}}}
    resume = tmp_path / "resume.yaml"
    resume.write_text(yaml.safe_dump(low), encoding="utf-8")
    res = rm.check(write(tmp_path, GOOD), resume)
    assert any(w.startswith("W1: met only below the first 30 seconds") for w in res["warnings"])
    top = {"cv": {"sections": {"Experience": [{"company": "A", "summary": "x",
            "highlights": ["Built the agent harness behind the shipped app", "y"]}]}}}
    resume.write_text(yaml.safe_dump(top), encoding="utf-8")
    assert not any(w.startswith("W1:") for w in rm.check(write(tmp_path, GOOD), resume)["warnings"])


def test_a_want_whose_words_are_nowhere_on_the_page_warns(tmp_path):
    resume = write(tmp_path, "highlights:\n- Built the agent harness behind the shipped app\n- Built the evaluation bench "
                   "that grades every release candidate\n- Owned the app end to end\n", "resume.yaml")
    res = rm.check(write(tmp_path, GOOD), resume)
    assert any(w.startswith("W3: none of the reader's words") for w in res["warnings"])     # "engineers", "product"
    assert not any(w.startswith("W2: none of the reader's words") for w in res["warnings"])  # "release" is there



def test_a_package_over_the_6_kb_form_warns(tmp_path):
    # the package form in roles/role-analyst.md is 12 KB; a larger package warns
    assert not any("12 KB" in w for w in rm.check(write(tmp_path, GOOD))["warnings"])
    assert not any("12 KB" in w for w in rm.check(write(tmp_path, GOOD + "x" * 7000 + "\n", "mid-202610.md"))["warnings"])
    big = write(tmp_path, GOOD + "x" * 13000 + "\n", "big-202610.md")
    assert any("12 KB" in w for w in rm.check(big)["warnings"])
