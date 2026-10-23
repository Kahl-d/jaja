"""model_policy.py: every card runs on the model and effort of its kind of work."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_policy as mp  # noqa: E402

POLICY = """# p
```yaml
kinds:
  classify: {model: haiku, effort: low, why: x}
  judge: {model: opus, effort: high, why: y}
```
```yaml
roles: {sorter: classify, gate: judge, reader: judge}
lanes: {recruiter: judge}
exceptions:
  reader: {effort: medium, why: z}
```
"""


def card(root, rel, model, effort):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {p.stem}\nmodel: {model}\neffort: {effort}\n---\n# x\n", encoding="utf-8")


def tree(tmp_path):
    (tmp_path / "policy.md").write_text(POLICY, encoding="utf-8")
    card(tmp_path, "agents/sorter.md", "haiku", "low")
    card(tmp_path, "agents/gate.md", "opus", "high")
    card(tmp_path, "agents/reader.md", "opus", "medium")
    card(tmp_path, "lanes/recruiter/AGENT.md", "opus", "high")
    return tmp_path


def test_a_tree_that_follows_the_policy_and_its_exceptions_is_clean(tmp_path):
    root = tree(tmp_path)
    assert mp.check(root, root / "policy.md") == []


def test_a_drifted_card_and_an_unnamed_card_are_findings(tmp_path):
    root = tree(tmp_path)
    card(root, "agents/gate.md", "sonnet", "high")
    card(root, "agents/newcomer.md", "opus", "high")
    found = mp.check(root, root / "policy.md")
    assert any("gate.md: model sonnet, policy opus" in f for f in found)
    assert any("newcomer.md: not in the policy" in f for f in found)


def test_the_real_tree_follows_the_real_policy():
    assert mp.check() == []
    assert mp.main(["pick", "research"]) == 0
