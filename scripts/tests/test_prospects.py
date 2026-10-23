"""prospects.py — the CRM: ingest + merge, inference, scoring, batches, follow-ups, list/set/stats."""
from __future__ import annotations

import json
import sys
import time
from datetime import date

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import linkedin_export as le
import mail_classify as mc
import prospects as pr

TODAY = date(2026, 9, 24)
OWNER = ch.DEFAULT_TO.split("<")[1].rstrip(">")     # the mailbox address the shared fixtures (fixtures/comms) were written for


def _roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _classified(roots, items, name="gmail-2026-09-24.jsonl"):
    src = roots.harvest / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in ch.records(items)))
    return mc.classify_file(src, decider=mc.ReviewBackend(), account_emails={OWNER})


def _linkedin(roots):
    """The parsed export, classified the way the pipeline does it (mail_classify.py classify) before ingest."""
    out = roots.harvest / "linkedin-2026-09-24.jsonl"
    le.write_jsonl(out, le.build(le.open_export(ch.FIXTURES / "linkedin_export")))
    return mc.classify_file(out, decider=mc.ReviewBackend(), account_emails={OWNER})


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    roots = _roots(tmp_path_factory.mktemp("world"))
    gmail = _classified(roots, ch.load_labeled() + ch.load_outbound())
    report = pr.ingest(roots, gmail_files=[gmail], linkedin_files=[_linkedin(roots)], today=TODAY,
                       account_emails={OWNER})
    return roots, report


def _load(roots, slug):
    return jc.read_record(roots.prospects / f"{slug}.md")


def _event(key, name, *, at="2026-09-01T17:00:00Z", direction="in", email=None, url=None, company=None,
           title=None, label="recruiter_outreach", text="Hi Alex, we're hiring an ML Engineer. Open to a chat?",
           channel="email", thread=None):
    return {"key": key, "at": at, "direction": direction, "channel": channel, "source": "gmail-backlog",
            "label": label, "confidence": 0.9, "create": direction == "in",
            "person": {"name": name, "emails": [email] if email else [], "linkedin_url": url or "",
                       "company": company, "title": title},
            "subject": "Role", "summary": text[:100], "text": text, "thread_id": thread or key,
            "message_id": f"<{key}@x>", "references": "", "reply_to": "", "tags": [], "fingerprint": None}


# --------------------------------------------------------------------------- ingest
def test_ingest_creates_prospects_only_from_outreach_and_networking(world):
    roots, report = world
    slugs = {p.stem for p in roots.prospects.glob("*.md")}
    assert {"maya-chen", "ava-sinclair", "priya-nair", "hannah-lee", "kevin-zhao", "rohan-gupta"} <= slugs
    assert {"marta-lopez", "sanjay-rao", "victor-hale", "olivia-grant", "hiro-sato"} <= slugs      # LinkedIn only
    for noise in ("helio-systems", "google-calendar", "linkedin-job-alerts", "carlos-ruiz", "nadia-brooks",
                  "omar-siddiqui", "priyanka-das", "the-gradient-descent-weekly"):
        assert noise not in slugs, noise
    assert report["created"] == len(slugs)


def test_every_prospect_satisfies_the_os_schema(world):
    roots, _ = world
    sys.path.insert(0, str(__import__("agency_paths").KERNEL_DIR))
    import lint
    schema = jc.fm.parse_yaml((__import__("agency_paths").SCHEMAS_DIR / "prospect.yaml").read_text())
    # Upstream bug (reported): the schema lists `sponsorship_hint: [yes, no, ...]` unquoted, which
    # YAML 1.1 reads as booleans, so lint would reject the correct strings 'yes'/'no'. Restore the intent.
    schema["enums"]["sponsorship_hint"] = [{True: "yes", False: "no"}.get(v, v) for v in schema["enums"]["sponsorship_hint"]]
    for path in roots.prospects.glob("*.md"):
        meta, _ = jc.read_record(path)
        assert lint.validate(meta, schema, path.name, "prospect") == [], path.name
        assert meta["max_follow_ups"] == 2 and isinstance(meta["tags"], list)


def test_records_have_no_yaml_anchors_so_obsidian_can_read_them(world):
    roots, _ = world
    for path in roots.prospects.glob("*.md"):
        head = path.read_text().split("\n---\n", 1)[0]
        assert "&id" not in head and "*id" not in head, path.name


def test_ingest_is_idempotent(world, tmp_path):
    roots, _ = world
    before = {p.name: p.read_text() for p in roots.prospects.glob("*.md")}
    touches_before = sum(1 for _ in jc.iter_touches(roots.js))
    again = pr.ingest(roots, gmail_files=[roots.harvest / "gmail-2026-09-24.classified.jsonl"],
                      linkedin_files=[roots.harvest / "linkedin-2026-09-24.classified.jsonl"], today=TODAY,
                      account_emails={OWNER})
    assert again["created"] == 0 and again["new_events"] == 0
    assert {p.name: p.read_text() for p in roots.prospects.glob("*.md")} == before
    assert sum(1 for _ in jc.iter_touches(roots.js)) == touches_before


def test_one_person_across_email_threads_is_one_record(world):
    roots, _ = world
    meta, body = _load(roots, "maya-chen")
    assert meta["emails"] == ["maya.chen@heliosystems.test"]
    assert meta["inbound_count"] == 3 and meta["outbound_count"] == 1
    # invite + reschedule after the owner's reply, but the fixture has no application: the interview gate sends both
    # to review and moves no stage
    assert meta["status"] == "in_conversation" and "needs-review" in meta["tags"]
    assert body.count("[k:gm-") == 4


def test_linkedin_notification_email_and_export_merge_by_profile_url_without_double_counting(world):
    roots, _ = world
    meta, body = _load(roots, "leila-haddad")
    assert meta["linkedin_url"] == "https://www.linkedin.com/in/leila-haddad-7a1b2c"
    assert meta["emails"] == ["leila@brightloop.example.test"]            # from Connections
    assert set(meta["sources"]) == {"gmail-backlog", "linkedin-export"}
    assert (meta["inbound_count"], meta["outbound_count"]) == (2, 1)
    assert meta["status"] == "in_conversation" and meta["role"] == "founder"
    assert not (roots.prospects / "leila-haddad-2.md").exists()


def test_email_and_linkedin_merge_by_name_and_company(world):
    roots, _ = world
    meta, _ = _load(roots, "rebecca-stone")
    assert meta["emails"] == ["rebecca@cortexlabs.test"]
    assert meta["linkedin_url"] == "https://www.linkedin.com/in/rebecca-stone-talent"
    assert cu.companies_match(meta["company"], "Cortex Labs") and meta["channels"] == ["email", "linkedin"]
    assert len([p for p in roots.prospects.glob("rebecca-stone*.md")]) == 1


def test_owners_linkedin_invitation_attaches_to_the_email_prospect(world):
    roots, _ = world
    meta, body = _load(roots, "ben-carter")
    assert meta["outbound_count"] == 1 and meta["linkedin_url"] == "https://www.linkedin.com/in/ben-carter-perception"
    assert meta["company"] == "Mosaic Robotics" and meta["role"] == "hiring-manager"


def test_referral_intro_creates_the_introduced_person_and_the_referrer(world):
    roots, _ = world
    hannah, _ = _load(roots, "hannah-lee")
    assert hannah["label"] == "referral_intro" and "introduced" in hannah["tags"]
    assert hannah["role"] == "hiring-manager" and hannah["company"] == "Parallax AI"
    assert hannah["outbound_count"] == 1 and hannah["inbound_count"] == 1
    kevin, _ = _load(roots, "kevin-zhao")
    assert "referrer" in kevin["tags"] and kevin["company"] is None


def test_outbound_never_creates_and_personal_chat_is_skipped(world):
    roots, report = world
    assert not (roots.prospects / "priyanka-das.md").exists()
    assert not (roots.prospects / "omar-siddiqui.md").exists()
    assert report["outbound_unmatched"] >= 1 and report["skipped"] >= 1


def test_last_inbound_is_a_short_wrapped_summary_with_a_pointer_to_the_local_harvest(world):
    roots, _ = world
    _, body = _load(roots, "ava-sinclair")
    section = cu.section_text(body, "Last inbound (untrusted)")
    assert "state/harvest/gmail-2026-09-24.classified.jsonl" in section and "local only" in section
    wrapped = section[section.index("<untrusted_email>"):]
    assert wrapped.strip().endswith("</untrusted_email>") and "following up on my note" in wrapped
    assert len(wrapped) < 220
    timeline = cu.section_text(body, "Timeline")
    assert "[[" not in timeline and timeline.count("\n- ") + timeline.startswith("- ") >= 2


def test_prospect_files_keep_summaries_not_message_bodies(world):
    """Full bodies stay in the local, git-ignored state/harvest/; records hold <= 160-char summaries."""
    roots, _ = world
    assert "(2 days a week)" not in (roots.prospects / "maya-chen.md").read_text()
    assert "Open to chat?" not in (roots.prospects / "leila-haddad.md").read_text()


def test_touches_are_logged_once_with_the_prospect_slug(world):
    roots, _ = world
    rows = [t for t in jc.iter_touches(roots.js) if t["prospect"] == "maya-chen"]
    assert len(rows) == 4 and len({t["key"] for t in rows}) == 4
    assert {t["direction"] for t in rows} == {"in", "out"}
    assert all(t["status"] in ("delivered", "sent", "replied") for t in rows)


# --------------------------------------------------------------------------- conservative merging
def test_same_name_different_company_stays_separate_and_is_flagged(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Alex Kim", email="alex@acme.test", company="Acme"),
                             _event("b", "Alex Kim", email="alex@beta.test", company="Beta", at="2026-09-02T17:00:00Z")],
                     today=TODAY)
    files = sorted(p.stem for p in roots.prospects.glob("alex-kim*.md"))
    assert len(files) == 2
    for slug in files:
        meta, _ = _load(roots, slug)
        assert "possible-duplicate" in meta["tags"] and meta["possible_duplicates"] == [s for s in files if s != slug]


def test_same_name_without_any_corroboration_is_not_merged(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Sam Lee", email="sam@acme.test", company="Acme"),
                             _event("b", "Sam Lee", url="https://www.linkedin.com/in/sam-lee-9", channel="linkedin-message",
                                    at="2026-09-03T17:00:00Z")], today=TODAY)
    assert len(list(roots.prospects.glob("sam-lee*.md"))) == 2


def test_conflicting_linkedin_urls_block_a_name_and_company_merge(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Jo Park", url="https://www.linkedin.com/in/jo-park-1", company="Acme"),
                             _event("b", "Jo Park", url="https://www.linkedin.com/in/jo-park-2", company="Acme",
                                    at="2026-09-03T17:00:00Z")], today=TODAY)
    assert len(list(roots.prospects.glob("jo-park*.md"))) == 2


def test_merge_by_email_and_by_name_plus_company(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [
        _event("a", "Dana Fox", email="dana@acme.test", company="Acme AI"),
        _event("b", "Dana Fox", email="dana@acme.test", at="2026-09-02T17:00:00Z"),
        _event("c", "Dana Fox", url="https://www.linkedin.com/in/dana-fox", company="Acme",
               channel="linkedin-message", at="2026-09-03T17:00:00Z")], today=TODAY)
    files = list(roots.prospects.glob("dana-fox*.md"))
    assert len(files) == 1
    meta, _ = jc.read_record(files[0])
    assert meta["inbound_count"] == 3 and meta["linkedin_url"] == "https://www.linkedin.com/in/dana-fox"


# --------------------------------------------------------------------------- inference
@pytest.mark.parametrize("company,domain,text,role,expected", [
    ("Example AI Labs", "", "", "recruiter", "ai-lab"),
    ("Acme Cloud", "", "", "recruiter", "bigtech"),
    ("ApexMatch Talent Partners", "apexmatch-talent.com", "My client is hiring", "recruiter", "recruiting-firm"),
    (None, "gmail.com", "I'm an independent recruiter working with a stealth AI startup", "recruiter", "recruiting-firm"),
    ("Tessellate", "tessellate.test", "we just raised a $4M seed round", "founder", "startup"),
    ("Orbit Cloud", "orbitcloud.com", "our Series D growth", "recruiter", "scaleup"),
    ("ESU", "esu.test.edu", "", "other", "university"),
    ("Helio Systems", "heliosystems.test", "We're hiring", "recruiter", "unknown"),
])
def test_company_type_rules(company, domain, text, role, expected):
    assert pr.infer_company_type(company, domain, text, role) == expected


def test_fixture_people_get_roles_companies_and_hints(world):
    roots, _ = world
    maya, _ = _load(roots, "maya-chen")
    assert (maya["role"], maya["company"], maya["title"]) == ("recruiter", "Helio Systems", "Technical Recruiter")
    assert maya["role_hint"] == "Senior ML Engineer" and maya["remote_hint"] == "hybrid"
    assert maya["comp_hint"] == "$190k - $230k" and maya["sponsorship_hint"] == "yes"
    chris, _ = _load(roots, "chris-tan")
    assert chris["sponsorship_hint"] == "no" and "unable to sponsor" in chris["sponsorship_evidence"]
    jordan, _ = _load(roots, "jordan-blake")
    assert jordan["company_type"] == "recruiting-firm" and "contract" in jordan["tags"]
    derek, _ = _load(roots, "derek-osei")
    assert derek["role"] == "sourcer" and derek["company"] == "Northwind Robotics"


@pytest.mark.parametrize("text,expected", [
    ("We sponsor H-1B transfers and support STEM OPT.", "yes"),
    ("We do sponsor visas.", "yes"),
    ("Visa sponsorship is available for this role.", "yes"),
    ("We are unable to sponsor visas for this position.", "no"),
    ("Sponsorship is not available.", "no"),
    ("Candidates must be a US citizen due to clearance requirements.", "no"),
    ("USC/GC only, no C2C.", "no"),
    ("This role does not offer visa sponsorship.", "no"),
    ("Great team, remote friendly.", "unknown"),
])
def test_sponsorship_hint_from_text(text, expected):
    assert pr.sponsorship_hint(text)[0] == expected


# --------------------------------------------------------------------------- scoring
def _meta(**kw):
    base = {"id": "x", "name": "X", "role": "recruiter", "company_type": "unknown", "status": "new",
            "last_inbound_at": "2026-09-20T17:00:00Z", "personalization": 0.0, "role_hint": None,
            "sponsorship_hint": "unknown", "label": "recruiter_outreach", "tags": [], "title": None,
            "remote_hint": None, "location_hint": None}
    base.update(kw)
    return base


def _warmth(**kw):
    return pr.score_prospect(_meta(**kw), TODAY)["warmth"]


def test_warmth_is_monotonic_in_each_signal():
    recency = [_warmth(last_inbound_at=d) for d in
               ("2026-02-01T00:00:00Z", "2026-05-01T00:00:00Z", "2026-08-01T00:00:00Z", "2026-09-20T00:00:00Z")]
    assert recency == sorted(recency) and recency[-1] > recency[0]
    personal = [_warmth(personalization=p) for p in (0.0, 1.0, 2.0)]
    assert personal == sorted(personal)
    seniority = [_warmth(role=r, company_type=ct) for r, ct in
                 (("recruiter", "recruiting-firm"), ("sourcer", "unknown"), ("recruiter", "unknown"), ("founder", "startup"))]
    assert seniority == sorted(seniority) and seniority[-1] > seniority[0]
    assert _warmth(role_hint="ML Engineer") >= _warmth(role_hint=None)
    assert all(0 <= w <= 3 for w in recency + personal + seniority)


def test_fit_reads_the_role_hint_and_sponsorship_is_weighed_not_a_gate():
    """A plain no-sponsorship line weighs the fit down by the configured weight (0.5 here); citizenship gates."""
    ml = pr.score_prospect(_meta(role_hint="Senior ML Engineer"), TODAY)["fit_score"]
    sales = pr.score_prospect(_meta(role_hint="Account Executive"), TODAY)["fit_score"]
    weighed = pr.score_prospect(_meta(role_hint="Senior ML Engineer", sponsorship_hint="no"), TODAY)["fit_score"]
    blocked = pr.score_prospect(_meta(role_hint="Senior ML Engineer", sponsorship_hint="no",
                                      sponsorship_evidence="US citizens only."), TODAY)["fit_score"]
    assert ml >= 4.0 and sales <= 2.0 and weighed == ml - 0.5 and blocked == 1.0
    assert 1.0 <= ml <= 5.0


@pytest.mark.parametrize("kw,expected", [
    (dict(role="founder", company_type="startup", role_hint="Founding ML Engineer", personalization=2.0), "P0"),
    (dict(label="referral_intro", role="other", role_hint="Applied ML Engineer"), "P0"),
    (dict(role="recruiter", company_type="unknown", role_hint="Senior ML Engineer"), "P1"),
    (dict(role="recruiter", company_type="recruiting-firm", role_hint="Backend Engineer"), "P3"),
    (dict(role="founder", role_hint="ML Engineer", last_inbound_at="2026-02-10T00:00:00Z"), "P1"),
    (dict(role="recruiter", role_hint="ML Engineer", last_inbound_at="2026-02-10T00:00:00Z"), "P3"),
    (dict(role="founder", role_hint="ML Engineer", sponsorship_hint="no",
          sponsorship_evidence="Applicants must be a U.S. citizen."), "P3"),               # citizenship gates
    (dict(role="recruiter", role_hint="Account Executive"), "P3"),
])
def test_priority_rules(kw, expected):
    assert pr.score_prospect(_meta(**kw), TODAY)["priority"] == expected


def test_score_marks_new_as_triaged_and_keeps_a_locked_priority(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Rae Lin", email="rae@acme.test", company="Acme")], today=TODAY)
    pr.set_fields(roots, "rae-lin", priority="P0", today=TODAY)
    pr.score_all(roots, today=TODAY, apply_baseline=True)          # the old default, now opt-in
    meta, _ = _load(roots, "rae-lin")
    assert meta["status"] == "triaged" and meta["priority"] == "P0" and meta["priority_locked"] is True


# --------------------------------------------------------------------------- batches, follow-ups
def _seed(roots, rows):
    for row in rows:
        meta = pr.new_meta(row["id"], row["id"].replace("-", " ").title(), source="gmail-backlog", today=TODAY)
        meta.update(row)
        jc.write_record(roots.prospects / f"{row['id']}.md", meta, "# x\n\n## Timeline\n")


def test_select_orders_by_priority_warmth_fit_and_skips_unsafe(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [
        {"id": "p1-cold", "priority": "P1", "warmth": 1, "fit_score": 4.0},
        {"id": "p0-warm", "priority": "P0", "warmth": 3, "fit_score": 4.5},
        {"id": "p1-warm", "priority": "P1", "warmth": 3, "fit_score": 3.5},
        {"id": "p0-dnc", "priority": "P0", "warmth": 3, "status": "do_not_contact"},
        {"id": "p0-dup", "priority": "P0", "warmth": 3, "tags": ["possible-duplicate"]},
        {"id": "p0-replied", "priority": "P0", "warmth": 3, "status": "replied"},
        {"id": "p2", "priority": "P2", "warmth": 3},
    ])
    picked = pr.select(roots, batch_size=3, priorities=("P0", "P1"))
    assert [p["id"] for p in picked] == ["p0-warm", "p1-warm", "p1-cold"]
    assert [p["id"] for p in pr.select(roots, batch_size=1, priorities=("P0", "P1"))] == ["p0-warm"]


@pytest.mark.parametrize("sent,not_yet,due_on", [
    ("2026-09-25T17:00:00Z", date(2026, 9, 30), date(2026, 10, 1)),     # Friday -> next Thursday (4 business days)
    ("2026-09-26T18:00:00Z", date(2026, 9, 30), date(2026, 10, 1)),     # Saturday -> Thursday
    ("2026-09-26T00:30:00Z", date(2026, 9, 30), date(2026, 10, 1)),     # Fri 17:30 Pacific (Sat UTC)
    ("2026-09-23T16:00:00Z", date(2026, 9, 28), date(2026, 9, 29)),     # Wednesday -> next Tuesday
])
def test_follow_ups_are_due_four_business_days_later_across_weekends(tmp_path, sent, not_yet, due_on):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "rec", "status": "replied", "last_outbound_at": sent, "last_inbound_at": "2026-09-20T00:00:00Z",
                   "outbound_count": 1, "follow_ups_sent": 0}])
    assert pr.due(roots, today=not_yet)["due"] == []
    due = pr.due(roots, today=due_on)["due"]
    assert [d["id"] for d in due] == ["rec"] and due[0]["due_date"] == due_on.isoformat() and due[0]["nudge"] == 1


def test_no_more_than_three_nudges_and_replies_stop_the_clock(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [
        {"id": "maxed", "status": "replied", "last_outbound_at": "2026-09-01T17:00:00Z", "follow_ups_sent": 3,
         "outbound_count": 4},
        {"id": "answered", "status": "in_conversation", "last_outbound_at": "2026-09-01T17:00:00Z",
         "last_inbound_at": "2026-09-02T17:00:00Z", "outbound_count": 1},
    ])
    result = pr.due(roots, today=TODAY)
    assert result["due"] == [] and [e["id"] for e in result["exhausted"]] == ["maxed"]


# --------------------------------------------------------------------------- list / set / stats / CLI
def test_list_filters(world):
    roots, _ = world
    rows = pr.list_prospects(roots, {"role": ["founder"]})
    assert rows and all(r["role"] == "founder" for r in rows)
    assert all("linkedin-export" in (r.get("sources") or []) for r in pr.list_prospects(roots, {"source": ["linkedin-export"]}))
    assert {r["id"] for r in pr.list_prospects(roots, {"sponsorship": ["no"]})} == {"chris-tan"}
    assert all(r["last_inbound_at"] >= "2026-09-01" for r in pr.list_prospects(roots, {"since": date(2026, 9, 1)}))
    assert {r["id"] for r in pr.list_prospects(roots, {"tag": ["referrer"]})} >= {"kevin-zhao"}
    assert all(r["company_type"] == "recruiting-firm" for r in pr.list_prospects(roots, {"company_type": ["recruiting-firm"]}))
    assert all(r["status"] == "interviewing" for r in pr.list_prospects(roots, {"status": ["interviewing"]}))
    assert all(r["priority"] in ("P0", "P1") for r in pr.list_prospects(roots, {"priority": ["P0", "P1"]}))


def test_cli_list_json_and_table(world, capsys):
    roots, _ = world
    assert pr.main(["list", "--root", str(roots.js), "--role", "founder", "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and {r["role"] for r in rows} == {"founder"}
    assert pr.main(["list", "--root", str(roots.js), "--role", "founder", "--format", "table"]) == 0
    table = capsys.readouterr().out
    assert table.splitlines()[0].split()[:3] == ["id", "name", "company"] and "ava-sinclair" in table


def test_set_changes_status_tags_and_notes_append_only(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Kim Oh", email="kim@acme.test", company="Acme")], today=TODAY)
    _, before = _load(roots, "kim-oh")
    pr.set_fields(roots, "kim-oh", status="dormant", add_tags=["agency"], note="Asked to reconnect in Q1",
                  today=TODAY)
    assert "agency" in _load(roots, "kim-oh")[0]["tags"]
    pr.set_fields(roots, "kim-oh", remove_tags=["agency"], dnc="asked not to be contacted", today=TODAY)
    meta, body = _load(roots, "kim-oh")
    assert meta["status"] == "do_not_contact" and meta["do_not_contact_reason"] == "asked not to be contacted"
    assert "agency" not in meta["tags"]
    assert cu.section_text(before, "Timeline").strip() in cu.section_text(body, "Timeline")
    assert "status new → dormant" in body and "Asked to reconnect in Q1" in cu.section_text(body, "Notes")
    with pytest.raises(ValueError):
        pr.set_fields(roots, "kim-oh", status="ghosting", today=TODAY)


def test_stats_backlog_funnel(world):
    roots, _ = world
    s = pr.stats(roots, today=TODAY)
    assert s["total"] == len(list(roots.prospects.glob("*.md")))
    assert s["funnel"]["replied"] >= 3 and s["funnel"]["responded"] >= 2
    assert s["funnel"]["interviewing"] == 0                      # no application in the fixture: interview mails go to review
    assert sum(s["by_status"].values()) == s["total"] and set(s["by_priority"]) <= {"P0", "P1", "P2", "P3"}


def test_list_and_stats_stay_fast_at_1200_records(tmp_path):
    roots = _roots(tmp_path)
    rows = [{"id": f"p{i:04d}", "priority": "P%d" % (i % 4), "warmth": i % 4, "fit_score": 3.0,
             "role": ("recruiter", "founder", "hiring-manager")[i % 3], "tags": ["backlog"],
             "last_inbound_at": f"2026-0{1 + i % 9}-15T10:00:00Z"} for i in range(1200)]
    _seed(roots, rows)
    start = time.perf_counter()
    listed = pr.list_prospects(roots, {"role": ["founder"], "priority": ["P0", "P1"]})
    summary = pr.stats(roots, today=TODAY)
    elapsed = time.perf_counter() - start
    assert summary["total"] == 1200 and listed
    assert elapsed < 3.0, elapsed


def test_company_names_are_clean_and_consistent_across_people(world):
    roots, _ = world
    company = lambda slug: _load(roots, slug)[0]["company"]  # noqa: E731
    assert company("lucy-park") == "Helio Systems"          # domain spelling adopts the known display name
    assert company("raj-iyer") == "Cortex Labs"
    assert company("hiro-sato") == "Kumo Robotics"
    assert company("olivia-grant") == "Meridian Health AI"
    assert company("arjun-mehta") == "Orbit Cloud"
    assert company("jordan-blake") == "ApexMatch Talent Partners"
    assert company("rohan-gupta") is None                    # "a fintech" is not a company
    assert company("luis-ortega") is None                    # luis@ortega.test is a personal domain


@pytest.mark.parametrize("text,expected", [
    ("Would love to talk about you joining as a founding engineer.", "founding engineer"),
    ("We're hiring a Founding ML Engineer to build our eval stack.", "Founding ML Engineer"),
    ("an opening for a Staff ML Engineer on our team", "Staff ML Engineer"),
    ("a Software Engineer, GenAI role (L5)", "Software Engineer"),
])
def test_role_hint_extraction(text, expected):
    assert pr.hints(text)["role_hint"] == expected


# Obsidian Bases views: guarded in test_views.py (all four views and the vault bookmarks).


# --------------------------------------------------------------------------- set key=value (role-card contract)
@pytest.fixture
def one(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Rae Lin", email="rae@acme.test", company="Acme", title="Recruiter")], today=TODAY)
    return roots


def _cli(roots, *args):
    return pr.main(["--root", str(roots.js), "--today", "2026-09-24", *args])


def test_set_accepts_key_value_pairs_validated_against_the_schema(one, capsys):
    assert _cli(one, "set", "rae-lin", "warmth=2", "fit_score=4.5", "sponsorship_hint=yes", "priority=P1",
                "status=triaged", "next_touch=2026-10-01", "--timeline", "2026-09-24 triage: W2 F4.5 -> P1") == 0
    meta, body = _load(one, "rae-lin")
    assert (meta["warmth"], meta["fit_score"], meta["sponsorship_hint"], meta["priority"], meta["status"]) == (
        2, 4.5, "yes", "P1", "triaged")
    assert str(meta["next_touch_at"]) == "2026-10-01"
    assert set(meta["locked_fields"]) >= {"warmth", "fit_score", "sponsorship_hint", "priority"}
    assert "triage: W2 F4.5 -> P1" in cu.section_text(body, "Timeline")


@pytest.mark.parametrize("pair", ["warmth=4", "fit_score=6", "status=ghosting", "priority=P9", "sponsorship_hint=maybe",
                                  "next_touch=soon", "not_a_field=1", "id=other", "role=wizard"])
def test_set_rejects_invalid_values_and_unknown_or_protected_fields(one, pair, capsys):
    before = (one.prospects / "rae-lin.md").read_text()
    assert _cli(one, "set", "rae-lin", pair) == 2
    assert (one.prospects / "rae-lin.md").read_text() == before


def test_scoring_never_overwrites_fields_triage_set_by_hand(one):
    pr.set_fields(one, "rae-lin", fields={"warmth": 3, "fit_score": 2.0, "priority": "P0"}, today=TODAY)
    pr.score_all(one, today=TODAY)
    meta, _ = _load(one, "rae-lin")
    assert (meta["warmth"], meta["fit_score"], meta["priority"]) == (3, 2.0, "P0")


def test_do_not_contact_is_one_way_and_needs_a_reason(one, capsys):
    assert _cli(one, "set", "rae-lin", "status=do_not_contact") == 2              # no reason given
    assert _cli(one, "set", "rae-lin", "status=do_not_contact",
                "do_not_contact_reason=asked to stop on 2026-09-24") == 0
    for attempt in (["status=triaged"], ["--status", "new"], ["status=in_conversation"]):
        assert _cli(one, "set", "rae-lin", *attempt) == 2
    assert _load(one, "rae-lin")[0]["status"] == "do_not_contact"
    pr.ingest_events(one, [_event("b", "Rae Lin", email="rae@acme.test", at="2026-09-20T17:00:00Z", label="interview_invite")],
                     today=TODAY)
    assert _load(one, "rae-lin")[0]["status"] == "do_not_contact"                  # ingest never lifts it either


def test_add_tag_alias_and_list_fields(one, capsys):
    assert _cli(one, "set", "rae-lin", "--add-tag", "invitation-pending", "applications+=acme-ml-engineer") == 0
    assert _cli(one, "set", "rae-lin", "--remove-tag", "invitation-pending", "emails+=rae.lin@gmail.com") == 0
    meta, _ = _load(one, "rae-lin")
    assert "invitation-pending" not in meta["tags"] and meta["applications"] == ["acme-ml-engineer"]
    assert meta["emails"] == ["rae@acme.test", "rae.lin@gmail.com"]


# --------------------------------------------------------------------------- tags obey the vocabulary (J18)
@pytest.mark.parametrize("args", [
    ["--add-tag", "agency-later"],                              # an ad hoc tag
    ["--add-tag", "referrer", "--add-tag", "hot"],              # one bad tag refuses the whole change
    ["tags+=referrer,warm-lead"],
    ["tags=needs-review,Referrer"],                             # case matters: the vocabulary is lower-case
    ["--add-tag", "project/no-such-project"],                   # project/<slug> must name a real project
])
def test_set_refuses_a_tag_outside_the_vocabulary_and_writes_nothing(one, capsys, args):
    before = (one.prospects / "rae-lin.md").read_text(encoding="utf-8")
    assert _cli(one, "set", "rae-lin", "status=triaged", *args) == 2
    err = json.loads(capsys.readouterr().out)["error"]
    assert "not in schemas/tags.yaml" in err and "Record tags allowed:" in err
    assert "referrer" in err and "sponsor-likely" in err and "possible-duplicate" in err
    assert (one.prospects / "rae-lin.md").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("tag", ["referrer", "df-adjacent", "sponsor-likely", "domain/career", "job-search",
                                 "project/job-search"])
def test_set_accepts_record_topic_domain_and_project_tags(one, capsys, tag):
    assert _cli(one, "set", "rae-lin", "--add-tag", tag) == 0
    assert tag in _load(one, "rae-lin")[0]["tags"]


def test_set_removes_a_tag_the_vocabulary_no_longer_allows(one, capsys):
    meta, body = _load(one, "rae-lin")
    meta["tags"] = ["legacy-flag"]
    jc.write_record(one.prospects / "rae-lin.md", meta, body)
    assert _cli(one, "set", "rae-lin", "--remove-tag", "legacy-flag") == 0          # cleanup is always allowed
    assert "legacy-flag" not in _load(one, "rae-lin")[0]["tags"]


def test_every_tag_a_script_writes_is_in_the_vocabulary():
    """The record tags the scripts write themselves (string literals next to a tags write) are all allowed, so
    the vocabulary check on manual writes never blocks a script's own flag and lint never flags one."""
    import re
    literal = re.compile(r'"([a-z][a-z0-9-]*)"')
    write = re.compile(r'_add_unique\([^)]*tags|\["tags"\]\s*=|\btags\s*=\s*\[|\btags=\[|\+ \["[a-z-]+"\]'
                       r'|\(\["[a-z-]+"\] if ')
    found = set()
    for path in (jc.CODE_ROOT / "scripts").glob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "tags" in line and write.search(line) or (i and "tags" in lines[i - 1] and write.search(line)):
                code = re.sub(r'[\w)\]]\["[^"]*"\]', "", line.split("#")[0])   # subscripts: keys, not tags
                found |= set(literal.findall(code)) - {"tags"}
    assert {"jd-missing", "possible-duplicate", "suspected-scam", "merged", "sponsorship-badge"} <= found, found
    bad = jc.unknown_tags(sorted(found))
    assert not bad, bad


def test_merge_into_combines_two_records_and_routes_future_mail_to_the_canonical_one(tmp_path, capsys):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [
        _event("a", "Alex Kim", email="alex@acme.test", company="Acme", at="2026-09-01T17:00:00Z"),
        _event("b", "Alex Kim", url="https://www.linkedin.com/in/alex-kim-7", channel="linkedin-message",
               at="2026-09-02T17:00:00Z", text="Hi Alex, we're hiring at Acme. Open to a chat?")], today=TODAY)
    dup, canonical = "alex-kim-2", "alex-kim"
    assert _cli(roots, "set", dup, f"merge_into={canonical}") == 0
    meta, body = _load(roots, canonical)
    assert meta["linkedin_url"] == "https://www.linkedin.com/in/alex-kim-7" and meta["inbound_count"] == 2
    assert "possible-duplicate" not in meta["tags"] and meta["possible_duplicates"] == []
    assert body.count("[k:") == 2 and f"merged {dup}" in body
    dmeta, _ = _load(roots, dup)
    assert (dmeta["status"], dmeta["merged_into"]) == ("closed", canonical) and "merged" in dmeta["tags"]
    pr.ingest_events(roots, [_event("c", "Alex Kim", url="https://www.linkedin.com/in/alex-kim-7",
                                    channel="linkedin-message", at="2026-09-05T17:00:00Z")], today=TODAY)
    assert _load(roots, canonical)[0]["inbound_count"] == 3 and _load(roots, dup)[0]["inbound_count"] == 1


def test_list_possible_duplicates_and_company(tmp_path, capsys):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Alex Kim", email="alex@acme.test", company="Acme AI"),
                             _event("b", "Alex Kim", email="alex@beta.test", company="Beta", at="2026-09-02T17:00:00Z"),
                             _event("c", "Bo Li", email="bo@acme.test", company="Acme", at="2026-09-03T17:00:00Z")],
                     today=TODAY)
    assert _cli(roots, "list", "--possible-duplicates", "--format", "json") == 0
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == {"alex-kim", "alex-kim-beta"}
    assert _cli(roots, "list", "--company", "acme", "--format", "json") == 0
    assert {r["id"] for r in json.loads(capsys.readouterr().out)} == {"alex-kim", "bo-li"}


def test_ingest_takes_files_positionally_including_the_prospectors_outbound_rows(tmp_path, capsys):
    roots = _roots(tmp_path)
    rows = [{"name": "Nora Vale", "company": "Kite Bio", "role": "hiring-manager",
             "linkedin_url": "https://www.linkedin.com/in/nora-vale", "email": None, "source": "referral",
             "found_via": "companies/kite-bio.md", "relation": "alumni", "req_id": "KB-12"}]
    src = roots.harvest / "outbound-2026-09-24.jsonl"
    src.parent.mkdir(parents=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert _cli(roots, "ingest", str(src)) == 0
    meta, body = _load(roots, "nora-vale")
    assert (meta["source"], meta["role"], meta["status"], meta["company"]) == ("referral", "hiring-manager", "new", "Kite Bio")
    assert meta["inbound_count"] == 0 and meta["found_via"] == "companies/kite-bio.md" and meta["req_id"] == "KB-12"
    assert _cli(roots, "ingest", str(src)) == 0 and len(list(roots.prospects.glob("nora-vale*.md"))) == 1


def test_ingest_honours_labels_already_resolved_on_a_classified_linkedin_file(tmp_path):
    roots = _roots(tmp_path)
    src = _linkedin(roots)
    rows = [json.loads(x) for x in src.read_text().splitlines()]
    for r in rows:
        if r["type"] == "touch" and r["person_name"] == "Omar Siddiqui" and r["direction"] == "in":
            r["classification"] = {"label": "networking", "confidence": 0.9, "identity": {}}
    classified = roots.harvest / "linkedin-2026-09-24.classified.jsonl"
    classified.write_text("".join(json.dumps(r) + "\n" for r in rows))
    pr.ingest(roots, linkedin_files=[classified], today=TODAY)
    assert _load(roots, "omar-siddiqui")[0]["label"] == "networking"


# --------------------------------------------------------------------------- regressions
def _luis_row():
    body = ("Hi Alex,\n\nI lead the perception and language-instruction team at Harborline Robotics. I'm hiring an ML "
            "engineer to own our evaluation pipelines, and your LLM agent work stood out. Would you like to talk about "
            "the role on my team?\n\nLuis Ferreira\nEngineering Manager, Perception, Harborline Robotics")
    return {"id": "7d67d85d61e9efff", "thread_id": "7d67d85d61e9efff", "internal_date": "2026-08-25T18:40:00Z",
            "date": "Tue, 25 Aug 2026 11:40:00 -0700", "from": "Luis Ferreira <luis.ferreira@harborline.test>",
            "to": f"Alex Example <{OWNER}>", "cc": "", "reply_to": "", "subject": "ML engineer on my perception team?",
            "message_id": "<c1-luis-01@fixtures.test>", "references": "", "list_id": "", "list_unsubscribe": "",
            "snippet": body[:160], "body": body, "direction": "in"}


def test_choose_company_keeps_the_signature_name_when_the_domain_is_its_leading_word():
    assert pr.choose_company("Harborline Robotics", "Harborline") == "Harborline Robotics"
    assert pr.choose_company("Harbor Line Robotics", "Harborline") == "Harbor Line Robotics"
    assert pr.choose_company("Acme Robotics", "Zeta") == "Zeta"                 # a different employer: domain wins
    assert pr.choose_company("Harbor Robotics", "Harborline") == "Harborline"  # "harbor" is not "harborline"


def test_ingest_records_the_full_company_name_from_the_signature(tmp_path):
    roots = _roots(tmp_path)
    events = pr.gmail_events([_luis_row()], {OWNER})
    assert events[0]["person"]["company"] == "Harborline Robotics"
    pr.ingest_events(roots, events, today=TODAY)
    meta, _ = _load(roots, "luis-ferreira")
    assert (meta["company"], meta["company_slug"]) == ("Harborline Robotics", "harborline-robotics")


def test_a_truncated_company_on_file_is_upgraded_to_the_full_name(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a1", "Luis Ferreira", email="luis@harborline.test", company="Harborline")],
                     today=TODAY)
    pr.ingest_events(roots, [_event("a2", "Luis Ferreira", email="luis@harborline.test", company="Harborline Robotics",
                                    at="2026-09-02T17:00:00Z")], today=TODAY)
    assert _load(roots, "luis-ferreira")[0]["company"] == "Harborline Robotics"


WATCHLIST = """companies:
  - {name: Harborline Robotics, slug: harborline-robotics, careers_url: "https://harborline.test/careers"}
  - {name: Kite Bio, slug: kitebio-inc, careers_url: "https://job-boards.greenhouse.io/kitebio"}
  - {name: Orchard Loop, slug: orchard-loop, aliases: [Pomona Labs], careers_url: "https://orchardloop.test/jobs"}
  - {name: Globex, slug: globex, careers_url: "https://globex.test/careers"}
  - {name: Globex Labs, slug: globex-labs, careers_url: "https://globex.test/labs/careers"}
  - {name: Acme Robotics, slug: acme-robotics}
  - {name: Acme Robotics Labs West, slug: acme-west}
"""


@pytest.mark.parametrize("name,email,company,slug,source", [
    ("Luis Ferreira", "luis.ferreira@harborline.test", "Harborline", "harborline-robotics", "watchlist"),  # domain
    ("Nora Vale", "nora@kite.test", "Kite Bio", "kitebio-inc", "watchlist"),                           # name
    ("Ivy Chen", "ivy@pomona.test", "Pomona Labs", "orchard-loop", "watchlist"),                       # alias
    ("Gus Hale", "gus@globex.test", "Globex", "globex", "watchlist"),               # exact beats the longer-name prefix
    ("Pia Ruiz", "pia@unlisted.test", "Unlisted Co", "unlisted", "derived"),     # jobs_manual's slug
    ("Tom Ode", "tom@acme.test", "Acme", "acme", "derived"),               # two watchlist extensions: no guess
    ("Rae Quill", "rae.quill@gmail.com", "Harborline", "harborline-robotics", "watchlist"),  # extension, no domain
])
def test_company_slug_resolves_against_the_watchlist_before_slugify(tmp_path, name, email, company, slug, source):
    roots = _roots(tmp_path)
    (roots.js / "targets").mkdir()
    (roots.js / "targets" / "companies.yaml").write_text(WATCHLIST)
    pr.ingest_events(roots, [_event("w1", name, email=email, company=company)], today=TODAY)
    meta, _ = _load(roots, jc.slugify(name))
    assert (meta["company_slug"], meta["company_slug_source"]) == (slug, source)


def test_the_dossier_slug_beats_the_watchlist_and_the_derived_slug(tmp_path):
    # A record said fabrikam-motor-company; the dossier companies/fabrikam.md says fabrikam
    roots = _roots(tmp_path)
    (roots.js / "targets").mkdir()
    (roots.js / "targets" / "companies.yaml").write_text(WATCHLIST + "  - {name: Fabrikam Motor Company, slug: fabrikam-motor}\n")
    (roots.js / "companies").mkdir()
    (roots.js / "companies" / "fabrikam.md").write_text('---\ntype: company\nslug: fabrikam\nname: "Fabrikam Motor Company"\n---\n')
    pr.ingest_events(roots, [_event("w3", "Saul Ode", email="saul@fabrikam.test", company="Fabrikam Motor Company")],
                     today=TODAY)
    meta, _ = _load(roots, "saul-ode")
    assert (meta["company_slug"], meta["company_slug_source"]) == ("fabrikam", "dossier")


def test_company_slug_is_derived_without_a_watchlist(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("w2", "Nora Vale", email="nora@kite.test", company="Kite Bio")], today=TODAY)
    meta, _ = _load(roots, "nora-vale")
    assert (meta["company_slug"], meta["company_slug_source"]) == ("kite-bio", "derived")


# --------------------------------------------------------------------------- suspected scams
def _scam_row(rid, sender, at):
    text = "You have been hired without an interview. Please reply with your bank account and routing number."
    return {"id": rid, "thread_id": f"t-{rid}", "internal_date": at, "from": sender, "to": ch.DEFAULT_TO,
            "subject": "Congratulations on your new role", "message_id": f"<{rid}@x>", "body": text, "snippet": text,
            "direction": "in", "classification": {"label": "suspected_scam", "confidence": 0.9, "identity": {},
                                                  "signals": ["bank_or_payment", "offer_without_interview"]}}


def test_suspected_scam_mail_creates_no_prospect_and_moves_no_pipeline(tmp_path):
    roots = _roots(tmp_path)
    pr.ingest_events(roots, [_event("a", "Jordan Blake", email="jordan@veltrix.test", company="Veltrix",
                                    at="2026-09-01T17:00:00Z")], today=TODAY)
    before, _ = _load(roots, "jordan-blake")
    events = pr.gmail_events([_scam_row("s1", "Jordan Blake <jordan@veltrix.test>", "2026-09-05T17:00:00Z"),
                              _scam_row("s2", "Allen Moore <allen@orbis.test>", "2026-09-05T18:00:00Z")])
    assert [e["create"] for e in events] == [False, False]
    report = pr.ingest_events(roots, events, today=TODAY)
    assert not (roots.prospects / "allen-moore.md").exists() and report["skipped"] == 1
    meta, body = _load(roots, "jordan-blake")
    assert "suspected-scam" in meta["tags"] and "offer" not in meta["tags"]
    assert meta["status"] == before["status"] and meta["label"] == before["label"] == "recruiter_outreach"
    assert meta["reply_context"] == before["reply_context"]                   # nobody drafts a reply to it
    assert "suspected_scam" in body                                            # the timeline still shows it


def test_suspected_scam_is_a_valid_label_for_set_and_list(tmp_path, capsys):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "sc", "label": "suspected_scam"}])
    assert _cli(roots, "list", "--label", "suspected_scam", "--format", "json") == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["sc"]


# --------------------------------------------------------------------------- outbound channels
def _ingest_outbound(roots, rows, name="outbound-2026-09-25.jsonl"):
    src = roots.harvest / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert _cli(roots, "ingest", str(src)) == 0


@pytest.mark.parametrize("row,channels,emails", [
    ({"linkedin_url": "https://www.linkedin.com/in/nora-vale", "email": None}, ["linkedin"], []),
    ({"linkedin_url": "https://www.linkedin.com/in/nora-vale"}, ["linkedin"], []),
    ({"linkedin_url": "https://www.linkedin.com/in/nora-vale", "email": "Unknown - to confirm"}, ["linkedin"], []),
    ({"linkedin_url": "https://www.linkedin.com/in/nora-vale", "email": "unknown"}, ["linkedin"], []),
    ({"linkedin": "https://www.linkedin.com/in/nora-vale/"}, ["linkedin"], []),
    ({"linkedin_url": "https://www.linkedin.com/in/nora-vale", "email": "Nora@KiteBio.test"}, ["email", "linkedin"],
     ["nora@kitebio.test"]),
    ({"email": "nora@kitebio.test"}, ["email"], ["nora@kitebio.test"]),
    ({}, [], []),
])
def test_outbound_prospect_channels_follow_the_contact_points_it_has(tmp_path, capsys, row, channels, emails):
    roots = _roots(tmp_path)
    _ingest_outbound(roots, [{"name": "Nora Vale", "company": "Kite Bio", "source": "outbound", **row}])
    meta, _ = _load(roots, "nora-vale")
    assert meta["channels"] == channels and meta["emails"] == emails
    if "linkedin" in channels:
        assert meta["linkedin_url"] == "https://www.linkedin.com/in/nora-vale"


# --------------------------------------------------------------------------- LinkedIn follows the classify route
INES_URL = "https://www.linkedin.com/in/ines-alvarez-hm"
INES_CONV = "2-SU5FU0FMVkFSRVo="


def _seed_ines(roots):
    meta = pr.new_meta("ines-alvarez", "Ines Alvarez", source="linkedin-export", today=TODAY)
    meta.update({"role": "hiring-manager", "label": "hiring_manager_outreach", "company": "Harborline Robotics",
                 "linkedin_url": INES_URL, "linkedin_threads": [INES_CONV], "channels": ["linkedin"],
                 "status": "in_conversation", "last_inbound_at": "2026-09-10T17:00:00Z", "inbound_count": 1})
    body = ("# Ines Alvarez\n\n## Notes\n\n## Timeline\n- 2026-09-10T17:00:00Z · in · linkedin-message · "
            f"hiring_manager_outreach · \"ML engineer on my team\" · I lead perception · thread {INES_CONV} [k:li-old]\n")
    jc.write_record(roots.prospects / "ines-alvarez.md", meta, body)


def _ines_followup(roots, name="linkedin-2026-09-25.jsonl"):
    import linkedin_export as le
    person = {"key": INES_URL, "name": "Ines Alvarez", "linkedin_url": INES_URL, "company": "Harborline Robotics",
              "position": "Engineering Manager", "email": None, "sources": ["messages"], "conversation_ids": [INES_CONV]}
    text = "Thursday at 3 works for me. See you then!"
    touch = {"key": "li-msg-ines-2", "at": "2026-09-24T16:00:00Z", "person_key": INES_URL, "person_name": "Ines Alvarez",
             "channel": "linkedin-message", "direction": "in", "kind": "reply", "conversation_id": INES_CONV,
             "thread_id": INES_CONV, "subject": "", "text": text, "summary": text}
    out = roots.harvest / name
    le.write_jsonl(out, {"people": [person], "touches": [touch], "conversations": [], "connections": []})
    return out


def test_ingest_refuses_a_raw_linkedin_export_and_points_at_classify(tmp_path, capsys):
    roots = _roots(tmp_path)
    roots.prospects.mkdir(parents=True)
    _seed_ines(roots)
    before = (roots.prospects / "ines-alvarez.md").read_text()
    raw = _ines_followup(roots)
    assert _cli(roots, "ingest", str(raw)) == 2
    out = capsys.readouterr()
    assert "mail_classify.py classify" in out.out + out.err and raw.name in out.out + out.err
    assert (roots.prospects / "ines-alvarez.md").read_text() == before
    with pytest.raises(pr.IngestError):
        pr.ingest(roots, linkedin_files=[raw], today=TODAY)


def test_a_linkedin_follow_up_from_a_known_hiring_manager_is_matched_by_thread_not_review(tmp_path, capsys):
    roots = _roots(tmp_path)
    roots.prospects.mkdir(parents=True)
    _seed_ines(roots)
    raw = _ines_followup(roots)
    assert mc.main(["classify", "--in", str(raw), "--l3", "agent", "--today", "2026-09-25"]) == 0
    capsys.readouterr()
    classified = roots.harvest / "linkedin-2026-09-25.classified.jsonl"
    row = next(json.loads(x) for x in classified.read_text().splitlines() if json.loads(x)["type"] == "touch")
    assert row["classification"]["label"] == "hiring_manager_outreach", row["classification"]
    assert "ines-alvarez" in row["classification"]["evidence"] and "thread" in row["classification"]["evidence"]
    assert _cli(roots, "ingest") == 0                                 # the default picks the classified file
    capsys.readouterr()
    meta, body = _load(roots, "ines-alvarez")
    assert "needs-review" not in meta["tags"] and meta["inbound_count"] == 2
    assert meta["label"] == "hiring_manager_outreach" and "li-msg-ines-2" in body
    assert "linkedin-2026-09-25.classified.jsonl" in body                # read from the classified file
    assert "hiring_manager_outreach · \"\" · Thursday at 3" in body or \
        "linkedin-message · hiring_manager_outreach" in body.split("[k:li-old]", 1)[1]


# --------------------------------------------------------------------------- reconcile senders by email and profile URL
def _seed_people(roots):
    roots.prospects.mkdir(parents=True, exist_ok=True)
    for slug, extra in (("maya-chen", {"emails": ["maya.chen@heliosystems.test"]}),
                        ("leila-haddad", {"linkedin_url": "https://www.linkedin.com/in/leila-haddad-7a1b2c"}),
                        ("ines-alvarez", {"linkedin_url": INES_URL})):
        meta = pr.new_meta(slug, slug.replace("-", " ").title(), source="gmail-backlog", today=TODAY)
        meta.update(extra)
        jc.write_record(roots.prospects / f"{slug}.md", meta, "# x\n\n## Timeline\n")


def _reconcile_inputs(roots):
    cls = lambda label: {"label": label, "confidence": 0.9}                         # noqa: E731
    gmail = [
        {"id": "g1", "from": "Maya Chen <Maya.Chen@heliosystems.test>", "direction": "in",
         "classification": cls("recruiter_outreach")},
        {"id": "g2", "from": "Tom Reed <tom@brightpath.test>", "direction": "in",
         "classification": cls("recruiter_outreach")},                                  # dropped person
        {"id": "g3", "from": "News <news@list.test>", "direction": "in", "classification": cls("newsletter")},
        {"id": "g4", "from": "Leila Haddad <inmail-hit-reply@linkedin.com>", "direction": "in",
         "body": "Leila Haddad\nCo-founder\nView profile: https://www.linkedin.com/in/Leila-Haddad-7a1b2c/\nHey Alex!",
         "classification": cls("founder_outreach")},                                    # relay: checked by URL
        {"id": "g5", "from": f"Owner <{OWNER}>", "direction": "out", "classification": cls("other")},
    ]
    linkedin = [
        {"type": "person", "key": INES_URL, "name": "Ines Alvarez"},
        {"type": "touch", "key": "li-1", "person_key": INES_URL, "person_name": "Ines Alvarez", "direction": "in",
         "classification": cls("hiring_manager_outreach")},
        {"type": "touch", "key": "li-2", "person_key": "https://www.linkedin.com/in/new-person-99",
         "person_name": "New Person", "direction": "in", "classification": cls("recruiter_outreach")},  # dropped
        {"type": "touch", "key": "li-3", "person_key": "https://www.linkedin.com/in/new-person-99",
         "person_name": "New Person", "direction": "out", "classification": cls("other")},
    ]
    roots.harvest.mkdir(parents=True, exist_ok=True)
    g = roots.harvest / "gmail-2026-09-25.classified.jsonl"
    li = roots.harvest / "linkedin-2026-09-25.classified.jsonl"
    g.write_text("".join(json.dumps(r) + "\n" for r in gmail))
    li.write_text("".join(json.dumps(r) + "\n" for r in linkedin))
    return g, li


def test_reconcile_checks_emails_and_linkedin_urls_and_prints_missing(tmp_path, capsys):
    roots = _roots(tmp_path)
    _seed_people(roots)
    g, li = _reconcile_inputs(roots)
    rc = pr.main(["--root", str(roots.js), "--today", "2026-09-25", "reconcile", "--harvest", str(g), str(li)])
    out = capsys.readouterr().out
    missing = sorted(line for line in out.splitlines() if line.startswith("MISSING "))
    assert missing == ["MISSING https://www.linkedin.com/in/new-person-99", "MISSING tom@brightpath.test"]
    assert rc == 1
    senders = (roots.harvest / "senders-2026-09-25.txt").read_text().split()
    assert senders == sorted(["maya.chen@heliosystems.test", "tom@brightpath.test",
                              "https://www.linkedin.com/in/leila-haddad-7a1b2c", INES_URL,
                              "https://www.linkedin.com/in/new-person-99"])
    assert "inmail-hit-reply@linkedin.com" not in senders and "news@list.test" not in senders


def test_reconcile_exits_zero_when_every_human_sender_has_a_prospect(tmp_path, capsys):
    roots = _roots(tmp_path)
    _seed_people(roots)
    g, li = _reconcile_inputs(roots)
    for slug, extra in (("tom-reed", {"emails": ["tom@brightpath.test"]}),
                        ("new-person", {"linkedin_url": "https://www.linkedin.com/in/New-Person-99/"})):
        meta = pr.new_meta(slug, slug, source="gmail-backlog", today=TODAY)
        meta.update(extra)
        jc.write_record(roots.prospects / f"{slug}.md", meta, "# x\n")
    assert pr.main(["--root", str(roots.js), "--today", "2026-09-25", "reconcile", "--harvest", str(g), str(li)]) == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out.replace("0 MISSING", "") and "5 senders" in out


def test_apply_fills_every_missing_list_and_count_field_and_keeps_present_values():
    lists = [k for k, v in pr.new_meta("x", "x", "manual", TODAY).items() if isinstance(v, list)]
    counts = [k for k, v in pr.new_meta("x", "x", "manual", TODAY).items()
              if isinstance(v, int) and not isinstance(v, bool)]
    assert {"sources", "channels", "thread_ids", "emails", "linkedin_threads", "possible_duplicates", "tags",
            "applications", "score_reasons"} <= set(lists)
    assert {"inbound_count", "outbound_count", "follow_ups_sent"} <= set(counts)
    meta = {"id": "vic-example", "name": "Vic Example", "status": "new", "source": "gmail-backlog",
            "tags": None, "warmth": 2, "emails": ["vic@example.test"]}
    ev = {"key": "gm-vic-1", "at": "2026-09-29T10:00:00Z", "direction": "in", "channel": "email",
          "source": "gmail-backlog", "label": "recruiter_outreach", "person": {"name": "Vic Example",
          "emails": ["Vic@Example.test"], "linkedin_url": "", "company": None, "title": None},
          "subject": "Hello", "summary": "Hi", "text": "Hi", "thread_id": "t-vic", "tags": []}
    body, touch = pr._apply(meta, "# Vic Example\n\n## Timeline\n", ev, TODAY)
    assert all(isinstance(meta[k], list) for k in lists) and all(isinstance(meta[k], int) for k in counts)
    assert meta["inbound_count"] == 1 and meta["thread_ids"] == ["t-vic"] and meta["warmth"] == 2
    assert meta["emails"] == ["vic@example.test"] and meta["sources"] == ["gmail-backlog"]
    assert touch and "[k:gm-vic-1]" in body


def test_x_handle_is_a_prospect_field_stored_without_the_at_sign(monkeypatch):
    assert pr.validate_field("x_handle", "@Sam_Alba") == ("x_handle", "sam_alba")
    assert pr.validate_field("x_handle", "https://x.com/sam_alba") == ("x_handle", "sam_alba")
    with pytest.raises(ValueError, match="X handle"):
        pr.validate_field("x_handle", "not a handle!")
