"""prep_focus.py (prep lane): one command gives the first prep pack for any screen: the focus picked on the job
title, the profile's story (section 7), ten bank questions for that focus, and the Reader model wants when present."""
from __future__ import annotations

import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import prep_focus as pf  # noqa: E402

LENS = """---
family: {family}
titles_covered: {titles}
status: draft
---
## 1. Part
x
## 7. Focus profile: the best foot forward

**Story to tell.** prep/stories/{story}.md first; prep/stories/watcher.md as the proof.

**Questions for the owner.** none
"""
STORY = "# Story\n\n**One line (30 seconds).** I built the {name}, and it held up on real prompts.\n"
PACKAGE = """# Pkg
## 5 Vocabulary
x
## 6 Reader model
- **Reader:** the head of agent quality.
- **Wants** (ranked):
- W1 | "evals built from real user prompts" | https://example.com/post, 2026-09-12
- W2 | "a judge you checked" | https://example.com/talk, 2026-08-30
- **Fears:** benchmark-only evals.
## 7 Angle and lead bullet
x
"""


def q(i, topic, cluster="llm-agent-system-design", **kw):
    return {"id": f"q-{i:03d}", "q": f"question {i} on {topic}", "cluster": cluster, "topic": topic, "source": "s",
            "url": "https://example.com", "read": "2026-10-01", **kw}


def world(root, package=True, title="Agent Evaluation Engineer"):
    (root / "profile" / "lenses").mkdir(parents=True)
    (root / "profile" / "lenses" / "evaluation.md").write_text(
        LENS.format(family="evaluation", titles='["Agent Evaluation Engineer", "Evals Engineer*"]', story="evaluation-bench"))
    (root / "profile" / "lenses" / "ml-infra.md").write_text(
        LENS.format(family="ml-infra", titles='["ML Platform Engineer"]', story="watcher"))
    (root / "prep" / "stories").mkdir(parents=True)
    (root / "prep" / "stories" / "evaluation-bench.md").write_text(STORY.format(name="evaluation bench"))
    (root / "prep" / "stories" / "watcher.md").write_text(STORY.format(name="Watcher"))
    qs = ([q(i, "evals", "evals") for i in range(1, 9)] + [q(i, "system-design") for i in range(9, 15)]
          + [q(i, "agents") for i in range(15, 20)] + [q(i, "behavioral", "behavioral") for i in range(20, 24)]
          + [q(i, "fundamentals", "ml-fundamentals") for i in range(24, 30)]
          + [q(30, "fundamentals", "ml-fundamentals", focus=["evaluation"]),                 # tagged for the focus
             q(31, "evals", "evals", provenance="reported", company_loops=["Quillon"])])      # reported in this loop
    (root / "prep" / "questions.yaml").write_text(yaml.safe_dump({"schema": "prep-questions/1", "questions": qs}))
    slug = "quillon-agent-evaluation-engineer-202610"
    app = root / "applications" / slug
    app.mkdir(parents=True)
    (app / "application.md").write_text(f"---\ntype: application\nid: {slug}\ncompany: Quillon\ncompany_slug: quillon\n"
                                        f"title: {title}\nstage: screen\n---\n# app\n")
    if package:
        (root / "research" / "targets").mkdir(parents=True)
        (root / "research" / "targets" / f"{slug}.md").write_text(PACKAGE)
    return slug


def test_focus_story_wants_and_ten_questions(root):
    slug = world(root)
    pack = pf.build(root, slug)
    assert pack["title"] == "Agent Evaluation Engineer" and pack["focus"] == "evaluation"
    assert pack["stories"][0]["path"] == "prep/stories/evaluation-bench.md"
    assert "evaluation bench" in pack["stories"][0]["one_line"]
    assert [w["id"] for w in pack["wants"]] == ["W1", "W2"] and "real user prompts" in pack["wants"][0]["want"]
    ids = [x["id"] for x in pack["questions"]]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert ids[0] == "q-030"                       # tagged for the focus first
    assert ids[1] == "q-031"                       # then a question reported in this company's loop
    topics = {x["topic"] for x in pack["questions"]}
    assert {"evals", "system-design", "agents", "behavioral"} <= topics


def test_no_package_and_no_matching_focus_are_said_not_hidden(root, capsys):
    slug = world(root, package=False, title="Robot Controls Engineer")
    pack = pf.build(root, slug)
    assert pack["focus"] is None and pack["wants"] == [] and len(pack["questions"]) == 10
    assert pf.main(["--root", str(root), slug]) == 0
    out = capsys.readouterr().out
    assert "focus: none" in out and "Reader model: none" in out and "\u2014" not in out


def test_unknown_slug_exits_1_and_cli_is_read_only(root, capsys):
    slug = world(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert pf.main(["--root", str(root), slug]) == 0
    out = capsys.readouterr().out
    assert "focus: evaluation" in out and "W1" in out and "prep/stories/evaluation-bench.md" in out and out.count("\n  q-") == 10
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert pf.main(["--root", str(root), "no-such-slug"]) == 1


def test_the_topic_map_is_a_mapping_of_families_to_topic_lists():
    assert pf.FOCUS_TOPICS["evaluation"][0] == "evals" and pf.DEFAULT_TOPICS
    assert all(isinstance(v, list) and v and all(isinstance(t, str) for t in v) for v in pf.FOCUS_TOPICS.values())


def test_unmapped_names_the_lens_families_with_no_topic_map(root):
    world(root)
    (root / "profile" / "lenses" / "robotics.md").write_text(LENS.format(family="robotics", titles='["Robot Engineer"]', story="watcher"))
    assert pf.unmapped(root) == ["robotics"]
