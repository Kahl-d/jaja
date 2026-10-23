"""apply_pack.py reads the employer's candidate AI policy before any essay (charter section 7). The policy lives in targets/companies.yaml `ai_policy` (or companies/<slug>.md frontmatter, which wins):
{binds: yes|no, rule, url, quote, read_on}. A pack that carries an essay shows the policy (or says it is not recorded);
a binding policy blocks an agent-drafted cover or why-us until the owner's own first draft is on record.
Fictional company (Acme) from test_apply_pack's tree; nothing real is read."""
from __future__ import annotations

import pytest
import yaml

from test_apply_pack import SLUG, V, build, make_tree, pack, section, set_front, write


@pytest.fixture
def js(tmp_path):
    return make_tree(tmp_path / "job-search")

BINDS = {"binds": "yes", "rule": "the candidate writes the first draft; AI may refine it",
         "url": "https://acme.example.com/candidate-ai-guidance", "quote": "create your first draft yourself",
         "read_on": "2026-10-06"}


def set_policy(root, policy, where="targets"):
    if where == "targets":
        write(root / "targets/companies.yaml", yaml.safe_dump(
            {"companies": [{"name": "Acme Robotics", "slug": "acme", "tier": "good", "ats_vendor": "greenhouse",
                            "ai_policy": policy}]}, sort_keys=False))
    else:
        write(root / "companies/acme.md", "---\n" + yaml.safe_dump({"type": "company", "slug": "acme",
                                                                     "ai_policy": policy}) + "---\n# Acme\n")


def test_a_pack_with_essays_and_no_policy_says_it_is_not_recorded(js):
    assert build(js) == 0
    _, body, _ = pack(js)
    assert "not recorded: read the employer's candidate AI guidance" in section(body, "Candidate AI policy")


def test_a_binding_policy_blocks_agent_drafted_essays(js):
    set_policy(js, BINDS)
    assert build(js) == 1
    meta, body, _ = pack(js)
    assert any("candidate AI policy binds" in b and "first_draft: owner" in b for b in meta["blockers"])
    part = section(body, "Candidate AI policy")
    assert "| Binds essays | yes |" in part and "acme.example.com/candidate-ai-guidance" in part
    assert body.index("## Candidate AI policy") < body.index("## Resume, cover letter and why-us")


def test_the_owners_first_draft_on_record_clears_the_block(js):
    set_policy(js, BINDS, where="dossier")
    set_front(js / f"applications/{SLUG}/application.md", first_draft="owner")
    assert build(js) == 0
    assert "companies/acme.md ai_policy" in section(pack(js)[1], "Candidate AI policy")


def test_a_policy_that_does_not_bind_never_blocks(js):
    set_policy(js, dict(BINDS, binds="no", rule="no rule on AI help"))
    assert build(js) == 0


def test_no_essay_and_no_policy_adds_nothing(js):
    (js / V / "cover.md").unlink()
    (js / V / "why-us.md").unlink()
    assert build(js) == 0
    assert "## Candidate AI policy" not in pack(js)[1]
