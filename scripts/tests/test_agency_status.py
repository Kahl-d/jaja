"""agency_status.py: the agency's health in at most 15 lines for the daily brief: clock, funnel, funnel by tier
(tiers come from the apply-batch sheets), queue depth, today's queue, follow-ups due, routines' receipts, lint and sync (from the latest nightly-sweep receipt, or --live). Read only."""
from __future__ import annotations

from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import agency_status as ag  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

TODAY = date(2026, 10, 5)


def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _app(js, slug, events):
    lines = "\n".join(tr.format_event(at, t, "manual", 1.0, "e") for at, t in events)
    _w(js / "applications" / slug / "application.md",
       fm.dump({"id": slug, "company": slug.split("-")[0].title(), "title": "AI Engineer"}, f"# x\n\n## Events\n{lines}\n"))


def tree(tmp_path):
    os_root = tmp_path / "os"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    _app(js, "acme-ai-202610", [("2026-10-03T17:00:00Z", "applied")])
    _app(js, "beta-ai-202610", [("2026-10-03T17:00:00Z", "applied"), ("2026-10-04T17:00:00Z", "screen")])
    _app(js, "delta-ai-202610", [("2026-10-02T17:00:00Z", "tailored")])
    _w(js / "state" / "apply-batches" / "2026-10-03-1.md",
       fm.dump({"type": "apply-batch", "date": "2026-10-03", "tier": "mass", "slugs": ["acme-ai-202610", "delta-ai-202610"],
                "counts": {"roles": 2, "ready": 2, "blocked": 0, "lint": 0, "refused": 0, "base_72": 1}}, "# batch\n"))
    _w(js / "state" / "apply-batches" / "2026-10-04-1.md",
       fm.dump({"type": "apply-batch", "date": "2026-10-04", "tier": "dream", "slugs": ["beta-ai-202610"],
                "counts": {"roles": 1, "ready": 1, "blocked": 0, "lint": 0, "refused": 0, "base_72": 0}}, "# batch\n"))
    _w(js / "hub" / "APPLY-QUEUE.md", "# Retired queue\n\n## READY (now)\n| # | role |\n|---|---|\n| 1 | stale |\n")
    _w(js / "hub" / "NEXT-UP.md", "# NEXT-UP\n\n## The list (top first)\n\n"
                                  "| # | role | fit | why here | chance | posted | state | page | tier |\n|---|---|---|---|---|---|---|---|---|\n"
                                  "| 1 | a | 4.5 | w | c | p | READY | x | dream |\n| 2 | b | 4.4 | w | c | p | READY (APPLY, ab12) | x | focused |\n"
                                  "| 3 | c | 4.2 | w | c | p | PACKAGE | | focused |\n| 4 | d | 4.1 | w | c | p | BLOCKED: years | | focused |\n\n"
                                  "## Submitted\n| # | role |\n|---|---|\n| 9 | z |\n")
    _w(js / "hub" / "lanes" / "SETUP-H1.md", "# H1\n\n### 2026-10-04T10:00:00Z · CHANGE SET 7 · to: SETUP · x\n")
    _w(js / "hub" / "lanes" / "SETUP-H2.md", "# H2\n\n### 2026-10-05T09:00:00Z · CHANGE SET 15 · to: SETUP · y\n"
                                            "### 2026-10-05T09:30:00Z · CORRECTION to CHANGE SET 15 · to: SETUP\n")
    _w(os_root / "routines" / "watchdog.yaml", "name: watchdog\nschedule: \"0 7,18 * * *\"\nenabled: true\n")
    _w(os_root / "routines" / "off.yaml", "name: off-one\nschedule: \"0 9 * * *\"\nenabled: false\n")
    _w(js / "routines" / "js-hunt-boards.yaml", "name: js-hunt-boards\nschedule: \"0 7-22/3 * * *\"\nenabled: true\n")
    _w(os_root / "logs" / "runs" / "watchdog" / "2026-10-05.md", "# r\n")
    _w(os_root / "logs" / "runs" / "js-hunt-boards" / "2026-09-30.md", "# r\n")
    _w(os_root / "logs" / "runs" / "nightly-sweep" / "2026-10-05.md",
       "# r\n\n## Run 2026-10-05T07:00:00-07:00 · ok\n- lint: 12 finding(s)\n- sync --check: 3 drift\n")
    return os_root, js


def run(os_root, js, *extra):
    return ag.main(["--root", str(js), "--os-root", str(os_root), "--today", TODAY.isoformat(), *extra])


def test_at_most_fifteen_lines_with_every_part(tmp_path, capsys):
    os_root, js = tree(tmp_path)
    assert run(os_root, js) == 0
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert len(out) <= 15
    text = "\n".join(out)
    assert out[0].startswith("Agency status") and "Interviews booked this week" in out[0]   
    for part in ("Clock:", "Funnel:", "By tier", "Queue", "Follow-ups due", "Routines", "Lint"):
        assert part in text, part
    assert "\u2014" not in text


def test_funnel_and_tiers(tmp_path):
    os_root, js = tree(tmp_path)
    s = ag.collect(js, os_root, TODAY)
    assert s["funnel"]["applications"] == 3 and s["funnel"]["applied"] == 2 and s["funnel"]["screen"] == 1
    assert s["tiers"] == {"dream": {"batched": 1, "applied": 1, "inferred": 0},       # R-S2 adds "inferred"
                          "focused": {"batched": 0, "applied": 0, "inferred": 0},
                          "mass": {"batched": 2, "applied": 1, "inferred": 0}, "untiered": {"applied": 0}}


def test_queue_routines_helpers_and_lint(tmp_path):
    os_root, js = tree(tmp_path)
    s = ag.collect(js, os_root, TODAY)
    assert s["queue"] == {"READY": 2, "PACKAGE": 1, "BLOCKED": 1}      # hub/NEXT-UP.md's state column
    r = s["routines"]
    assert r["enabled"] == 2 and r["today"] == 1 and r["stale"] == [("js-hunt-boards", "2026-09-30")]
    assert "helpers" not in s
    assert s["lint"] == {"source": "nightly-sweep 2026-10-05", "lint": "12 finding(s)", "sync": "3 drift"}


def test_absent_sources_say_no_data(tmp_path, capsys):
    os_root = tmp_path / "os"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    assert run(os_root, js) == 0
    out = capsys.readouterr().out
    assert "no data yet" in out and len(out.rstrip("\n").splitlines()) <= 15


def test_read_only(tmp_path):
    os_root, js = tree(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    run(os_root, js)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


# ----------------------------------------------------------------------------- Scout lessons line
def test_scout_lessons_line_counts_open_and_names_the_oldest(tmp_path, capsys):
    import hunt_lessons as hl
    os_root, js = tree(tmp_path)
    l1 = "LEARNED js-hunt-boards has no receipt | ls | alarm"
    l2 = "LEARNED fetch_ats took 14 minutes | log | parallelise"
    _w(js / "hub" / "lanes" / "HUNT.md", f"# HUNT\n\n### 2026-10-03 09:00 PDT · STATUS\n{l1}\n\n### 2026-10-04 09:00 PDT · STATUS\n{l2}\n")
    _w(js / "hub" / "lanes" / "SETUP.md", "# SETUP\n")
    s = ag.collect(js, os_root, TODAY)
    assert s["lessons"] == {"open": 2, "oldest": (hl.lesson_id(l1), "2026-10-03")}
    run(os_root, js)
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert f"Scout lessons: 2 open (oldest {hl.lesson_id(l1)} 2026-10-03)" in out and len(out) <= 15
    _w(js / "hub" / "lanes" / "SETUP.md", f"answer: {hl.lesson_id(l1)} FIX done\nanswer: {hl.lesson_id(l2)} NO-FIX vendor\n")
    run(os_root, js)
    assert "Scout lessons: 0 open" in capsys.readouterr().out


def test_scout_lessons_without_hunt_md_say_no_data(tmp_path, capsys):
    os_root, js = tree(tmp_path)
    run(os_root, js)
    assert "Scout lessons: no data yet" in capsys.readouterr().out


# ----------------------------------------------------------------------------- reader models line
def test_reader_models_line_counts_packages_with_a_section_6_and_the_clean_ones(tmp_path, capsys):
    from test_reader_model import GOOD
    os_root, js = tree(tmp_path)
    t = js / "research" / "targets"
    _w(t / "acme-ai-202610.md", GOOD)
    _w(t / "beta-ai-202610.md", GOOD.replace("W3 | Owned the app end to end | ev-acme-001\n", ""))   # W3 unanswered
    _w(t / "delta-ai-202610.md", "# Role Package\n## 1 Title\nno reader model yet\n")              # not counted
    _w(t / "acme-ai-202610.matrix.yaml", "rows: []\n")
    assert ag.collect(js, os_root, TODAY)["reader_models"] == {"packages": 2, "clean": 1}
    run(os_root, js)
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert any(l.endswith("reader models clean 1/2") for l in out) and len(out) <= 15


def test_reader_models_without_packages_say_no_data(tmp_path, capsys):
    os_root, js = tree(tmp_path)
    run(os_root, js)
    assert "reader models clean no data yet" in capsys.readouterr().out


# ----------------------------------------------------------------------------- focus profiles line
def _lens(js, family, approved):
    vision = '**Vision line.** "I build agents."' if approved else '**Vision line (draft for Alex).** "I build agents."'
    _w(js / "profile" / "lenses" / f"{family}.md",
       f"---\nfamily: {family}\ntitles_covered: ['*AI Engineer*']\nstatus: active\n---\n# {family}\n"
       f"## 1. Reader\nx\n## 7. Focus profile: the best foot forward\n{vision}\n")


def test_focus_profiles_line_counts_approved_vision_lines(tmp_path, capsys):
    os_root, js = tree(tmp_path)
    _lens(js, "ai-engineer", approved=True)
    _lens(js, "evaluation", approved=False)
    _lens(js, "tool-use", approved=False)
    _w(js / "profile" / "lenses" / "README.md", "# lenses\n")
    assert ag.collect(js, os_root, TODAY)["focus"] == {"profiles": 3, "approved": 1}
    run(os_root, js)
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert "Focus profiles: 3 (vision lines approved 1/3); reader models clean no data yet" in out and len(out) <= 15
    assert not any(l.startswith("Reader models:") for l in out)      # one line for both, not two


def test_focus_profiles_without_lenses_say_no_data(tmp_path, capsys):
    os_root, js = tree(tmp_path)
    run(os_root, js)
    assert "Focus profiles: no data yet" in capsys.readouterr().out


def test_a_routine_installed_since_its_last_receipt_is_not_stale_and_a_future_install_hides_nothing(tmp_path):
    """CS12 F3: agency_status called four reinstalled routines "stale (never)" while the watchdog called them OK."""
    os_root, js = tree(tmp_path)
    spec = os_root / "routines" / "audit.yaml"
    _w(spec, "name: audit\nschedule: \"0 9 1 * *\"\nenabled: true\n")
    assert ("audit", "never") in ag.routines(os_root, TODAY)["stale"]
    _w(spec, "name: audit\nschedule: \"0 9 1 * *\"\nenabled: true\ninstalled: \"2026-10-02T12:36\"\n")
    assert "audit" not in [n for n, _ in ag.routines(os_root, TODAY)["stale"]]
    _w(spec, "name: audit\nschedule: \"0 9 1 * *\"\nenabled: true\ninstalled: \"2099-01-01T00:00\"\n")
    assert ("audit", "never") in ag.routines(os_root, TODAY)["stale"]


def test_the_queue_counts_people_first_as_one_state(tmp_path):
    os_root, js = tree(tmp_path)
    _w(js / "hub" / "NEXT-UP.md", "# NEXT-UP\n\n## The list (top first)\n\n| # | state |\n|---|---|\n"
                                  "| 1 | PEOPLE FIRST `a-1` |\n| 2 | PEOPLE FIRST `b-2` |\n| 3 | READY `c-3` |\n| 4 | BLOCKED: years |\n")
    assert ag.queue(js) == {"PEOPLE FIRST": 2, "READY": 1, "BLOCKED": 1}
