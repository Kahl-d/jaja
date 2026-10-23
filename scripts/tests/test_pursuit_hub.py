"""pursuit_hub.py: pursuits/<slug>/HUB.md, the generated join over everything one pursuit names."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pursuit_hub as ph  # noqa: E402
import pursuits as pu  # noqa: E402

APP1 = "acme-motor-ai-engineer-202609"
APP2 = "acme-motor-data-scientist-202609"
SLUG = "acme-via-sam-referrer-202609"
MASTER = "acme-referral-master-202609"


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    os_root = tmp_path / "os"
    js = tmp_path / "js"
    w(js / "companies" / "acme.md", "---\nslug: acme\nname: Acme Motor Company\ntier: strong\ne_verify: \"yes\"\n"
                                    "last_researched: 2026-09-27\n---\n")
    w(js / "legal" / "checks" / "acme.md", "---\ncompany: Acme Motor Company\nverdict: clear\ne_verify: \"yes\"\n"
                                           "checked_at: 2026-09-27\n---\n")
    for pid, name, cs, role in (("sam-referrer", "Sam Referrer", "acme-motor-company", "engineer"),
                                ("ida-introducer", "Ida Introducer", "examplestate", "alumni"),
                                ("lee-other", "Lee Other", "acme", "recruiter")):
        w(js / "prospects" / f"{pid}.md", f"---\nid: {pid}\nname: {name}\ncompany_slug: {cs}\nrole: {role}\n"
                                          f"status: new\npriority: P1\nnext_touch_at: 2026-09-29\n---\n")
    for a, stage, score, at in ((APP1, "tailored", 4.4, "2026-09-27T16:00:00Z"), (APP2, "fit_checked", 4.0, "2026-09-20T10:00:00Z")):
        rv = f"\n            resume_variant: '[[{a}]]'" if a == APP1 else ""   # recorded, never guessed 
        w(js / "applications" / a / "application.md", f"""
            ---
            id: {a}
            company: Acme Motor Company
            company_slug: acme-motor
            title: role
            stage: {stage}{rv}
            ---
            # role

            ## Events
            - {at} · fit_checked · manual · conf=1.00 · fit.md {score}
            """)
        w(js / "applications" / a / "fit.md", f"---\nfit_score: {score}\nfit_verdict: apply\n---\n")
        w(js / "applications" / a / "HUB.md", "hub\n")
        w(js / "resumes" / "variants" / a / "meta.yaml", "editorial_verdict: approved\n")
    w(js / "resumes" / "variants" / MASTER / "meta.yaml", "editorial_verdict: approved\nbase_of: general\n")
    w(js / "resumes" / "variants" / MASTER / "review.md", "---\nverdict: approved\n---\nreview\n")
    w(js / "resumes" / "variants" / MASTER / "NOTES.md", "notes\n")
    w(js / "research" / "targets" / "case-sam-referrer-202609.md", "---\ntype: research\nstatus: case-ready\n---\ncase\n")
    w(js / "hub" / "intake" / "2026-09-27-sam-referrer.md", "intake\n")
    w(js / "outbox" / "O-2026-0002.md", """
        ---
        id: O-2026-0002
        prospect: sam-referrer
        channel: linkedin-connect
        kind: intro
        created: 2026-09-27
        status: pending
        approval: A-2026-0003
        ---
        Hi Sam, SECRET MESSAGE TEXT
        """)
    w(js / "outbox" / "O-2026-0009.md", "---\nid: O-2026-0009\nprospect: lee-other\nchannel: email\nkind: intro\n"
                                        "created: 2026-09-26\nstatus: drafted\n---\nHi Lee\n")
    (js / "touches").mkdir()
    (js / "touches" / "2026-09.jsonl").write_text("\n".join(json.dumps(t) for t in [
        {"at": "2026-09-26T18:00:00Z", "prospect": "ida-introducer", "channel": "linkedin-message", "direction": "in",
         "kind": "intro", "status": "delivered", "key": "li-1", "summary": "PRIVATE TOUCH SUMMARY"},
        {"at": "2026-09-25T18:00:00Z", "prospect": "lee-other", "channel": "email", "direction": "in",
         "kind": "reply", "status": "delivered", "key": "g-7", "summary": "x"}]) + "\n")
    w(os_root / "work" / "approvals" / "A-2026-0003.md", "---\ntype: approval\nid: A-2026-0003\naction: post-linkedin\n"
                                                         "status: pending\ncreated: 2026-09-27\nexpires: 2026-10-04\n---\n")
    assert pu.main(["new", "--root", str(js), "--today", "2026-09-27", "--company", "Acme Motor Company",
                    "--kind", "referral", "--referrer", "sam-referrer", "--introducer", "ida-introducer",
                    "--contact", "jo-doe", "--case", "research/targets/case-sam-referrer-202609.md",
                    "--intake", "hub/intake/2026-09-27-sam-referrer.md", "--application", APP1,
                    "--application", APP2, "--master-variant", MASTER, "--stage", "review", "--waiting-on", "owner",
                    "--next-action", "Alex reviews the bundle", "--next-action-at", "2026-09-28"]) == 0
    assert pu.main(["decision", SLUG, "--root", str(js), "--today", "2026-09-27", "--ruling", "R-7",
                    "--text", "one master resume to Sam"]) == 0
    return js, os_root


def hub(js: Path) -> str:
    return (js / "pursuits" / SLUG / "HUB.md").read_text(encoding="utf-8")


def section(text: str, name: str) -> str:
    return text.split(f"## {name}", 1)[1].split("\n## ", 1)[0]


def refresh(js: Path, os_root: Path, *extra: str) -> int:
    return ph.main(["--root", str(js), "--os-root", str(os_root), *extra])


def test_the_hub_opens_with_the_state_of_the_direction(world):
    js, os_root = world
    assert refresh(js, os_root) == 0
    text = hub(js)
    assert text.splitlines()[0] == ph.header(f"pursuits/{SLUG}/pursuit.md and the records it names")
    assert "# Acme Motor Company via Sam Referrer, introduced by Ida Introducer" in text
    state = section(text, "State")
    assert "stage review · waiting on owner · next: Alex reviews the bundle · due 2026-09-28" in state


def test_people_carry_their_part_and_a_missing_record_is_named(world):
    js, os_root = world
    refresh(js, os_root)
    people = section(hub(js), "People")
    assert "[Sam Referrer](../../prospects/sam-referrer.md) · referrer" in people
    assert "[Ida Introducer](../../prospects/ida-introducer.md) · introducer" in people
    assert "missing: prospects/jo-doe.md" in people
    assert "Lee Other" not in people


def test_company_joins_through_the_canonical_slug(world):
    js, os_root = world
    refresh(js, os_root)
    company = section(hub(js), "Company")
    assert "companies/acme.md" in company and "legal/checks/acme.md" in company and "verdict clear" in company


def test_each_pick_shows_stage_fit_variant_and_its_role_hub(world):
    js, os_root = world
    refresh(js, os_root)
    apps = section(hub(js), "Applications")
    assert f"applications/{APP1}/HUB|{APP1}]]" in apps
    assert "stage tailored · fit 4.4 · variant approved" in apps
    assert "stage fit_checked · fit 4.0 · variant not recorded" in apps        # a folder named like the slug is not read


def test_resumes_show_the_master_and_its_review(world):
    js, os_root = world
    refresh(js, os_root)
    resumes = section(hub(js), "Resumes")
    assert f"resumes/variants/{MASTER}/" in resumes and "editorial_verdict approved" in resumes
    assert "review.md" in resumes and "verdict approved" in resumes


def test_outreach_joins_records_and_never_copies_message_text(world):
    js, os_root = world
    refresh(js, os_root)
    text = hub(js)
    out = section(text, "Outreach and approvals")
    assert "O-2026-0002" in out and "linkedin-connect" in out and "pending" in out
    assert "`work/approvals/A-2026-0003.md` · post-linkedin · status pending · expires 2026-10-04" in out
    assert "O-2026-0009" not in out                                 # another company's person
    assert "SECRET MESSAGE TEXT" not in text and "PRIVATE TOUCH SUMMARY" not in text


def test_one_timeline_oldest_first(world):
    js, os_root = world
    refresh(js, os_root)
    lines = [l for l in section(hub(js), "Timeline").splitlines() if l.startswith("- ")]
    assert lines[0] == f"- 2026-09-20T10:00:00Z · {APP2} · fit_checked · manual"
    assert lines[1].startswith("- 2026-09-26T18:00:00Z · touch · in · linkedin-message")
    assert any("R-7 · one master resume to Sam" in l for l in lines)
    assert any(f"{APP1} · fit_checked" in l for l in lines)
    assert any("opened at stage review" in l for l in lines)
    stamps = [l[2:12] for l in lines]
    assert stamps == sorted(stamps)


def test_refresh_is_deterministic_check_reports_stale_and_a_gone_pursuit_is_pruned(world, capsys):
    js, os_root = world
    assert refresh(js, os_root, "--check") == 1
    refresh(js, os_root)
    capsys.readouterr()
    assert refresh(js, os_root) == 0 and "pursuit hubs: 0 changed" in capsys.readouterr().out
    assert refresh(js, os_root, "--check") == 0
    (js / "pursuits" / SLUG / "pursuit.md").unlink()
    refresh(js, os_root)
    assert not (js / "pursuits" / SLUG / "HUB.md").exists()


def test_no_pursuits_writes_nothing(tmp_path, capsys):
    assert ph.main(["--root", str(tmp_path), "--os-root", str(tmp_path)]) == 0
    assert "pursuit hubs: 0 changed" in capsys.readouterr().out and not (tmp_path / "pursuits").exists()


def test_a_master_without_its_review_says_so(world):
    js, os_root = world
    (js / "resumes" / "variants" / MASTER / "review.md").unlink()
    refresh(js, os_root)
    assert f"missing: resumes/variants/{MASTER}/review.md (resume-editor)" in section(hub(js), "Resumes")


def test_every_link_in_the_hub_resolves_in_the_os_lint(world, monkeypatch):
    # [[work/approvals/A-...]] is not a lint link target (work/ is not one), so approvals are named by path, not linked
    import shutil
    import graph_links
    monkeypatch.setattr(graph_links, "VAULT_PREFIX", "domains/career/projects/job-search")   # the layout lint scans
    from js_common import OS_ROOT
    sys.path.insert(0, str(__import__("agency_paths").KERNEL_DIR))
    import lint
    js, os_root = world
    dept = os_root / "domains" / "career" / "projects" / "job-search"
    dept.parent.mkdir(parents=True)
    shutil.move(str(js), str(dept))
    assert refresh(dept, os_root) == 0
    assert "A-2026-0003" in section(hub(dept), "Outreach and approvals")
    bad = [f.message for f in lint.check_links(os_root) if f.code == "broken-link" and "HUB.md" in f.path]
    assert bad == []


def test_a_pick_reads_fit_md_score_too(world):
    js, os_root = world
    w(js / "applications" / APP2 / "fit.md", "---\nscore: 4.2\nverdict: maybe\n---\n")
    refresh(js, os_root)
    assert "stage fit_checked · fit 4.2" in section(hub(js), "Applications")
