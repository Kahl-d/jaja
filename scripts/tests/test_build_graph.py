"""build_graph.py: the tracking graph built from the records, never editing one."""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_graph as bg  # noqa: E402

TODAY = date(2026, 9, 28)
EM = chr(0x2014)


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def js(tmp_path: Path) -> Path:
    root = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    w(root / "companies" / "acme.md", "---\nslug: acme\nname: Acme\ntier: strong\nsponsorship_stance: sponsors\n---\n")
    w(root / "companies" / "emptyco.md", "---\nslug: emptyco\nname: EmptyCo\ntier: good\n---\n")      # no roles
    w(root / "targets" / "companies.yaml", "companies:\n  - name: Watchy\n    slug: watchy\n    tier: dream\n"
                                           "    sponsorship_stance: unknown\n")
    w(root / "jobs" / "acme-man-1.md", f"---\ntype: job\nid: acme-man-1\ncompany: Acme\ncompany_slug: acme\n"
                                       f"title: ML Engineer {EM} Agents\nstatus: queued\nfit_score: null\n"
                                       f"platform: company-ats\nfirst_seen: '2026-09-20T10:00:00Z'\n---\n")
    w(root / "jobs" / "acme-man-2.md", "---\ntype: job\nid: acme-man-2\ncompany: Acme\ncompany_slug: acme\n"
                                       "title: Data Engineer\nstatus: discovered\nplatform: linkedin\n"
                                       "first_seen: '2026-09-21T10:00:00Z'\n---\n")
    w(root / "applications" / "acme-ml-202609" / "application.md",
      "---\ntype: application\nid: acme-ml-202609\njob: '[[acme-man-1]]'\ncompany: Acme\ncompany_slug: acme\n"
      "title: ML Engineer\nstage: applied\nnext_action: follow up\nnext_action_at: '2026-10-03'\n---\n"
      "# x\n\n## Events\n- 2026-09-27T10:00:00Z · applied · manual · created from [[acme-man-1]]\n")
    # Pat works at Acme and introduced Rae (Betaco): one person linked to two companies
    w(root / "prospects" / "pat-lee.md", "---\ntype: prospect\nid: pat-lee\nname: Pat Lee\ncompany: Acme\n"
                                         "company_slug: acme\nstatus: triaged\npriority: P2\nwarmth: 3\n"
                                         "channels: [linkedin]\napplications: [acme-ml-202609]\n---\n")
    w(root / "prospects" / "rae-kim.md", "---\ntype: prospect\nid: rae-kim\nname: Rae Kim\ncompany: Betaco\n"
                                         "company_slug: betaco\nstatus: replied\npriority: P1\nwarmth: 2\n"
                                         "relation: introduced by pat-lee\nlast_outbound_at: '2026-09-25T10:00:00Z'\n"
                                         "next_touch_at: 2026-10-01\n---\n")
    w(root / "prospects" / "broken.md", "---\nid: broken\nname: 'it's broken'\n---\n")
    w(root / "touches" / "2026-09.jsonl",
      json.dumps({"at": "2026-09-27T09:00:00Z", "prospect": "rae-kim", "direction": "in", "channel": "email",
                  "kind": "reply", "status": "delivered", "summary": "SECRET MESSAGE TEXT"}) + "\n")
    w(root / "pursuits" / "betaco-via-rae-kim-202609" / "pursuit.md",
      "---\ntype: pursuit\nid: betaco-via-rae-kim-202609\ncompany: Betaco\ncompany_slug: betaco\nkind: referral\n"
      "referrer: rae-kim\nintroducer: pat-lee\ncontacts: []\ncase: research/targets/case-betaco-rae-202609.md\n"
      "intake: hub/intake/2026-09-28-betaco.md\napplications: []\nstage: sent\nwaiting_on: contact:rae-kim\n"
      "next_action: wait for Rae\nnext_action_at: 2026-10-04\n---\n# p\n\n## Log\n- 2026-09-28 · opened\n")
    w(root / "research" / "targets" / "case-betaco-rae-202609.md",
      "---\ntype: research\ncompany: betaco\nstatus: research\n---\n# Betaco, through Rae Kim: stage 1 case\n")
    w(root / "research" / "targets" / "evidence" / "linkedin-lin-wu-2026-09-28.txt",
      "# snapshot\nName: Lin Wu (1st)\nHeadline: Physics AI\nCurrent: Betaco Technologies; UC Berkeley\n")
    w(root / "research" / "targets" / "evidence" / "betaco-careers-2026-09-28.json", "{}\n")
    w(root / "hub" / "intake" / "2026-09-28-betaco.md",
      "---\ntitle: Intake, TARGET 3 (QUEUED): Betaco via Rae Kim\ncase: case-betaco-rae-202609\n---\n# Intake\n")
    w(root / "state" / "inbox" / "prospects-table.csv",
      "last_date,first_date,name,company,role,channel,last_from,what_they_said,where_it_went,what_we_owe,urgency,"
      "action,recommended_next,sponsorship,authenticity,kind,email,source,new\n"
      "2026-09-28 12:00,2026-09-28 12:00,Sam Doe,Delta Works,,email,them,booked a call,"
      "booked 2026-09-28; call on 2026-10-02 15:30 PT,research,0 days,research,research: prep for the call,,x,person,"
      ",gmail:1,NEW\n"
      "2026-01-01 12:00,2026-01-01 12:00,,Oldco,Engineer,email,them,x,applied 2026-01-01,,,skip,skip: off target,,x,"
      "application,,gmail:2,\n")
    return root


def run(js: Path, *extra: str) -> int:
    return bg.main(["--root", str(js), "--today", TODAY.isoformat(), *extra])


def test_counts_statuses_and_the_person_linked_to_two_companies(js):
    g = bg.load(js, TODAY.isoformat())
    status = {s: bg.company_status(g, c) for s, c in g.companies.items()}
    assert status["acme"] == "applied" and status["betaco"] == "target" and status["watchy"] == "watching"
    assert status["emptyco"] == "queued" and status["delta-works"] == "lead" and status["oldco"] == "contact"
    assert bg.company_roles(g, g.companies["emptyco"]) == []
    # Pat: works at Acme (prospect), introduced Rae (Betaco) in the pursuit and in Rae's record
    pat = {(e.relation, e.dst_kind, e.dst) for e in g.edges if e.src == "pat-lee"}
    assert ("works at", "company", "acme") in pat and ("introduced", "person", "rae-kim") in pat
    assert any(e.src == "betaco-via-rae-kim-202609" and e.relation == "introducer" and e.dst == "pat-lee"
               for e in g.edges)
    # the LinkedIn snapshot's "Betaco Technologies" joins betaco, and the merge is listed
    assert g.people["lin-wu"].company == "betaco" and ("betaco-technologies", "betaco") in \
        {(a, b) for a, b, _ in g.merges}
    assert g.unreadable == ["prospects/broken.md"] and g.flat_read == ["hub/intake/2026-09-28-betaco.md"]


def test_role_status_and_people_fields(js):
    g = bg.load(js, TODAY.isoformat())
    roles = {r.id: r for r in g.roles}
    assert roles["acme-man-1"].status == "applied" and roles["acme-man-1"].application == "acme-ml-202609"
    assert roles["acme-man-2"].status == "lead" and roles["acme-man-2"].fit == "unknown"
    assert "pat-lee" in roles["acme-man-1"].people
    rae = g.people["rae-kim"]
    assert rae.last_in == "2026-09-27" and rae.wrote_last == "them"          # touch after the last outbound
    sam = g.people["sam-doe"]
    assert sam.record is None and sam.warmth is None and sam.company == "delta-works"


def test_writes_tables_hubs_and_notes_with_resolving_links(js):
    assert run(js) == 0
    out = js / "graph"
    for name in ("COMPANIES", "ROLES", "PEOPLE", "CONNECTIONS", "PRIORITY"):
        text = (out / f"{name}.md").read_text(encoding="utf-8")
        assert text.startswith(bg.HEADER_MD) and EM not in text
    hub = (out / "companies" / "betaco-company.md").read_text(encoding="utf-8")
    assert hub.splitlines()[1] == bg.HEADER_YAML
    meta = yaml.safe_load(hub.split("---\n")[1])
    assert list(meta) == bg.COMPANY_FIELDS and meta["status"] == "target"
    assert "Pat Lee" in hub and "introduced Rae Kim" in hub                   # paths in, from Acme
    assert "SECRET MESSAGE TEXT" not in hub                                   # never message text
    empty = yaml.safe_load((out / "companies" / "emptyco-company.md").read_text().split("---\n")[1])
    assert empty["roles"] == 0 and empty["priority"] == "unknown" and empty["next_action_at"] == "unknown"
    person = yaml.safe_load((out / "people" / "sam-doe-person.md").read_text().split("---\n")[1])
    assert list(person) == bg.PERSON_FIELDS and person["warmth"] == "unknown" and person["record"] == "none"
    files = {p.relative_to(js).as_posix(): p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    assert bg.link_problems(js.parents[3], js, files) == []
    assert bg.collisions(js.parents[3], js, files) == []
    # idempotent: the check passes and a second write changes nothing
    assert run(js, "--check") == 0
    assert bg.plan(js, bg.render(bg.load(js, TODAY.isoformat()), TODAY)) == {"write": [], "remove": []}


def test_priority_has_the_booked_call_and_the_reply_owed(js):
    run(js)
    text = (js / "graph" / "PRIORITY.md").read_text(encoding="utf-8")
    now = text.split("## Now")[1].split("## Backlog")[0]
    assert "2026-10-02" in now and "Sam Doe" in now                          # the call INBOX named
    assert "Rae Kim" in now and "reply owed" in now                           # she wrote last
    assert "waiting on Rae Kim" in now and "2026-10-04" in now                # the pursuit
    assert "Oldco" not in text                                                # skip rows are counted, not listed
    assert "INBOX rows marked skip, not listed: 1" in text


def test_stale_generated_notes_are_removed_and_other_files_kept(js):
    run(js)
    stale = js / "graph" / "companies" / "gone-company.md"
    stale.write_text(f"---\n{bg.HEADER_YAML}\ntype: company-hub\n---\n", encoding="utf-8")
    mine = js / "graph" / "notes.md"
    mine.write_text("hand notes\n", encoding="utf-8")
    assert run(js, "--check") == 1
    run(js)
    assert not stale.exists() and mine.exists()


def test_a_broken_link_fails_the_check(js):
    run(js)
    files = {"graph/X.md": "[[domains/career/projects/job-search/nowhere|x]] and [y](../nope.md)"}
    assert len(bg.link_problems(js.parents[3], js, files)) == 2


def test_basename_collision_is_reported(js):
    w(js.parents[3] / "brain" / "acme-company.md", "# a brain page with the same basename\n")
    files = bg.render(bg.load(js, TODAY.isoformat()), TODAY)
    assert bg.collisions(js.parents[3], js, files) == ["graph/companies/acme-company.md vs brain/acme-company.md"]


# ----------------------------------------------------------------------------- refresh (REQ-2: the dashboard's call)
def test_refresh_writes_the_graph_and_skips_the_whole_vault_collision_walk(js, monkeypatch):
    monkeypatch.setattr(bg, "collisions", lambda *a, **k: pytest.fail("refresh must not walk the vault"))
    report = bg.refresh(js, TODAY, js.parents[3])
    assert (js / "graph" / "COMPANIES.md").is_file() and bg.MARK in (js / "graph" / "COMPANIES.md").read_text()
    assert report["write"] and report["remove"] == [] and report["refused"] == []
    assert report["links"] == [] and report["em_dashes"] == [] and report["counts"]["companies"] >= 3
    assert bg.refresh(js, TODAY, js.parents[3])["write"] == []           # a second refresh has nothing to write
    monkeypatch.undo()
    assert run(js, "--check") == 0                                        # the refresh leaves --check clean


def test_refresh_refuses_a_hand_file_in_its_way_and_writes_nothing(js):
    hand = w(js / "graph" / "COMPANIES.md", "hand notes, no generated header\n")
    report = bg.refresh(js, TODAY, js.parents[3])
    assert report["refused"] == ["graph/COMPANIES.md"]
    assert hand.read_text() == "hand notes, no generated header\n"
    assert not (js / "graph" / "ROLES.md").exists()                       # all or nothing: no partial graph


def test_fit_md_score_is_read_as_well_as_fit_score(js):
    # H2 CS90 row: 126 of 147 fit.md files write `score`, not `fit_score`; the graph read only `fit_score`
    app = js / "applications" / "acme-scored-202610"
    w(app / "application.md", "---\nslug: acme-scored-202610\ncompany: Acme\ncompany_slug: acme\nstage: applied\n---\n")
    w(app / "fit.md", "---\nscore: 4.2\nfit_verdict: apply\n---\nx\n")
    g = bg.load(js)
    assert str(g.applications["acme-scored-202610"]["_fit"]) == "4.2"


def test_the_owner_is_shown_by_the_configured_label_and_aliases():
    p = bg.Person(id="x", name="X", last_in="2026-09-01", last_out="2026-09-02")
    assert p.wrote_last == "Alex"
    assert bg.Person(id="y", name="Y", last_from_csv="alex").wrote_last == "Alex"
    assert bg.Person(id="z", name="Z", last_from_csv="Zed").wrote_last == "Zed"
