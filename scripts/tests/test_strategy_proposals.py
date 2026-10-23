"""strategy_review proposals: the warm vs cold and speed numbers move the ranking weights as a PROPOSAL for the
recruiter and the owner, never silently. Rules: with 15 or more applications on each side, a warm path worth 2x or
more a cold application doubles the warm weight at most once per review; a median posting-to-application time over
72 h adds 4 to freshness; while the applications pause stands, mass-tier roles stay out of the day's list (a status
line with its count). Every line carries its numbers and n; a number not measured changes nothing.
targets/hunt.yaml is never written. Fixtures only."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from layout import JS  # noqa: E402
if str(JS / "scripts") not in sys.path:
    sys.path.insert(0, str(JS / "scripts"))

import hunt_score  # noqa: E402
import strategy_review as sr  # noqa: E402

DAY = date(2026, 10, 2)
CURRENT = dict(hunt_score.DEFAULTS["weights"])
RATES = {"warm": 0.26, "cold": 0.03, "warm_n": 15, "warm_k": 4, "cold_n": 24, "cold_k": 0}   # MIN_N on each side
LATENCY = {"median": 431, "measured": 20, "within_72h": 3}


def test_warm_doubles_at_most_and_freshness_gains_4_when_applying_is_slow():
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 5, "replies": 0}, paused=True)
    assert p["proposed"]["warm"] == CURRENT["warm"] * 2            # ratio 8.7, capped at 2x per review
    assert p["proposed"]["freshness"] == CURRENT["freshness"] + 4
    assert {k: v for k, v in p["proposed"].items() if k not in ("warm", "freshness")} == \
        {k: v for k, v in CURRENT.items() if k not in ("warm", "freshness")}
    warm = next(c for c in p["changes"] if c["weight"] == "warm")
    assert "warm 4 of 15" in warm["why"] and "cold 0 of 24" in warm["why"] and "small sample" not in warm["why"]
    fresh = next(c for c in p["changes"] if c["weight"] == "freshness")
    assert "median 431 h" in fresh["why"] and "3 of 20 within 72 h" in fresh["why"]


def test_no_change_when_the_numbers_do_not_say_so():
    p = sr.weight_proposal(CURRENT, {**RATES, "warm": 0.04}, {"median": 40, "measured": 12, "within_72h": 9},
                           mass={"applications": 0, "replies": 0}, paused=False)
    assert p["proposed"] == CURRENT and p["changes"] == []
    assert any("1.3x" in h for h in p["held"]) and any("median 40 h" in h for h in p["held"])


def test_a_number_not_measured_changes_nothing():
    p = sr.weight_proposal(CURRENT, None, {"median": None, "measured": 0, "within_72h": 0},
                           mass={"applications": 0, "replies": 0}, paused=False)
    assert p["proposed"] == CURRENT
    assert any("not measured" in h for h in p["held"])


def test_mass_tier_line_while_the_pause_stands():
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 5, "replies": 0}, paused=True)
    assert any("pause stands" in m and "5 mass-tier applications" in m and "0 replies" in m for m in p["mass"])
    one = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 1, "replies": 1}, paused=True)["mass"][0]
    assert "1 mass-tier application in" in one and "1 reply." in one
    assert sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 5, "replies": 0}, paused=False)["mass"] == []


def test_effect_counts_jobs_over_the_cut_and_the_top_10_moves():
    jobs = [{"id": f"j{i}", "c": {"warm": 1.0 if i < 3 else 0.0, "freshness": 0.5}} for i in range(20)]

    def scorer(rec, weights, cut):
        return sum(weights[k] * rec["c"].get(k, 0.0) for k in weights)
    e = sr.proposal_effect(jobs, {"warm": 6, "freshness": 18}, {"warm": 12, "freshness": 22}, cut=10, scorer=scorer)
    assert e["over_cut_before"] == 3 and e["over_cut_after"] == 20
    assert e["top10_kept"] == 10 - len(e["top10_in"]) and e["scored"] == 20
    assert e["cut_same_count"] == 23                  # the 3rd best score after (23, 23, 23, 11 ...) keeps 3 over the cut
    e2 = sr.proposal_effect(jobs, {"warm": 6, "freshness": 18}, {"warm": 6, "freshness": 18}, cut=100, scorer=scorer)
    assert e2["over_cut_before"] == 0 and e2["cut_same_count"] is None


def test_proposals_file_holds_the_hunt_yaml_snippet_and_says_never_applied(tmp_path):
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 5, "replies": 0}, paused=True)
    text = sr.render_proposals(DAY, p, {"over_cut_before": 250, "over_cut_after": 263, "top10_kept": 8,
                                        "top10_in": ["a", "b"], "top10_out": ["c", "d"], "scored": 2600, "cut": 69})
    assert text.startswith("# Ranking weight proposals 2026-10-02")
    assert "never applied by this script" in text and "targets/hunt.yaml" in text
    assert "  warm: 12" in text and "  freshness: 22" in text and "  role: 28" in text
    assert "250 to 263" in text
    assert "replies favour" not in text                                 # no claim the records do not show
    assert "—" not in text
    path = sr.write_proposals(tmp_path, DAY, text)
    assert path == tmp_path / "state" / "recruiter" / "proposals-2026-10-02.md" and path.read_text() == text
    assert not (tmp_path / "targets" / "hunt.yaml").exists()


def test_the_pause_reads_the_ruling_status(tmp_path, monkeypatch):
    real_get = sr.cfg.get
    monkeypatch.setattr(sr.cfg, "get", lambda key, default=None: "pause-1" if key == "policy.strategy.pause_ruling"
                        else real_get(key, default))
    (tmp_path / "hub").mkdir()
    (tmp_path / "hub" / "rulings.yaml").write_text("- {id: pause-1, status: standing}\n", encoding="utf-8")
    assert sr.pause_stands(tmp_path) is True
    (tmp_path / "hub" / "rulings.yaml").write_text("- {id: pause-1, status: superseded}\n", encoding="utf-8")
    assert sr.pause_stands(tmp_path) is False


def test_no_named_pause_ruling_means_no_pause(tmp_path):
    (tmp_path / "hub").mkdir()
    (tmp_path / "hub" / "rulings.yaml").write_text("- {id: pause-1, status: standing}\n", encoding="utf-8")
    assert sr.pause_stands(tmp_path) is False


def test_the_no_effect_run_writes_the_review_and_the_proposals(tmp_path, monkeypatch, capsys):
    fake = {"date": "2026-10-02", "flags": []}
    monkeypatch.setattr(sr, "review", lambda root, day: fake)
    monkeypatch.setattr(sr, "render", lambda r: "# review\n")
    seen = {}

    def fake_proposals(root, day, r, effect=True):
        seen["effect"] = effect
        return sr.weight_proposal(CURRENT, RATES, LATENCY, {"applications": 0, "replies": 0}, False), None
    monkeypatch.setattr(sr, "proposals", fake_proposals)
    assert sr.main(["--root", str(tmp_path), "--today", "2026-10-02", "--no-effect"]) == 0
    assert (tmp_path / "state" / "recruiter" / "review-2026-10-02.md").is_file()
    text = (tmp_path / "state" / "recruiter" / "proposals-2026-10-02.md").read_text()
    assert "warm: 6 to 12" in text and seen["effect"] is False
    assert "proposals-2026-10-02.md: 2 changes" in capsys.readouterr().out


def test_the_cut_that_keeps_the_count_joins_the_snippet():
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 0, "replies": 0}, paused=False)
    eff = {"over_cut_before": 239, "over_cut_after": 169, "top10_kept": 10, "top10_in": [], "top10_out": [],
           "scored": 2710, "cut": 69, "cut_same_count": 64.2}
    eff.update(cut_new=64, cut_new_keeps=241)
    text = sr.render_proposals(DAY, p, eff)
    assert "cut: 69 to 64" in text and "keeps 241 jobs at or over it (239 before)" in text and "\ncut: 64\n" in text


def test_the_new_cut_counts_ties_after_the_rescore():
    # 2 jobs over the cut before; after, three jobs tie at 20.5 at the 2nd place: a cut at 20 keeps 3, not 2
    jobs = [{"id": "a", "s": (15, 20.5)}, {"id": "b", "s": (12, 20.5)}, {"id": "c", "s": (5, 20.5)},
            {"id": "d", "s": (4, 3)}]

    def scorer(rec, w, cut):
        return rec["s"][w["k"]]
    e = sr.proposal_effect(jobs, {"k": 0}, {"k": 1}, cut=10, scorer=scorer)
    assert e["over_cut_before"] == 2 and e["cut_new"] == 20 and e["cut_new_keeps"] == 3
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 0, "replies": 0}, paused=False)
    text = sr.render_proposals(DAY, p, {**e, "top10_in": [], "top10_out": []})
    assert "cut: 10 to 20 keeps 3 jobs at or over it (2 before)" in text and "\ncut: 20\n" in text


def test_no_effect_still_writes_but_never_replaces_a_measured_proposal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sr, "review", lambda root, day: {"date": "2026-10-02", "flags": []})
    monkeypatch.setattr(sr, "render", lambda r: "# review\n")
    measured = {"over_cut_before": 3, "over_cut_after": 4, "top10_kept": 10, "top10_in": [], "top10_out": [],
                "scored": 20, "cut": 69}
    monkeypatch.setattr(sr, "proposals", lambda root, day, r, effect=True: (
        sr.weight_proposal(CURRENT, RATES, LATENCY, {"applications": 0, "replies": 0}, False),
        measured if effect else None))
    args = ["--root", str(tmp_path), "--today", "2026-10-02"]
    assert sr.main(args) == 0
    path = tmp_path / "state" / "recruiter" / "proposals-2026-10-02.md"
    first = path.read_text()
    assert "of 20 open jobs rescored" in first
    assert sr.main(args + ["--no-effect"]) == 0                         # the measured file stays
    assert path.read_text() == first
    assert "kept state/recruiter/proposals-2026-10-02.md" in capsys.readouterr().out
    assert "Read only." not in sr.__doc__.splitlines()[0]              # the module writes two files
    assert "--no-effect still writes" in sr.__doc__


def test_a_plain_run_with_no_change_replaces_a_measured_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sr, "review", lambda root, day: {"date": "2026-10-02", "flags": []})
    monkeypatch.setattr(sr, "render", lambda r: "# review\n")
    path = tmp_path / "state" / "recruiter" / "proposals-2026-10-02.md"
    path.parent.mkdir(parents=True)
    path.write_text("- jobs at or over the cut (69): 3 to 4, of 20 open jobs rescored\n", encoding="utf-8")
    still = sr.weight_proposal(CURRENT, {**RATES, "warm": 0.04}, {"median": 40, "measured": 12, "within_72h": 9},
                               {"applications": 0, "replies": 0}, False)
    monkeypatch.setattr(sr, "proposals", lambda root, day, r, effect=True: (still, None))
    assert sr.main(["--root", str(tmp_path), "--today", "2026-10-02"]) == 0
    text = path.read_text()
    assert "open jobs rescored" not in text and "nothing to measure: no weight change is proposed" in text
    assert "--no-effect" not in text and "kept" not in capsys.readouterr().out


def test_the_help_says_no_effect_still_writes(capsys):
    import pytest
    with pytest.raises(SystemExit):
        sr.main(["--help"])
    assert "still writes the review and the proposals" in " ".join(capsys.readouterr().out.split())


def test_the_new_cut_is_counted_with_the_gates_at_the_new_cut():
    # an off-role job is capped at cut - 1: 68 under cut 69, but 65 under the proposed cut 66
    jobs = [{"id": "on1", "raw": (70, 70)}, {"id": "on2", "raw": (70, 66)}, {"id": "off", "raw": (50, 80), "gated": True}]

    def scorer(rec, w, cut):
        total = rec["raw"][w["k"]]
        return min(total, cut - 1) if rec.get("gated") else total
    e = sr.proposal_effect(jobs, {"k": 0}, {"k": 1}, cut=69, scorer=scorer)
    assert e["over_cut_before"] == 2 and e["cut_new"] == 66
    assert e["cut_new_keeps"] == 2                       # on1 and on2; the gated job sits at 65 under cut 66


def test_an_empty_side_is_not_measured():
    p = sr.weight_proposal(CURRENT, {**RATES, "warm_n": 0, "warm_k": 0, "warm": 0.03}, LATENCY,
                           mass={"applications": 0, "replies": 0}, paused=False)
    assert p["proposed"]["warm"] == CURRENT["warm"]
    assert any("not measured" in h and "warm 0 of 0" in h for h in p["held"])
    assert not any("1.0x" in h for h in p["held"])


def test_the_lines_claim_only_what_is_measured():
    p = sr.weight_proposal(CURRENT, RATES, LATENCY, mass={"applications": 5, "replies": 0}, paused=True)
    fresh = next(c for c in p["changes"] if c["weight"] == "freshness")["why"]
    assert "higher in the list" in fresh and "so more applications" not in fresh
    assert "pause stands" in p["mass"][0] and "No weight change is proposed" in p["mass"][0]
