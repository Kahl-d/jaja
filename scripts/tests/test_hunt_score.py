"""hunt_score.py: the deterministic triage score for every open job."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa
import hunt_score as hs

TODAY = date(2026, 9, 29)
JD = ("We build LLM agents and evals in Python. You will own agent harnesses, evaluation pipelines and RAG. "
      "Experience with PyTorch and FastAPI. 2+ years of experience. We sponsor visas.")


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "js"
    (root / "jobs").mkdir(parents=True)
    (root / "prospects").mkdir()
    (root / "targets").mkdir()
    (root / "profile").mkdir()
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Tiny AI, slug: tiny-ai, tier: strong, segment: startup, ats_vendor: ashby, board_token: tiny,"
        " sponsorship_stance: sponsors}\n"
        "  - {name: Giant Co, slug: giant-co, tier: dream, segment: giant, ats_vendor: greenhouse, board_token: g,"
        " sponsorship_stance: sponsors}\n"
        "  - {name: Mid Co, slug: mid-co, tier: good, ats_vendor: lever, board_token: m,"
        " sponsorship_stance: does-not-sponsor}\n")
    (root / "profile" / "skills-taxonomy.yaml").write_text(
        "skills:\n  languages:\n"
        "    - {skill: Python, evidence: [ev-1]}\n"
        "    - {skill: LLM agents, aliases: [LLM, agents], evidence: [ev-2]}\n"
        "    - {skill: Evals, aliases: [evals, evaluation], evidence: [ev-3]}\n"
        "    - {skill: RAG, evidence: [ev-4]}\n")
    (root / "clock.yaml").write_text(
        "updated: 2026-09-26\nemployer: Example Corp\nemployment_end: 2026-10-01\nemployment_end_confirmed: true\n"
        "clock_days: 90\nbasis: test\nnew_employment_start: null\nvisa_allowance_days: null\n"
        "visa_allowance_source: null\nstem_extension: {status: pending}\n"
        "phases:\n  - {weeks: [1, 2], name: a}\n  - {weeks: [3, 8], name: b}\n  - {weeks: [9, 11], name: c}\n"
        "  - {weeks: [12, 13], name: d}\n")
    return root


def _job(root, slug, title, company_slug, *, company=None, jd=JD, posted=None, gate="pass", status="fit_checked",
         location="San Francisco, CA", sponsorship="unknown", h1b=None, save=True, **extra):
    rec = fa.make_record({"name": company or company_slug, "slug": company_slug}, title=title, source="ashby",
                         source_url=f"https://jobs.example.com/{slug}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted or (TODAY - timedelta(days=1)).isoformat(), location=location,
                         remote="onsite", comp=(None, None, None), employment_type="full-time", jd_text=jd,
                         now=NOW)
    rec.update({"id": slug, "gate": gate, "status": status, "sponsorship": sponsorship, "h1b_approvals_3y": h1b,
                **extra})
    if save:
        fa.save_job(root, rec)
    return rec


def _ctx(root, today=TODAY):
    return hs.build_context(root, today=today)


# --------------------------------------------------------------------------- components
def test_a_fresh_ai_engineer_role_at_a_sponsoring_startup_scores_high_with_reasons(world):
    rec = _job(world, "tiny-1", "AI Engineer, Agents", "tiny-ai", company="Tiny AI")
    out = hs.score(rec, _ctx(world))
    assert out["hunt_score"] >= 80
    reasons = " | ".join(out["hunt_reasons"])
    for part in ("title", "skills", "fresh", "startup", "sponsor"):
        assert part in reasons, part


def test_each_component_moves_the_score_the_right_way(world):
    ctx = _ctx(world)
    base = hs.score(_job(world, "b", "AI Engineer", "tiny-ai", save=False), ctx)["hunt_score"]
    worse = {
        "title": _job(world, "t", "Sales Manager", "tiny-ai", save=False),
        "seniority": _job(world, "s", "Staff AI Engineer", "tiny-ai", save=False),
        "years": _job(world, "y", "AI Engineer", "tiny-ai", jd=JD.replace("2+ years", "8+ years"), save=False),
        "stale": _job(world, "o", "AI Engineer", "tiny-ai", posted=(TODAY - timedelta(days=25)).isoformat(),
                      save=False),
        "visa": _job(world, "v", "AI Engineer", "mid-co", save=False),
        "location": _job(world, "l", "AI Engineer", "tiny-ai", gate="review", save=False),
    }
    for name, rec in worse.items():
        assert hs.score(rec, ctx)["hunt_score"] < base, name


def test_a_missing_jd_is_neutral_on_skills_and_says_so(world):
    rec = _job(world, "nojd", "AI Engineer", "tiny-ai", jd="", save=False)
    out = hs.score(rec, _ctx(world))
    assert any("JD not captured" in r for r in out["hunt_reasons"])
    assert 40 <= out["hunt_score"] <= 90


def test_a_warm_path_raises_the_score(world):
    ctx0 = _ctx(world)
    cold = hs.score(_job(world, "w", "AI Engineer", "mid-co", save=False), ctx0)["hunt_score"]
    (world / "prospects" / "ana.md").write_text("---\nid: ana\nname: Ana\ncompany_slug: mid-co\nstatus: in_conversation\n"
                                                "---\n# Ana\n")
    warm = hs.score(_job(world, "w", "AI Engineer", "mid-co", save=False), _ctx(world))
    assert warm["hunt_score"] > cold and any("warm" in r for r in warm["hunt_reasons"])


def test_the_clock_weight_favours_giants_early_and_startups_late(world):
    early, late = _ctx(world, TODAY), _ctx(world, date(2026, 11, 10))
    giant = _job(world, "g", "AI Engineer", "giant-co", save=False)
    start = _job(world, "s", "AI Engineer", "tiny-ai", save=False)
    assert hs.clock_weight("giant", early["days_left"], dream=True) == 1.0
    assert hs.clock_weight("giant", late["days_left"], dream=True) == pytest.approx(hs.DEFAULTS["dream_floor"])
    assert hs.clock_weight("giant", late["days_left"], dream=False) == 0.0
    assert 0 < hs.clock_weight("startup", late["days_left"], dream=False) < 1
    late_gap = hs.score(start, late)["hunt_score"] - hs.score(giant, late)["hunt_score"]
    early_gap = hs.score(start, early)["hunt_score"] - hs.score(giant, early)["hunt_score"]
    assert late_gap >= early_gap + 10                                              # the clock multiplies the score


def test_an_unknown_company_uses_the_mid_segment_and_a_neutral_tier(world):
    out = hs.score(_job(world, "u", "AI Engineer", "nobody-inc", save=False), _ctx(world))
    assert any("segment mid (not on the watch list)" in r for r in out["hunt_reasons"])


def test_weights_can_be_overridden_by_targets_hunt_yaml(world):
    (world / "targets" / "hunt.yaml").write_text("weights: {role: 0, skills: 0, seniority: 0, location: 0, visa: 0,"
                                                 " freshness: 100, warm: 0, tier: 0}\ncut: 50\n")
    ctx = _ctx(world)
    assert ctx["cut"] == 50
    fresh = hs.score(_job(world, "f", "AI Engineer", "tiny-ai", posted=TODAY.isoformat(), save=False), ctx)
    assert fresh["hunt_score"] == 100
    off = hs.score(_job(world, "g", "Sales Manager", "tiny-ai", posted=TODAY.isoformat(), save=False), ctx)
    assert off["hunt_score"] == 49                      # the role gate holds whatever the weights say


def test_bad_weights_are_refused(world):
    (world / "targets" / "hunt.yaml").write_text("weights: {role: 50, mystery: 50}\n")
    with pytest.raises(hs.HuntError, match="mystery"):
        _ctx(world)


# --------------------------------------------------------------------------- run
def test_run_scores_only_open_gated_jobs_ranks_them_and_writes_the_fields(world):
    _job(world, "good", "AI Engineer, Agents", "tiny-ai")
    _job(world, "meh", "Data Analyst", "mid-co")
    _job(world, "failed", "AI Engineer", "tiny-ai", gate="fail", status="skipped")
    _job(world, "shut", "AI Engineer", "tiny-ai", status="closed")
    _job(world, "applied", "AI Engineer", "tiny-ai", status="applied")
    ranked = hs.run(world, today=TODAY)
    assert [r["id"] for r in ranked] == ["good", "meh"]
    jobs = fa.load_jobs(world)
    assert jobs["good"]["hunt_score"] == ranked[0]["hunt_score"] and jobs["good"]["hunt_reasons"]
    assert str(jobs["good"]["hunt_scored_at"]) == "2026-09-29"
    assert "hunt_score" not in jobs["failed"] and "hunt_score" not in jobs["shut"]


def test_dry_run_writes_nothing(world):
    _job(world, "good", "AI Engineer", "tiny-ai")
    before = (world / "jobs" / "good.md").read_text()
    assert hs.run(world, today=TODAY, dry_run=True)[0]["id"] == "good"
    assert (world / "jobs" / "good.md").read_text() == before


def test_cli_prints_the_ranked_list(world, capsys):
    _job(world, "good", "AI Engineer", "tiny-ai", company="Tiny AI")
    assert hs.main(["--root", str(world), "--today", "2026-09-29", "--dry-run", "--top", "5"]) == 0
    out = capsys.readouterr().out
    assert "good" in out and "Tiny AI" in out and "dry run" in out


def test_components_are_computed_as_documented(world):
    ctx = _ctx(world)
    comp = lambda rec: hs.score(rec, ctx)["components"]  # noqa: E731
    mixed = comp(_job(world, "m", "AI Engineer", "tiny-ai", jd=JD + " Kubernetes, Terraform, Kafka, Spark.", save=False))
    assert 0 < mixed["skills"] < 1                                   # evidenced terms over all terms the JD names
    assert comp(_job(world, "n", "AI Engineer", "tiny-ai", jd="", save=False))["skills"] == 0.5
    assert comp(_job(world, "s", "Staff AI Engineer", "tiny-ai", save=False))["seniority"] == 0.2
    assert comp(_job(world, "r", "Senior AI Engineer", "tiny-ai", save=False))["seniority"] == 0.6
    assert comp(_job(world, "y", "AI Engineer", "tiny-ai", jd=JD.replace("2+ years", "8+ years"), save=False))[
        "seniority"] == 0.25
    assert comp(_job(world, "z", "AI Engineer", "tiny-ai", jd=JD.replace("2+ years", "5+ years"), save=False))[
        "seniority"] == 0.6


def test_a_failed_gate_is_never_scored_even_if_its_status_looks_open(world):
    _job(world, "odd", "AI Engineer", "tiny-ai", gate="fail", status="fit_checked")
    assert hs.run(world, today=TODAY) == []


# --------------------------------------------------------------------------- role fit
# A generic "Software Engineer" once matched roles.md and tier plus freshness carried the rest, so off-role DevOps and
# silicon titles topped the triage. Role fit now leads the score and gates the cut. The cases are synthetic records
# shaped like real ones (scripts/tests/fixtures/hunt_role_cases.json).
import json as _json  # noqa: E402

CASES = _json.loads((Path(__file__).parent / "fixtures" / "hunt_role_cases.json").read_text(encoding="utf-8"))


def _case(world, c, *, company_slug, **kw):
    return _job(world, c["id"], c["title"], company_slug, company=c["company"], jd=c["jd"], save=False, **kw)


@pytest.mark.parametrize("case", CASES["off_role"], ids=lambda c: c["title"])
def test_an_off_role_title_never_reaches_the_cut_however_good_the_rest(world, case):
    ctx = _ctx(world)
    rec = _case(world, case, company_slug="giant-co", posted=TODAY.isoformat(), sponsorship="yes")   # dream, fresh
    out = hs.score(rec, ctx)
    assert out["hunt_score"] < ctx["cut"], (case["title"], out)
    assert out["components"]["role"] == 0.0 and any(r.startswith("role gate") for r in out["hunt_reasons"])


@pytest.mark.parametrize("case", CASES["known_good"], ids=lambda c: c["title"])
def test_a_known_good_role_has_full_role_fit_and_outranks_every_off_role_one(world, case):
    ctx = _ctx(world)
    good = hs.score(_case(world, case, company_slug="unknown-co", gate="review"), ctx)   # thin: not on the watch list
    assert good["components"]["role"] >= 0.8, (case["title"], good["hunt_reasons"])
    assert not any(r.startswith("role gate") for r in good["hunt_reasons"])
    worst_good = good["hunt_score"]
    for bad in CASES["off_role"]:
        b = hs.score(_case(world, bad, company_slug="giant-co", posted=TODAY.isoformat(), sponsorship="yes"), ctx)
        assert worst_good > b["hunt_score"], (case["title"], worst_good, bad["title"], b["hunt_score"])


@pytest.mark.parametrize("case", CASES["known_good"], ids=lambda c: c["title"])
def test_a_role_the_owner_picked_stays_at_or_above_the_cut(world, case):
    app = world / "applications" / case["application"]
    app.mkdir(parents=True)
    (app / "application.md").write_text(f"---\ntype: application\nid: {case['application']}\njob: '[[{case['id']}]]'\n---\n")
    ctx = _ctx(world)
    old = (TODAY - timedelta(days=20)).isoformat()      # stale enough that only the pick holds it at the cut
    out = hs.score(_case(world, case, company_slug="unknown-co", gate="review", posted=old), ctx)
    assert out["hunt_score"] >= ctx["cut"], (case["title"], out)
    assert any(r.startswith("the owner's pick") for r in out["hunt_reasons"])


def test_a_generic_title_is_judged_by_its_jd():
    ai = "You will build LLM agents, RAG retrieval and evaluation harnesses; fine-tuning with PyTorch."
    assert hs.role_fit("Software Engineer", ai)[0] >= 0.9
    assert hs.role_fit("Founding Engineer", ai)[0] >= 0.9
    assert hs.role_fit("Software Engineer", "Build payment ledgers in Go and Postgres for our billing team.")[0] < 0.3
    assert hs.role_fit("Software Engineer", "")[0] == 0.5                         # no JD yet: neutral, not gated
    assert hs.role_fit("Software Engineer", "Ranking with machine learning models in PyTorch.")[0] == 0.5   # 2 terms
    assert hs.role_fit("Senior AI Engineer", "")[0] == 1.0
    assert hs.role_fit("Lead Software Engineer: AI for Hardware Verification", "")[0] == 0.8   # AI beats "hardware"
    assert hs.role_fit("Hardware Engineer", "")[0] < 0.3
    assert hs.role_fit("Software Engineer, AI accelerator Runtime", ai)[0] == 0.0             # hard off beats AI
    assert hs.role_fit("AI Trainer", ai)[0] == 0.0


# --------------------------------------------------------------------------- fit calibration
# Skips by the fit-scorer (applied-scientist roles: years asked; research-engineer roles: distributed systems and RL at
# scale) once crowded the top of the triage. Two causes the score can see: it read the SMALLEST years figure in a JD
# ("3+ years ... Master's and 4+"), and it never learned from the verdicts already given at the same company and title
# family. Fixtures: scripts/tests/fixtures/hunt_fit_calibration.json (synthetic records).
CAL = _json.loads((Path(__file__).parent / "fixtures" / "hunt_fit_calibration.json").read_text(encoding="utf-8"))


def _cal_world(world):
    (world / "targets" / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Acme Retail, slug: acme, tier: dream, ats_vendor: greenhouse, board_token: a, sponsorship_stance: sponsors}\n"
        "  - {name: Globex Lab, slug: globex, tier: dream, ats_vendor: greenhouse, board_token: globex,"
        " sponsorship_stance: sponsors}\n"
        "  - {name: Initech, slug: initech, tier: dream, ats_vendor: workday, board_token: s,"
        " sponsorship_stance: sponsors}\n"
        "  - {name: Vandelay, slug: vandelay, tier: strong, ats_vendor: ashby, board_token: vandelay,"
        " sponsorship_stance: sponsors-selectively}\n")
    return world


def _cal(world, c, *, jid=None, verdict=None, save=True, **kw):
    extra = {"fit_verdict": verdict, "fit_score": c["fit_score"]} if verdict else {}
    status = "skipped" if verdict == "skip" else "fit_checked"
    return _job(world, jid or c["id"], c["title"], c["company_slug"], company=c["company"], jd=c["jd"], save=save,
                status=status, sponsorship="yes", posted=(TODAY - timedelta(days=2)).isoformat(), **{**extra, **kw})


def test_years_asked_reads_the_largest_experience_figure_not_the_smallest():
    fig = next(c for c in CAL["skip"] if c["id"] == "acme-1001")
    s, why = hs._seniority(fig["title"], fig["jd"])
    assert s <= 0.6 and "4+ years" in why, why


@pytest.mark.parametrize("case", CAL["skip"], ids=lambda c: c["title"])
def test_the_next_role_like_a_fit_skip_is_scored_down_and_a_repeated_family_falls_under_the_cut(world, case):
    """The six skips are on record; the same role family at the same company arrives again as a fresh find."""
    _cal_world(world)
    cold = hs.score(_cal(world, case, jid="twin", save=False), _ctx(world))
    for skip in CAL["skip"]:
        _cal(world, skip, verdict="skip")
    ctx = _ctx(world)
    warm = hs.score(_cal(world, case, jid="twin", save=False), ctx)
    assert warm["hunt_score"] < cold["hunt_score"], (case["title"], cold["hunt_score"], warm["hunt_score"])
    assert any(r.startswith("fit history") for r in warm["hunt_reasons"])
    family = hs.title_family(case["title"])
    peers = [c for c in CAL["skip"] if c["company_slug"] == case["company_slug"] and hs.title_family(c["title"]) == family]
    if len(peers) >= 2:                                   # a pattern: two or more skips in this family at this company
        assert warm["hunt_score"] < ctx["cut"], (case["title"], warm["hunt_score"], warm["hunt_reasons"])


@pytest.mark.parametrize("case", CAL["kept"], ids=lambda c: c["title"])
def test_the_roles_the_fit_scorer_kept_stay_at_or_above_the_cut(world, case):
    _cal_world(world)
    for skip in CAL["skip"]:
        _cal(world, skip, verdict="skip")
    ctx = _ctx(world)
    out = hs.score(_cal(world, case, save=False), ctx)
    assert out["hunt_score"] >= ctx["cut"], (case["title"], out["hunt_score"], out["hunt_reasons"])
    assert not any(r.startswith("fit history") for r in out["hunt_reasons"])


def test_fit_history_needs_two_reports_and_an_apply_in_the_family_cancels_it(world):
    _cal_world(world)
    fig, pv = (next(c for c in CAL["skip"] if c["id"] == i) for i in ("acme-1001", "acme-1004"))
    _cal(world, fig, verdict="skip")
    one = hs.score(_cal(world, pv, jid="probe", save=False), _ctx(world))
    assert not any(r.startswith("fit history") for r in one["hunt_reasons"])          # one report is not a pattern
    _cal(world, pv, jid="second", verdict="skip")
    two = hs.score(_cal(world, pv, jid="probe", save=False), _ctx(world))
    assert any(r.startswith("fit history: 2 of 2") for r in two["hunt_reasons"])
    assert two["hunt_score"] == one["hunt_score"] - hs.FIT_FAMILY_MAX == one["hunt_score"] - 12
    _cal(world, fig, jid="liked", verdict="apply")
    three = hs.score(_cal(world, pv, jid="probe", save=False), _ctx(world))
    assert not any(r.startswith("fit history") for r in three["hunt_reasons"])


def test_the_family_key_drops_levels_and_team_suffixes():
    assert hs.title_family("Applied Scientist II, Sponsored Products and Brands") == "applied scientist"
    assert hs.title_family("Research Engineer / Performance Engineer, RL Distributed Systems") == "research engineer"
    assert hs.title_family("Senior Machine Learning Engineer - Search") == "machine learning engineer"


def test_only_sentences_about_experience_set_the_years_bar():
    jd = "Founded 12 years ago, we serve 40 countries. You have 2+ years of experience with Python."
    assert hs._seniority("AI Engineer", jd) == (0.9, "level fits; asks 2+ years")


def test_a_jobs_own_verdict_never_counts_against_it(world):
    _cal_world(world)
    fig, gen = (next(c for c in CAL["skip"] if c["id"] == i) for i in ("acme-1001", "acme-1003"))
    _cal(world, fig, verdict="skip")
    mine = _cal(world, gen, verdict="skip")
    out = hs.score(mine, _ctx(world))
    assert not any(r.startswith("fit history") for r in out["hunt_reasons"])      # one peer only: no pattern yet


def test_a_gate_fail_is_not_a_fit_report(world):
    """fit_gates writes fit_verdict skip on every gate fail (thousands of records on a large harvest) with no fit_score; counting
    them would collapse the cut. Only the fit-scorer's reports (a numeric fit_score) teach the triage."""
    _cal_world(world)
    fig = next(c for c in CAL["skip"] if c["id"] == "acme-1001")
    for i in range(3):
        _job(world, f"gatefail-{i}", "Applied Scientist, Team " + str(i), "acme", company="Acme Retail", gate="fail",
             status="skipped", fit_verdict="skip", fit_score=None)
    out = hs.score(_cal(world, fig, jid="probe", save=False), _ctx(world))
    assert not any(r.startswith("fit history") for r in out["hunt_reasons"])


def test_a_core_strength_the_skipped_titles_lacked_waives_the_fit_history(world):
    """Two RL skips at one company pulled "Research Engineer, Post-Training Model Evaluations" under the cut. Evals is
    a configured core word; the skipped RL titles never named it."""
    _cal_world(world)
    rl = next(c for c in CAL["skip"] if c["id"] == "globex-1001")
    for c in CAL["skip"]:
        _cal(world, c, verdict="skip")
    evals = hs.score(_job(world, "ev", "Research Engineer, Post-Training Model Evaluations", "globex",
                          company="Globex Lab", jd=rl["jd"], save=False, sponsorship="yes"), _ctx(world))
    assert not any(r.startswith("fit history:") for r in evals["hunt_reasons"])
    assert any(r.startswith("fit history waived") for r in evals["hunt_reasons"])
    again = hs.score(_job(world, "rl2", "Research Engineer, RL Engineering", "globex", company="Globex Lab",
                          jd=rl["jd"], save=False, sponsorship="yes"), _ctx(world))
    assert any(r.startswith("fit history:") for r in again["hunt_reasons"])


def test_a_core_word_the_skipped_titles_also_had_waives_nothing(world):
    _cal_world(world)
    rl = next(c for c in CAL["skip"] if c["id"] == "globex-1001")
    for i in range(2):
        _job(world, f"ag{i}", f"Research Engineer, Agents {i}", "globex", company="Globex Lab", jd=rl["jd"],
             status="skipped", fit_verdict="skip", fit_score=2.8)
    out = hs.score(_job(world, "ag-new", "Research Engineer, Agents Platform", "globex", company="Globex Lab",
                        jd=rl["jd"], save=False, sponsorship="yes"), _ctx(world))
    assert any(r.startswith("fit history: 2 of 2") for r in out["hunt_reasons"])


def test_a_plus_years_bar_counts_without_the_word_experience_and_level_codes_are_read():
    """A JD can ask "8+ years designing, building, and scaling web crawlers" (no word "experience"); PMTS is Principal
    level, SMTS and L5 are senior."""
    assert hs._seniority("Research Engineer, Web Crawling",
                         "You have 8+ years designing, building, and scaling web crawlers.")[0] == 0.25
    assert hs._seniority("AI Engineer", "Founded 12 years ago, we are 40 people.")[0] == 0.9     # no plus, no bar
    assert hs._seniority("Software Engineer, AI Systems (PMTS)", "")[0] == 0.2
    assert hs._seniority("Software Engineering SMTS", "")[0] == 0.6
    assert hs._seniority("Software Engineer L5 - AI Observability & Agent Evaluation", "")[0] == 0.6


# --------------------------------------------------------------------------- fresh roles, founding seats, new startups
def test_stage_defaults_lift_freshness_and_add_a_stage_component():
    w = hs.DEFAULTS["weights"]
    assert sum(w.values()) == 100
    assert w["freshness"] == 18 and w["stage"] == 5
    assert w["freshness"] > w["skills"] / 2 and w["role"] >= 25          # role fit still leads 


def test_stage_freshness_falls_faster():
    f = hs._freshness
    assert f(0) == f(2) == 1.0
    assert f(7) == pytest.approx(0.6, abs=0.01)
    assert f(14) == pytest.approx(0.25, abs=0.01)
    assert f(21) == pytest.approx(0.1, abs=0.01) and f(45) == pytest.approx(0.05)
    assert f(None) == 0.5
    days = [0, 1, 2, 3, 5, 7, 10, 14, 18, 21, 30, 60]
    assert all(f(a) >= f(b) for a, b in zip(days, days[1:]))


def test_stage_a_founding_title_takes_the_full_stage_boost(world):
    out = hs.score(_job(world, "fd", "Founding AI Engineer", "nobody-inc", save=False), _ctx(world))
    assert out["components"]["stage"] == 1.0 and any("stage: founding" in r for r in out["hunt_reasons"])


def test_stage_a_new_startup_is_read_from_the_jd_or_a_recent_funding_round(world):
    seed = JD + " We are a seed-stage startup backed by top investors."
    out = hs.score(_job(world, "sd", "AI Engineer", "nobody-inc", jd=seed, save=False), _ctx(world))
    assert out["components"]["stage"] == 0.9 and any("stage: new startup" in r for r in out["hunt_reasons"])
    sig = world / "state" / "signals"
    sig.mkdir(parents=True)
    (sig / "funding-2026-09-28.json").write_text(
        '{"signals": [{"company": "Mid Co", "file_date": "2026-09-10", "matched_by": ["target:mid-co"]},'
        ' {"company": "Old Co", "file_date": "2026-04-01", "matched_by": ["target:nobody-inc"]}]}')
    ctx = _ctx(world)
    out = hs.score(_job(world, "fr", "AI Engineer", "mid-co", save=False), ctx)
    assert out["components"]["stage"] == 0.9 and any("funding round 2026-09-10" in r for r in out["hunt_reasons"])
    old = hs.score(_job(world, "ol", "AI Engineer", "nobody-inc", save=False), ctx)
    assert old["components"]["stage"] == 0.5                                # a round older than 90 days is not new


def test_stage_big_companies_rank_above_the_unknown_middle(world):
    ctx = _ctx(world)
    big = hs.score(_job(world, "bg", "AI Engineer", "giant-co", save=False), ctx)
    mid = hs.score(_job(world, "md", "AI Engineer", "nobody-inc", save=False), ctx)
    assert big["components"]["stage"] == 0.8 and mid["components"]["stage"] == 0.5
    assert any("stage: big company" in r for r in big["hunt_reasons"])


def test_stage_a_fresh_role_clearly_outranks_the_same_role_ten_days_old(world):
    ctx = _ctx(world)
    fresh = hs.score(_job(world, "n", "AI Engineer", "mid-co", posted=TODAY.isoformat(), save=False), ctx)
    stale = hs.score(_job(world, "o", "AI Engineer", "mid-co", posted=(TODAY - timedelta(days=10)).isoformat(),
                          save=False), ctx)
    assert fresh["hunt_score"] - stale["hunt_score"] >= 8


def test_stage_hunt_yaml_accepts_the_stage_weight(world):
    (world / "targets" / "hunt.yaml").write_text("weights: {role: 30, stage: 70}\n")
    ctx = _ctx(world)
    assert ctx["weights"]["stage"] == pytest.approx(70.0) and ctx["weights"]["freshness"] == 0


def test_stage_founding_team_words_in_a_big_companys_jd_are_not_a_new_startup(world):
    jd = JD + " Join the founding team of a new product inside our company. We recently raised our Series B."
    big = hs.score(_job(world, "sf", "Senior AI Engineer", "giant-co", jd=jd, save=False), _ctx(world))
    assert big["components"]["stage"] == 0.8                               # big company wins over JD words
    plain = hs.score(_job(world, "nq", "AI Engineer", "nobody-inc", jd=JD + " Join our founding team.", save=False),
                     _ctx(world))
    assert plain["components"]["stage"] == 0.5                             # "founding team" alone is too ambiguous


def test_l_e1019a7b_a_board_wide_republish_gets_no_freshness_points(world):
    rec = _job(world, "pi", "AI Engineer", "tiny-ai", posted=None, save=False)
    rec.update({"posted_at": None, "first_seen": "2026-09-29T08:00:00Z",
                "posted_unknown": "board-wide republish 2026-07-08 (every role on the board shares it)"})
    out = hs.score(rec, _ctx(world))
    assert out["components"]["freshness"] == 0.5 and any("date unknown" in r for r in out["hunt_reasons"])


# companies.yaml `segment` is startup, big-tech, ai-lab or enterprise (scripts/segment_companies.py); weeks_to_offer
# knows startup, mid and giant. The three big kinds read as giant.
import pytest  # noqa: E402


@pytest.mark.parametrize("company,segment", [
    ({"name": "Globex Lab", "tier": "dream", "segment": "ai-lab"}, "giant"),
    ({"name": "Bigco", "tier": "strong", "segment": "big-tech"}, "giant"),
    ({"name": "Example Bank", "tier": "good", "segment": "enterprise"}, "giant"),
    ({"name": "Sim", "tier": "dream", "segment": "startup"}, "startup"),
    ({"name": "Old Giant", "tier": "strong", "segment": "giant"}, "giant"),          # the legacy value still reads
    ({"name": "Dreamy", "tier": "dream"}, "mid"),                                     # no segment: mid; tier is not size
    ({"name": "Plain", "tier": "good"}, "mid"),
    (None, "mid"),                                                                     # not on the watch list
])
def test_clock_segment_maps_the_company_segments(company, segment):
    assert hs.clock_segment(company)[0] == segment


def test_an_ai_lab_counts_as_a_big_company_for_the_stage():
    s, why = hs._stage("Research Engineer", "", "x", {"tier": "strong", "segment": "ai-lab"}, {})
    assert why == "stage: big company"


# Freshness (an ATS update date is not a repost) uses the LATEST of (a) the first posting date,
# (b) a dated hiring post within 7 days recorded as hiring_signal {url, date, by}, (c) a relist with a NEW req id or URL.
# Never updated_at, a board-wide republish or an alert-copy date.
from datetime import date as _date  # noqa: E402

_TODAY = _date(2026, 10, 1)


def _rec(**kw):
    base = {"id": "acme-c501f06b", "req_id": "c501f06b", "posted_at": "2026-08-03T00:00:00Z",
            "source_url": "https://jobs.ashbyhq.com/acme/10dc1b32"}
    base.update(kw)
    return base


def test_a_recent_hiring_post_freshens_an_old_posting():
    rec = _rec(hiring_signal={"url": "https://news.ycombinator.com/item?id=4201", "date": "2026-09-30", "by": "founder"})
    assert hs._age_days(rec, _TODAY) == 1
    assert hs.fresh_source(rec, _TODAY)[1].startswith("hiring post")


def test_a_hiring_post_older_than_7_days_or_without_a_url_does_not_count():
    assert hs._age_days(_rec(hiring_signal={"url": "https://x.com/a/1", "date": "2026-09-20", "by": "hm"}), _TODAY) == 59
    assert hs._age_days(_rec(hiring_signal={"date": "2026-09-30", "by": "hm"}), _TODAY) == 59


def test_a_relist_counts_only_with_a_new_req_or_url():
    assert hs._age_days(_rec(relist={"date": "2026-09-28", "req_id": "d9e01aa2"}), _TODAY) == 3
    assert hs._age_days(_rec(relist={"date": "2026-09-28", "url": "https://jobs.ashbyhq.com/acme/new"}), _TODAY) == 3
    assert hs._age_days(_rec(relist={"date": "2026-09-28", "req_id": "c501f06b"}), _TODAY) == 59      # same req
    assert hs._age_days(_rec(relist={"date": "2026-09-28",
                                     "url": "https://jobs.ashbyhq.com/acme/10dc1b32"}), _TODAY) == 59


def test_updated_at_and_a_board_republish_never_freshen():
    assert hs._age_days(_rec(updated_at="2026-09-30T00:00:00Z"), _TODAY) == 59
    assert hs._age_days(_rec(posted_unknown=True), _TODAY) is None
    signal = {"url": "https://news.ycombinator.com/item?id=1", "date": "2026-09-29", "by": "company"}
    assert hs._age_days(_rec(posted_unknown=True, hiring_signal=signal), _TODAY) == 2   # the post still dates it
