"""next_up.py: NEXT-UP generated from the records, warm first, people first, with the orchestrator's and the owner's overrides read from their own file and never written."""
from __future__ import annotations

from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import agency_status  # noqa: E402
import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

import fetch_ats as fa  # noqa: E402
import next_up as nu  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm, write_record  # noqa: E402

NOW = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
JD = "We build LLM agents and evaluation harnesses in Python for our customers. " * 6


def _job(root, jid, company, slug, title="AI Engineer", status="discovered", fit=4.3, posted="2026-10-02T17:00:00Z"):
    rec = fa.make_record({"name": company, "slug": slug}, title=title, source="ashby",
                         source_url=f"https://jobs.ashbyhq.com/{slug}/{jid}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted, location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                         employment_type=None, jd_text=JD, now=NOW)
    rec.update({"id": jid, "status": status, "fit_score": fit, "gate": "pass", "sponsorship": "yes"})
    fa.save_job(root, rec)


def _app(root, slug, company, job, stage="fit_checked", events=(("2026-10-02T17:00:00Z", "fit_checked"),), variant=None):
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(tr.format_event(at, t, "manual", 1.0, "e") for at, t in events)
    meta = {"id": slug, "company": company, "title": "AI Engineer", "job": f"[[{job}]]", "stage": stage}
    if variant:
        meta["resume_variant"] = variant
    path.write_text(fm.dump(meta, f"# {slug}\n\n## Events\n{lines}\n"))


def _page(root, name, job, inline=False, built="2026-10-02T10:00:00"):
    """A page folder; whether apply_pack would ship it is stubbed by name: names ending '-ok' pass (see `ship`)."""
    d = root / "resumes" / "variants" / name / ("inline" if inline else "")
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.yaml").write_text(f"job: jobs/{job}.md\nbuilt_at: '{built}'\n")


@pytest.fixture(autouse=True)
def ship(monkeypatch):
    """apply_pack's own checks have their own tests; here a page ships when its folder name ends in -ok."""
    monkeypatch.setattr(nu, "signed", lambda ctx, name, app: None if name.endswith("-ok") else f"refused: {name} not signed")
    yield


def _note(root, oid, prospect, company_slug, status="approved", kind="intro"):
    write_record(root / "prospects" / f"{prospect}.md", {"id": prospect, "name": "A Person", "company_slug": company_slug},
                 "# p\n")
    write_record(root / "outbox" / f"{oid}.md", {"id": oid, "prospect": prospect, "channel": "linkedin-connect",
                                                 "kind": kind, "status": status}, "note\n")


def world(tmp_path):
    root = tmp_path / "js"
    for d in ("jobs", "applications", "targets", "state", "hub", "prospects", "outbox"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text("companies: []\n")
    _job(root, "acme-1", "Acme", "acme")
    _app(root, "acme-ai-202610", "Acme", "acme-1", variant="acme-ai-202610-b75-ok")
    _page(root, "acme-ai-202610-b75-ok", "acme-1", inline=True)          # the current pages keep meta in inline/
    _job(root, "beta-1", "Beta", "beta")
    _app(root, "beta-ai-202610", "Beta", "beta-1")
    _page(root, "beta-ai-202610-b75", "beta-1")
    _job(root, "zeta-1", "Zeta", "zeta")
    _app(root, "zeta-ai-202610", "Zeta", "zeta-1")
    _note(root, "O-1", "pat-zeta", "zeta")
    _job(root, "delta-1", "Delta", "delta")
    _app(root, "delta-ai-202610", "Delta", "delta-1", stage="on_hold")
    _job(root, "eps-1", "Eps", "eps", status="skipped")
    _page(root, "eps-ai-202610-b75-ok", "eps-1")
    return root


def _rows(res):
    return {r["company"]: r for r in res["rows"]}


def test_states_come_from_the_records(tmp_path):
    root = world(tmp_path)
    rows = _rows(nu.build(root, NOW))
    assert rows["Acme"]["state"] == "READY" and rows["Acme"]["page"] == "acme-ai-202610-b75-ok"   # found in inline/
    assert rows["Beta"]["state"] == "PAGE" and "not signed" in rows["Beta"]["why"]        # apply_pack's own refusal
    assert rows["Zeta"]["state"] == "PEOPLE FIRST" and rows["Zeta"]["notes"] == ["O-1"]
    assert rows["Delta"]["state"] == "BLOCKED"


def test_people_first_ranks_above_a_cold_ready_row(tmp_path):
    root = world(tmp_path)
    order = [r["company"] for r in nu.build(root, NOW)["rows"]]
    assert order.index("Zeta") < order.index("Acme") < order.index("Beta")


def test_overrides_pin_stop_and_waive_are_read_never_written(tmp_path):
    root = world(tmp_path)
    text = ("applying: paused\noverrides:\n"
            "  - {id: beta-ai-202610, action: pin, rank: 1, by: orchestrator, date: 2026-10-04}\n"
            "  - {id: acme-1, action: stop, note: owner said no, by: alex, date: 2026-10-04}\n"
            "  - {id: zeta-ai-202610, action: waive-people-first, by: alex, date: 2026-10-04}\n")
    (root / "hub" / "NEXT-UP-overrides.yaml").write_text(text)
    res = nu.build(root, NOW)
    rows = _rows(res)
    assert res["rows"][0]["company"] == "Beta"
    assert rows["Acme"]["state"] == "BLOCKED" and "owner said no" in rows["Acme"]["why"]
    assert rows["Zeta"]["state"] == "JOB"
    assert (root / "hub" / "NEXT-UP-overrides.yaml").read_text() == text


def test_a_page_whose_job_was_skipped_is_listed_apart_never_dropped(tmp_path):
    root = world(tmp_path)
    res = nu.build(root, NOW)
    assert "Eps" not in _rows(res)
    assert [x["job"] for x in res["left_out"]] == ["eps-1"]
    assert "Pages left out" in nu.render(res, "2026-10-04")


def test_one_row_per_job_when_two_folders_share_it(tmp_path):
    root = world(tmp_path)
    _app(root, "acme-ai-202610-dup", "Acme", "acme-1")
    assert [r["company"] for r in nu.build(root, NOW)["rows"]].count("Acme") == 1


def test_the_written_list_is_what_agency_status_counts(tmp_path):
    root = world(tmp_path)
    assert nu.main(["--root", str(root), "--now", NOW.isoformat(), "--write"]) == 0
    counts = agency_status.queue(root)
    assert counts == {"READY": 1, "PAGE": 1, "PEOPLE FIRST": 1, "BLOCKED": 1}
    assert "PAUSED" in (root / "hub" / "NEXT-UP.md").read_text()


def test_network_map_reads_the_company_from_the_generated_list(tmp_path):
    import network_map
    root = world(tmp_path)
    nu.main(["--root", str(root), "--now", NOW.isoformat(), "--write"])
    assert [r["company"] for r in network_map.next_up(root)] == ["Zeta", "Acme", "Beta", "Delta"]


def test_a_signed_page_the_record_does_not_link_says_link_it(tmp_path):
    """apply_pack builds from the page the record links; a signed page elsewhere is not READY yet."""
    root = world(tmp_path)
    _page(root, "beta-ai-202610-b76", "beta-1", built="2026-10-03T10:00:00")          # newer, not signed
    _page(root, "beta-ai-202610-b74-ok", "beta-1", built="2026-10-01T10:00:00")       # older, ships
    row = _rows(nu.build(root, NOW))["Beta"]
    assert (row["state"], row["page"]) == ("PAGE", "beta-ai-202610-b74-ok")
    assert "set resume_variant to beta-ai-202610-b74-ok" in row["why"]


def test_tailored_without_a_page_that_ships_is_not_ready(tmp_path):
    root = world(tmp_path)
    _job(root, "zed-1", "Zed", "zed")
    _app(root, "zed-ai-202610", "Zed", "zed-1", stage="tailored")
    assert _rows(nu.build(root, NOW))["Zed"]["state"] == "JOB"


def test_only_an_unsent_first_contact_note_puts_people_first(tmp_path):
    root = world(tmp_path)
    for i, (status, kind) in enumerate((("sent", "intro"), ("rejected", "intro"), ("approved", "reply"),
                                        ("approved", "follow_up"))):
        _job(root, f"co{i}-1", f"Co{i}", f"co{i}")
        _app(root, f"co{i}-ai-202610", f"Co{i}", f"co{i}-1")
        _note(root, f"O-{i}0", f"p-{i}", f"co{i}", status=status, kind=kind)
    rows = _rows(nu.build(root, NOW))
    assert [rows[f"Co{i}"]["state"] for i in range(4)] == ["JOB"] * 4


def test_a_note_finds_its_company_through_the_alias(tmp_path):
    root = world(tmp_path)
    (root / "companies").mkdir()
    (root / "companies" / "fenwick.md").write_text("---\nslug: fenwick\nname: Fenwick\naliases: [fenwick-motor, Fenwick Motor Company]\n---\n")
    _job(root, "fenwick-1", "Fenwick", "fenwick")
    _app(root, "fenwick-ai-202610", "Fenwick", "fenwick-1")
    _note(root, "O-9", "casey", "fenwick-motor")
    assert _rows(nu.build(root, NOW))["Fenwick"]["state"] == "PEOPLE FIRST"


def test_a_record_wins_over_a_fit_only_twin_and_an_applied_role_is_never_listed(tmp_path):
    root = world(tmp_path)
    twin = root / "applications" / "delta-ai-202609"                   # sorts first by name; fit-only
    twin.mkdir()
    (twin / "fit.md").write_text(fm.dump({"job": "[[delta-1]]", "score": 4.5}, "# fit\n"))
    _job(root, "omega-1", "Omega", "omega")
    _app(root, "omega-ai-202610", "Omega", "omega-1", stage="applied")
    rows = _rows(nu.build(root, NOW))
    assert rows["Delta"]["state"] == "BLOCKED" and rows["Delta"]["slug"] == "delta-ai-202610"
    assert "Omega" not in rows


def test_a_package_only_folder_below_the_fit_gate_is_not_listed(tmp_path):
    root = world(tmp_path)
    f = root / "applications" / "low-ai-202610"
    f.mkdir()
    _job(root, "low-1", "Low", "low")
    (f / "fit.md").write_text(fm.dump({"job": "[[low-1]]", "score": 3.6}, "# fit\n"))
    assert "Low" not in _rows(nu.build(root, NOW))


def test_a_years_ask_above_the_owners_count_with_no_person_inside_is_blocked(tmp_path, monkeypatch):
    root = world(tmp_path)
    monkeypatch.setattr(nu.fit_gates, "required_years", lambda jd: 5)
    rows = _rows(nu.build(root, NOW))
    assert rows["Acme"]["state"] == "BLOCKED" and "no person inside" in rows["Acme"]["why"]


def test_an_older_posting_ranks_below_a_fresh_one_of_the_same_kind(tmp_path):
    root = world(tmp_path)
    _job(root, "old-1", "Old", "old", posted="2026-09-15T17:00:00Z")      # 19 days old: decayed, not yet stale
    _app(root, "old-ai-202610", "Old", "old-1")
    _job(root, "new-1", "New", "new")
    _app(root, "new-ai-202610", "New", "new-1")
    rows = _rows(nu.build(root, NOW))
    assert rows["Old"]["state"] == rows["New"]["state"] == "JOB"
    assert rows["Old"]["per_hour"] < rows["New"]["per_hour"]


def test_a_pin_without_a_rank_still_pins_and_mixed_ranks_do_not_crash(tmp_path):
    root = world(tmp_path)
    (root / "hub" / "NEXT-UP-overrides.yaml").write_text(
        "overrides:\n  - {id: beta-1, action: pin}\n  - {id: delta-1, action: pin, rank: '2'}\n")
    order = [r["company"] for r in nu.build(root, NOW)["rows"]]
    assert order[:2] == ["Delta", "Beta"]


def test_signed_runs_apply_pack_checks_in_order_and_falls_back_to_inline(monkeypatch):
    """signed() is the one place READY is decided: apply_pack's check_variant then check_recruiter."""
    import apply_pack as ap
    monkeypatch.undo()                                       # the real signed(), with apply_pack's checks stubbed
    calls = []

    def variant(ctx, name, app, choice):
        calls.append(("variant", choice, app["resume_variant"]))
        if choice == "classic":
            raise ap.Refusal("classic render missing")

    monkeypatch.setattr(ap, "check_variant", variant)
    monkeypatch.setattr(ap, "variant_dir", lambda root, name, app: Path("/v") / name)
    monkeypatch.setattr(ap, "rel", lambda root, p: str(p))
    monkeypatch.setattr(ap, "sha256_file", lambda p: "abc")
    monkeypatch.setattr(ap, "check_recruiter", lambda vdir, vrel, sha: calls.append(("recruiter", vrel, sha)))

    class Ctx:
        root = Path("/root")

    assert nu.signed(Ctx(), "p-b75", {"stage": "fit_checked"}) == "inline"
    assert calls == [("variant", "classic", "p-b75"), ("variant", "inline", "p-b75"), ("recruiter", "/v/p-b75", "abc")]
    monkeypatch.setattr(ap, "check_recruiter", lambda *a: (_ for _ in ()).throw(ap.Refusal("verdict ADVANCE")))
    assert nu.signed(Ctx(), "p-b75", {}) == "refused: verdict ADVANCE"
    assert nu.signed(None, "p", {}).startswith("refused: ")


def test_a_note_to_a_person_inside_opens_a_years_stretch(tmp_path, monkeypatch):
    root = world(tmp_path)
    monkeypatch.setattr(nu.fit_gates, "required_years", lambda jd: 5)
    rows = _rows(nu.build(root, NOW))
    assert rows["Zeta"]["state"] == "PEOPLE FIRST" and rows["Zeta"]["stretch"] is True


def test_a_linked_page_that_does_not_ship_says_why():
    """The linked page's refusal is the row's reason (never "page x: None")."""
    assert nu.page_state(object(), ["x"], {}, "x") == ("PAGE", "x", "x not signed", "classic")
    assert nu.page_state(object(), [], {}, "x") == ("PAGE", "x", "x not signed", "classic")
