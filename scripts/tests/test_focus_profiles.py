"""focus_profiles.py: one profile per role focus, picked by title, each with its best foot forward."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import focus_profiles as fp  # noqa: E402

S7 = """## 7. Focus profile: the best foot forward
- Headline: AI Agents | LLM Evaluation
- Vision line (draft for the owner): x
- Page order: x
- Skills rows: x
- Projects to show: x
- Why this role: x
- Story to tell: prep/stories/x.md
- Outreach line: x
- Questions for the owner: none
"""


def lens(family, titles, s7=True):
    body = "".join(f"## {n}. Part\nx\n" for n in range(1, 7))
    fm = f"---\nfamily: {family}\ntitles_covered: {titles}\nstatus: draft\n---\n"
    return fm + body + (S7 if s7 else "")


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "profile" / "lenses"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# lenses\n", encoding="utf-8")
    (d / "agent-harness.md").write_text(lens("agent-harness", '["Software Engineer, Agent Harness", "Agent Platform Engineer"]'), encoding="utf-8")
    (d / "evaluation.md").write_text(lens("evaluation", '["*Evaluation*", "Eval Engineer"]', s7=False), encoding="utf-8")
    return tmp_path


def test_pick_by_glob_then_words(root):
    assert fp.pick(root, "Research Engineer, Model Evaluation")[0]["family"] == "evaluation"
    top = fp.pick(root, "Senior Agent Platform Engineer")
    assert top[0]["family"] == "agent-harness"
    assert fp.pick(root, "Accountant") == []


def test_check_reports_a_missing_best_foot(root):
    gaps = fp.check(root)
    assert gaps == ["evaluation: missing section(s) 7"]


def test_check_reports_missing_items(root):
    p = root / "profile" / "lenses" / "agent-harness.md"
    p.write_text(p.read_text(encoding="utf-8").replace("- Outreach line: x\n", ""), encoding="utf-8")
    assert "agent-harness: section 7 has no 'outreach' item" in fp.check(root)


def test_cli(root, capsys):
    assert fp.main(["--root", str(root), "pick", "Agent Platform Engineer"]) == 0
    assert "agent-harness" in capsys.readouterr().out
    assert fp.main(["--root", str(root), "check"]) == 1


def test_a_prefixed_title_still_matches(root):
    top = fp.pick(root, "Staff Software Engineer, Agent Harness")
    assert top[0]["family"] == "agent-harness" and top[0]["score"] == 1.5


def test_a_secondary_focus_needs_its_domain_word(root):
    d = root / "profile" / "lenses"
    (d / "design-automation.md").write_text(lens("design-automation", '["Applied AI Engineer, Silicon Engineering"]')
        .replace("status: draft", 'status: draft\nsecondary: true\nrequires_any: ["silicon", "chip", "eda"]'), encoding="utf-8")
    (d / "ai-engineer.md").write_text(lens("ai-engineer", '["AI Engineer*", "Applied AI Engineer"]'), encoding="utf-8")
    assert all(r["family"] != "design-automation" for r in fp.pick(root, "Software Engineer, Applied AI"))
    assert fp.pick(root, "Applied AI Engineer, Silicon Engineering")[0]["family"] == "design-automation"


# At scale: role_taxonomy.py was slow on thousands of titles because pick re-read every lens per title. The lenses are read once per process; a changed or added lens file is read again (its mtime or size moved).
def test_pick_reads_each_lens_file_once_across_many_titles(root, monkeypatch):
    fp._LENS_CACHE.clear()
    reads = []
    real = Path.read_text

    def counting(self, *a, **k):
        if self.parent.name == "lenses":
            reads.append(self.name)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", counting)
    for _ in range(50):
        fp.pick(root, "Software Engineer, Agent Harness")
        fp.pick(root, "Eval Engineer")
    assert sorted(reads) == ["agent-harness.md", "evaluation.md"]


def test_a_changed_or_added_lens_is_read_again(root):
    fp._LENS_CACHE.clear()
    assert fp.pick(root, "Voice Agent Engineer") == []
    d = root / "profile" / "lenses"
    (d / "voice.md").write_text(lens("voice-and-realtime-agents", '["Voice Agent Engineer"]'), encoding="utf-8")
    assert fp.pick(root, "Voice Agent Engineer")[0]["family"] == "voice-and-realtime-agents"
    (d / "voice.md").write_text(lens("voice-and-realtime-agents", '["Realtime Voice Engineer", "Speech Engineer"]'),
                                encoding="utf-8")
    assert fp.pick(root, "Speech Engineer")[0]["family"] == "voice-and-realtime-agents"


def test_callers_cannot_change_the_cache(root):
    fp._LENS_CACHE.clear()
    first = fp.lenses(root)
    first[0]["family"] = "tampered"
    first[0]["titles"].append("Tampered Title")
    again = fp.lenses(root)
    assert again[0]["family"] != "tampered" and "Tampered Title" not in again[0]["titles"]


def test_a_specialist_word_outranks_a_generic_title_match(tmp_path):
    """A specialist word: "ML Engineer - Automated Evaluation" is an evaluation seat before it is an ML seat."""
    lenses = tmp_path / "profile" / "lenses"
    lenses.mkdir(parents=True)
    body = "\n".join(f"## {n}. s\nx" for n in range(1, 8))
    (lenses / "applied-ml.md").write_text('---\nfamily: applied-ml\ntitles_covered: ["ML Engineer*"]\nstatus: draft\n---\n'
                                          + body, encoding="utf-8")
    (lenses / "evaluation.md").write_text('---\nfamily: evaluation\ntitles_covered: ["Evals Engineer"]\n'
                                          'specialist_words: ["evaluation", "evals"]\nstatus: draft\n---\n' + body,
                                          encoding="utf-8")
    picks = fp.pick(tmp_path, "ML Engineer - Automated Evaluation and Adversarial Design")
    assert [p["family"] for p in picks][:2] == ["evaluation", "applied-ml"]
    assert fp.pick(tmp_path, "ML Engineer, Ranking")[0]["family"] == "applied-ml"
