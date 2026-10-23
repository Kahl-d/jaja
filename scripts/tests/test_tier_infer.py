"""`tracker.py tier <slug> --infer` computes the tier (dream = a company at targets/companies.yaml tier dream, or a
warm path, or fit 4.5+; focused = fit 4.0 to 4.5; mass = under 4.0) from fit.md's score, the dream list and prospects/,
prints the tier and why, and records it with that reason (so it still reads "(inferred)"). No fit and no dream signal:
refused, never guessed."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import strategy_review as sr  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402
from test_strategy_review import app, js, w  # noqa: E402,F401  (the fixture world)

NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
BOARD = """---
title: DREAM-BOARD
---
| # | company | role | fit | send-kit approval | warm path | package (research/targets/) | note |
|---|---|---|---|---|---|---|---|
| 1 | Fabrikam | Deployed Engineer | 4.2 | none | none | x | y |
"""


def _fit(root, slug, score):
    w(root / "applications" / slug / "fit.md", f"---\njob: '[[job-{slug}]]'\nscore: {score}\nverdict: maybe\n---\n# fit\n")


def _prospect(root, slug, company_slug, warmth=0, tags=()):
    w(root / "prospects" / f"{slug}.md", f"---\ntype: prospect\nid: {slug}\nname: {slug}\ncompany_slug: {company_slug}\n"
                                         f"warmth: {warmth}\ntags: {list(tags)}\nstatus: new\n---\n")


@pytest.fixture
def world(js):
    w(js / "research" / "dream" / "DREAM-BOARD.md", BOARD)
    app(js, "fabrikam-q", "Fabrikam", "Deployed Engineer", None, [("2026-10-01T18:00:00Z", "applied")])
    app(js, "plain-a", "Plainco", "AI Engineer", None, [("2026-10-01T18:00:00Z", "applied")])
    return js


def _dream(root, name, slug):
    path = root / "targets" / "companies.yaml"
    path.write_text(path.read_text().rstrip("\n") + f"\n  - {{name: {name}, slug: {slug}, tier: dream, ats_vendor: ashby}}\n")


def test_a_dream_tier_company_is_dream_whatever_the_fit(world):
    # dream is targets/companies.yaml tier dream; the dream board does not make a company dream
    _dream(world, "Fabrikam", "fabrikam")
    _fit(world, "fabrikam-q", 4.2)
    tier, why = sr.infer_application_tier(world, "fabrikam-q")
    assert tier == "dream" and why.startswith("inferred by the tier rule") and "fit 4.2" in why
    assert "companies.yaml tier dream" in why and "DREAM-BOARD" not in why


def test_the_research_lists_alone_do_not_make_a_company_dream(world):
    """A company on research/dream/DREAM-BOARD.md or DREAM-COMPANIES.md but not tier dream is not dream."""
    w(world / "research" / "dream" / "DREAM-COMPANIES.md", "| Company | Group | Why | Board |\n|---|---|---|---|\n"
                                                           "| Plainco | consumer | x | y |\n")
    _fit(world, "fabrikam-q", 4.2)
    _fit(world, "plain-a", 4.2)
    assert sr.infer_application_tier(world, "fabrikam-q")[0] == "focused"
    assert sr.infer_application_tier(world, "plain-a")[0] == "focused"
    assert "plainco" not in sr.dream_slugs(world) and "fabrikam" not in sr.dream_slugs(world)
    assert sr.dream_slugs(world) == {"orbital"}


def test_a_scaleup_reads_as_growth():
    assert sr.SEGMENT_OF_TYPE["scaleup"] == "growth"        


@pytest.mark.parametrize("score,expected", [(4.6, "dream"), (4.2, "focused"), (4.0, "focused"), (3.8, "mass")])
def test_the_fit_bands(world, score, expected):
    _fit(world, "plain-a", score)
    assert sr.infer_application_tier(world, "plain-a")[0] == expected


def test_a_warm_path_makes_it_dream_and_a_cold_target_does_not(world):
    _fit(world, "plain-a", 4.2)
    _prospect(world, "cold-c", "plainco", warmth=0)
    assert sr.infer_application_tier(world, "plain-a")[0] == "focused"
    _prospect(world, "ref-r", "plainco", warmth=0, tags=["referrer"])
    tier, why = sr.infer_application_tier(world, "plain-a")
    assert tier == "dream" and "warm path: ref-r" in why


def test_no_fit_and_no_dream_signal_is_refused(world, capsys):
    with pytest.raises(tr.TrackerError, match="cannot infer"):
        sr.infer_application_tier(world, "plain-a")
    assert tr.main(["--root", str(world), "tier", "plain-a", "--infer"], now=NOW) == 2


def test_cli_infer_prints_records_and_dry_runs(world, capsys):
    _dream(world, "Fabrikam", "fabrikam")
    _fit(world, "fabrikam-q", 4.2)
    path = world / "applications" / "fabrikam-q" / "application.md"
    before = path.read_bytes()
    assert tr.main(["--root", str(world), "tier", "fabrikam-q", "--infer", "--dry-run"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "fabrikam-q: tier None -> dream (dry-run" in out and "why: inferred by the tier rule" in out
    assert path.read_bytes() == before
    assert tr.main(["--root", str(world), "tier", "fabrikam-q", "--infer"], now=NOW) == 0
    meta = fm.load(path)[0]
    assert meta["tier"] == "dream" and meta["tier_source"].startswith("inferred by the tier rule")
    assert tr.main(["--root", str(world), "tier", "fabrikam-q", "focused"], now=NOW) == 2   # a set needs --source


def test_the_refusal_is_exit_2_not_a_traceback_when_run_as_a_script(world):
    import subprocess
    import sys
    from pathlib import Path
    script = Path(tr.__file__).resolve()
    r = subprocess.run([sys.executable, str(script), "--root", str(world), "tier", "plain-a", "--infer"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr and "cannot infer" in r.stderr
