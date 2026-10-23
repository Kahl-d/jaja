"""Loop rates: interviews.rates counts by the recorded
`path` (person = warm, cold = cold, unknown = not counted) and falls back to applied_via only when `path` is absent;
strategy_review states and proposes a warm vs cold multiplier only with MIN_N (15) applications on each side, and keeps
the Recruiter's hand-written Decide section when it regenerates review-<date>.md and proposals-<date>.md. Fixtures only."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hunt_score  # noqa: E402
import interviews as iv  # noqa: E402
import strategy_review as sr  # noqa: E402
import tracker  # noqa: E402

NOW = datetime(2026, 10, 5, 23, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 5)
CURRENT = dict(hunt_score.DEFAULTS["weights"])
LATENCY = {"median": 40, "measured": 12, "within_72h": 9}
NO_MASS = {"applications": 0, "replies": 0}


def app(root: Path, slug: str, fields: dict[str, str], events: list[str]) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    head = "".join(f"{k}: {v}\n" for k, v in fields.items())
    body = "\n".join(tracker.format_event(f"2026-09-{day}T17:00:00Z", typ, "manual", 1.0, "fixture")
                     for day, typ in zip(("20", "24", "28"), events))
    (d / "application.md").write_text(f"---\nslug: {slug}\nstage: applied\n{head}---\n## Events\n{body}\n",
                                      encoding="utf-8")


def counts(r: dict) -> tuple:
    return r["warm_k"], r["warm_n"], r["cold_k"], r["cold_n"], r.get("unknown_n")


def test_path_is_used_over_applied_via(tmp_path):
    app(tmp_path, "person-via-ats", {"path": "person", "applied_via": "ats"}, ["applied", "screen"])
    app(tmp_path, "cold-via-referral", {"path": "cold", "applied_via": "referral"}, ["applied"])
    app(tmp_path, "cold-via-recruiter", {"path": "cold", "applied_via": "recruiter"}, ["applied", "rejected"])
    assert counts(iv.rates(tmp_path, NOW)) == (1, 1, 0, 2, 0)


def test_an_unknown_path_is_not_counted(tmp_path):
    app(tmp_path, "unknown-via-referral", {"path": "unknown", "applied_via": "referral"}, ["applied", "screen"])
    app(tmp_path, "unknown-via-ats", {"path": "unknown", "applied_via": "ats"}, ["applied"])
    app(tmp_path, "person", {"path": "person"}, ["applied"])
    assert counts(iv.rates(tmp_path, NOW)) == (0, 1, 0, 0, 2)
    assert iv.channel_counts(tmp_path, NOW)["unknown"] == {"applied": 2, "interviewed": 1}


def test_applied_via_decides_only_when_path_is_absent(tmp_path):
    app(tmp_path, "via-referral", {"applied_via": "referral"}, ["applied", "screen"])
    app(tmp_path, "via-ats", {"applied_via": "ats"}, ["applied"])
    app(tmp_path, "via-none", {}, ["applied", "screen"])
    app(tmp_path, "not-applied", {"path": "person"}, ["saved"])
    assert counts(iv.rates(tmp_path, NOW)) == (1, 1, 0, 1, 1)
    assert [iv.channel_of(m) for m in ({"path": "person", "applied_via": "ats"}, {"path": "cold", "applied_via":
            "referral"}, {"applied_via": "recruiter"}, {"applied_via": "ats"}, {})] == \
        ["warm", "cold", "warm", "cold", "unknown"]


def fake_rates(monkeypatch, warm_k, warm_n, cold_k, cold_n, warm=0.19, cold=0.014):
    rates = {"warm": warm, "cold": cold, "warm_k": warm_k, "warm_n": warm_n, "cold_k": cold_k, "cold_n": cold_n,
             "unknown_n": 2}
    monkeypatch.setattr(iv, "rates", lambda root, now: rates)
    monkeypatch.setattr(iv, "booked", lambda root, day: {"this_week": 0, "last_week": 2, "week_of": "2026-10-05",
                                                         "items": []})
    monkeypatch.setattr(iv, "target", lambda root: 2)
    return rates


def review_text(tmp_path, view) -> str:
    return sr.render({"date": DAY.isoformat(), "applications_on_record": 44, "clock": {"day": 3}, "windows": {},
                      "interviews": view, "flags": []})


def test_no_multiplier_below_min_n(tmp_path, monkeypatch):
    rates = fake_rates(monkeypatch, 2, 4, 0, 38)                      # a small funnel by path
    v = sr.interviews_view(tmp_path, DAY, {"totals": {"applications": 44}})
    assert v["warm_vs_cold"] is None and not any("worth" in p for p in v["proposals"])
    assert "person 2 of 4, cold 0 of 38" in v["small_n"] and "15 applications on each side" in v["small_n"]
    text = review_text(tmp_path, v)
    assert "worth about" not in text and "x a cold" not in text
    assert "person 2 of 4 (n=4), cold 0 of 38 (n=38), unknown 2 not counted" in text
    assert "- n is too small for a multiplier: person 2 of 4, cold 0 of 38" in text
    p = sr.weight_proposal(CURRENT, rates, LATENCY, NO_MASS, k206=False)
    assert p["proposed"]["warm"] == CURRENT["warm"] and not any(c["weight"] == "warm" for c in p["changes"])
    assert any("n is too small for a multiplier" in h and "warm 2 of 4" in h and "cold 0 of 38" in h
               for h in p["held"])
    assert "worth" not in sr.render_proposals(DAY, p, None)
    one_side = fake_rates(monkeypatch, 6, 20, 0, 14, warm=0.3)       # one side at 15 is not enough
    assert sr.interviews_view(tmp_path, DAY, {"totals": {}})["warm_vs_cold"] is None
    assert sr.weight_proposal(CURRENT, one_side, LATENCY, NO_MASS, k206=False)["proposed"]["warm"] == CURRENT["warm"]


def test_the_multiplier_is_still_proposed_at_min_n(tmp_path, monkeypatch):
    assert sr.MIN_N == 15
    rates = fake_rates(monkeypatch, 5, 15, 0, 15, warm=0.3, cold=0.03)
    v = sr.interviews_view(tmp_path, DAY, {"totals": {"applications": 30}})
    assert v["warm_vs_cold"] == 10.0 and v["small_n"] is None
    assert any("worth about 10.0x" in p for p in v["proposals"])
    assert "n is too small" not in review_text(tmp_path, v)
    p = sr.weight_proposal(CURRENT, rates, LATENCY, NO_MASS, k206=False)
    assert p["proposed"]["warm"] == CURRENT["warm"] * 2
    assert "worth 10.0x" in next(c for c in p["changes"] if c["weight"] == "warm")["why"]


def run_main(tmp_path, monkeypatch) -> tuple[str, str]:
    monkeypatch.setattr(sr, "review", lambda root, day: {"date": DAY.isoformat(), "flags": []})
    monkeypatch.setattr(sr, "render", lambda r: "# Strategy review 2026-10-05\n\nnew numbers\n")
    monkeypatch.setattr(sr, "proposals", lambda root, day, r, effect=True: (
        sr.weight_proposal(CURRENT, None, LATENCY, NO_MASS, False), None))
    assert sr.main(["--root", str(tmp_path), "--today", DAY.isoformat()]) == 0
    folder = tmp_path / "state" / "recruiter"
    return ((folder / "review-2026-10-05.md").read_text(encoding="utf-8"),
            (folder / "proposals-2026-10-05.md").read_text(encoding="utf-8"))


DECIDE = ("## Decide\n\nWritten by the Recruiter.\n\n**Decision.** The example multiplier line is withdrawn.\n"
          "- S1 cold: proposed N_cold = 20\n")


def test_the_decide_section_is_kept_on_regeneration(tmp_path, monkeypatch):
    folder = tmp_path / "state" / "recruiter"
    folder.mkdir(parents=True)
    (folder / "review-2026-10-05.md").write_text("# Strategy review 2026-10-05\n\nold numbers\n\n" + DECIDE,
                                                 encoding="utf-8")
    (folder / "proposals-2026-10-05.md").write_text("# Ranking weight proposals\n\nold\n\n" + DECIDE + "kept too\n",
                                                    encoding="utf-8")
    review, props = run_main(tmp_path, monkeypatch)
    assert review == "# Strategy review 2026-10-05\n\nnew numbers\n\n" + DECIDE           # verbatim, after the numbers
    assert "old numbers" not in review
    assert props.endswith("\n\n" + DECIDE + "kept too\n") and props.startswith("# Ranking weight proposals 2026-10-05")
    assert run_main(tmp_path, monkeypatch) == (review, props)                               # a rerun changes nothing


def test_a_new_file_ends_with_the_decide_placeholder(tmp_path, monkeypatch):
    folder = tmp_path / "state" / "recruiter"
    folder.mkdir(parents=True)
    (folder / "review-2026-10-05.md").write_text("# Strategy review 2026-10-05\n\nold, no decide\n", encoding="utf-8")
    review, props = run_main(tmp_path, monkeypatch)
    for text in (review, props):
        assert text.endswith("\n\n## Decide\n\n" + sr.DECIDE_PLACEHOLDER + "\n")
        assert text.count("## Decide") == 1 and "the Recruiter writes" in text
    assert "old, no decide" not in review
