"""graph_links.py: records carry `type:` and a `links:` list of path-qualified wikilinks, written by the owning
scripts on every write (js_common / js_comms_util write_record, outbox create, tracker new) and backfilled
idempotently by the CLI."""
from __future__ import annotations

import re
import sys
from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import graph_links as gl  # noqa: E402
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402
import outbox as ob  # noqa: E402
import prospects as pr  # noqa: E402
import tracker as tr  # noqa: E402

P = gl.VAULT_PREFIX
TODAY = date(2026, 9, 24)
FORM = re.compile(r"^\[\[" + re.escape(P) + r"/[a-z]+/[A-Za-z0-9._/-]+\|[^\]|]+\]\]$")


def _js(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    for d in ("jobs", "prospects", "companies", "outbox", "applications"):
        (js / d).mkdir(parents=True, exist_ok=True)
    return js


def _dossier(js, slug="acme"):
    jc.write_record(js / "companies" / f"{slug}.md", {"slug": slug, "name": slug.title(), "last_researched": "2026-09-20"},
                    f"# {slug}\n")


def _job(js, jid="acme-123", company_slug="acme", **extra):
    rec = {k: None for k in fa.JOB_FIELDS}
    rec.update({"id": jid, "company": company_slug.title(), "company_slug": company_slug, "title": "ML Engineer",
                "source": "greenhouse", "source_url": f"https://x/{jid}", "first_seen": "2026-09-20T00:00:00Z",
                "last_seen": "2026-09-20T00:00:00Z", "status": "fit_checked", **extra})
    return fa.save_job(js, rec)


def _prospect(js, pid="maya-chen", company_slug="acme"):
    cu.write_record(js / "prospects" / f"{pid}.md",
                    {"id": pid, "name": "Maya Chen", "company": "Acme", "company_slug": company_slug,
                     "source": "gmail-backlog", "status": "new", "priority": "P2", "warmth": 1,
                     "created": TODAY, "updated": TODAY, "tags": []}, "# Maya Chen\n\n## Timeline\n")


# ------------------------------------------------------------------------------ the form
def test_wikilink_is_the_vault_root_path_without_md_aliased_with_the_id():
    assert gl.wikilink("companies/acme", "acme") == f"[[{P}/companies/acme|acme]]"
    assert gl.wikilink("applications/a-b-202609/application.md", "a-b-202609") == \
        f"[[{P}/applications/a-b-202609/application|a-b-202609]]"


def test_scalar_targets_accept_links_bare_ids_and_reject_junk():
    assert gl._target("[[acme-123]]") == "acme-123"
    assert gl._target("[[acme-123|Acme]]") == "acme-123"
    assert gl._target(f"[[{P}/jobs/acme-123|x]]") == "acme-123"
    assert gl._target("acme-123") == "acme-123"
    assert gl._target(None) is None and gl._target("") is None and gl._target("two words") is None
    assert gl._targets(["a", "[[b]]", None]) == ["a", "b"]


def test_kind_of_matches_records_only(tmp_path):
    js = _js(tmp_path)
    assert gl.kind_of(js / "jobs" / "x.md") == ("job", js)
    assert gl.kind_of(js / "prospects" / "x.md") == ("prospect", js)
    assert gl.kind_of(js / "outbox" / "O-2026-0001.md") == ("outbox", js)
    assert gl.kind_of(js / "applications" / "s" / "application.md") == ("application", js)
    for other in ("applications/s/fit.md", "applications/s/jd.md", "applications/s/HUB.md", "jobs/README.md",
                  "jobs/_draft.md", "companies/acme.md", "state/role-hubs.md"):
        assert gl.kind_of(js / other) is None, other


# ------------------------------------------------------------------------------ links per kind
def test_links_only_to_records_that_exist(tmp_path):
    js = _js(tmp_path)
    meta = {"company_slug": "acme"}
    assert gl.links_for("job", js, meta) == []                     # no dossier yet: no dangling edge
    _dossier(js)
    assert gl.links_for("job", js, meta) == [f"[[{P}/companies/acme|acme]]"]
    assert gl.links_for("prospect", js, meta) == [f"[[{P}/companies/acme|acme]]"]


def test_application_links_job_company_and_prospects_in_order(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    _job(js)
    _prospect(js)
    meta = {"job": "[[acme-123]]", "company_slug": "acme", "prospects": ["maya-chen", "ghost-person"]}
    assert gl.links_for("application", js, meta) == [
        f"[[{P}/jobs/acme-123|acme-123]]", f"[[{P}/companies/acme|acme]]", f"[[{P}/prospects/maya-chen|maya-chen]]"]


def test_outbox_links_prospect_and_application(tmp_path):
    js = _js(tmp_path)
    _prospect(js)
    (js / "applications" / "acme-ml-202609").mkdir()
    (js / "applications" / "acme-ml-202609" / "application.md").write_text("---\nid: acme-ml-202609\n---\n")
    links = gl.links_for("outbox", js, {"prospect": "maya-chen", "application": "acme-ml-202609"})
    assert links == [f"[[{P}/prospects/maya-chen|maya-chen]]",
                     f"[[{P}/applications/acme-ml-202609/application|acme-ml-202609]]"]
    assert all(FORM.match(link) for link in links)
    assert gl.links_for("outbox", js, {"prospect": None}) == []   # an official (DSO) draft names no prospect


def test_stamp_sets_type_first_keeps_scalars_and_is_idempotent(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    meta = {"id": "acme-123", "company": "Acme", "company_slug": "acme", "application": "[[acme-ml-202609]]"}
    assert gl.stamp("job", js, meta) is True
    assert list(meta)[0] == "type" and meta["type"] == "job"
    assert meta["company_slug"] == "acme" and meta["application"] == "[[acme-ml-202609]]"   # scalars untouched
    assert meta["links"] == [f"[[{P}/companies/acme|acme]]"]
    assert gl.stamp("job", js, meta) is False                     # idempotent


def test_stamp_keeps_an_existing_type_and_drops_a_stale_empty_links(tmp_path):
    js = _js(tmp_path)
    meta = {"type": "prospect", "company_slug": "gone", "links": [f"[[{P}/companies/gone|gone]]"]}
    assert gl.stamp("prospect", js, meta) is True
    assert meta == {"type": "prospect", "company_slug": "gone"}


# ------------------------------------------------------------------------------ the owning writers
def test_save_job_writes_type_and_the_company_link(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    path = _job(js)
    meta, _ = jc.read_record(path)
    assert meta["type"] == "job" and meta["links"] == [f"[[{P}/companies/acme|acme]]"]
    text = path.read_text()
    fa.save_job(js, fa.load_jobs(js)["acme-123"])                 # a re-write changes nothing
    assert path.read_text() == text


def test_a_job_without_a_dossier_gets_type_and_no_links(tmp_path):
    js = _js(tmp_path)
    meta, _ = jc.read_record(_job(js, "nodossier-1", "nodossier"))
    assert meta["type"] == "job" and "links" not in meta


def test_tracker_new_links_the_application_to_job_and_company(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    _job(js)
    slug, created = tr.new_application(js, "acme-123", NOW)
    assert created
    meta, _ = jc.read_record(js / "applications" / slug / "application.md")
    assert meta["type"] == "application"
    assert meta["links"] == [f"[[{P}/jobs/acme-123|acme-123]]", f"[[{P}/companies/acme|acme]]"]
    assert meta["job"] == "[[acme-123]]"                          # the scalar the scripts read is unchanged
    fit, _ = jc.read_record(js / "applications" / slug / "fit.md")
    assert "links" not in fit and "type" not in fit               # fit.md and jd.md are not graph records


def test_prospect_write_links_the_company(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    _prospect(js)
    meta, _ = jc.read_record(js / "prospects" / "maya-chen.md")
    assert meta["type"] == "prospect" and meta["links"] == [f"[[{P}/companies/acme|acme]]"]


def test_prospects_ingest_writes_type_on_every_new_record(tmp_path):
    js = _js(tmp_path)
    roots = cu.Roots(js=js, os=tmp_path / "os")
    pr.ingest_events(roots, [
        {"key": "gm-x", "at": "2026-08-28T17:04:11Z", "direction": "in", "channel": "email", "source": "gmail-backlog",
         "label": "recruiter_outreach", "confidence": 0.9, "create": True,
         "person": {"name": "Ann Lee", "emails": ["ann@acme.test"], "linkedin_url": "", "company": "Acme",
                    "title": "Recruiter"},
         "subject": "Role", "summary": "Hi", "text": "Hi Alex", "thread_id": "t-x", "message_id": "<m@x>",
         "references": "", "reply_to": "", "tags": []}], today=TODAY)
    for path in (js / "prospects").glob("*.md"):
        meta, _ = jc.read_record(path)
        assert meta["type"] == "prospect", path.name


def test_outbox_create_writes_type_and_the_prospect_link(tmp_path):
    js = _js(tmp_path)
    _prospect(js)
    roots = cu.Roots(js=js, os=tmp_path / "os")
    path = ob.create_draft(roots, prospect="maya-chen", channel="linkedin-message", body="Hi Maya, thanks.",
                           today=TODAY)
    meta, _ = jc.read_record(path)
    assert meta["type"] == "outbox"
    assert meta["links"] == [f"[[{P}/prospects/maya-chen|maya-chen]]"]


def test_approvals_carry_type_approval(tmp_path):
    js = _js(tmp_path)
    roots = cu.Roots(js=js, os=tmp_path / "os")
    item = cu.ApprovalItem(id="O-2026-0001", prospect=None, channel="email", target="a@b.c", subject="S", body="B")
    _, path = cu.write_approval(roots, action="send-email", items=[item], requested_by="role:messenger", why="w",
                                effect="e", today=TODAY)
    meta, _ = jc.read_record(path)
    assert meta["type"] == "approval"


# ------------------------------------------------------------------------------ backfill CLI
def _legacy(js):
    """Records as they were before the edges existed: no type, no links."""
    _dossier(js)
    (js / "jobs" / "acme-123.md").write_text("---\nid: acme-123\ncompany: Acme\ncompany_slug: acme\nstatus: queued\n---\n")
    (js / "jobs" / "zeta-9.md").write_text("---\nid: zeta-9\ncompany: Zeta\ncompany_slug: zeta\nstatus: discovered\n---\n")
    (js / "outbox" / "O-2026-0001.md").write_text("---\nid: O-2026-0001\nprospect: null\nstatus: pending\n---\nBody\n")


def test_backfill_check_is_a_dry_run_and_write_is_idempotent(tmp_path, capsys):
    js = _js(tmp_path)
    _legacy(js)
    before = (js / "jobs" / "acme-123.md").read_text()
    assert gl.main(["--root", str(js), "--check"]) == 1
    assert (js / "jobs" / "acme-123.md").read_text() == before     # dry run writes nothing
    out = capsys.readouterr().out
    assert "job: 2 record(s) · type 0 -> 2 · with links 0 -> 1" in out and "would change 2" in out
    assert gl.main(["--root", str(js), "--write"]) == 0
    meta, _ = jc.read_record(js / "jobs" / "acme-123.md")
    assert meta["type"] == "job" and meta["links"] == [f"[[{P}/companies/acme|acme]]"]
    assert jc.read_record(js / "outbox" / "O-2026-0001.md")[0]["type"] == "outbox"
    assert gl.main(["--root", str(js), "--check"]) == 0              # second run: nothing to do
    report = gl.backfill(js, write=True)
    assert all(r["to_change"] == 0 for r in report.values())


def test_backfill_changes_only_the_type_and_links_lines(tmp_path):
    js = _js(tmp_path)
    _dossier(js)
    path = _job(js)
    meta, body = jc.read_record(path)
    meta.pop("type"), meta.pop("links")
    path.write_text(jc.fm.dump(meta, body))
    old = path.read_text().splitlines()
    gl.backfill(js, ["job"], write=True)
    new = path.read_text().splitlines()
    added = [ln for ln in new if ln not in old]
    assert "type: job" in added and "links:" in added
    assert all(ln.startswith(("type:", "links:", "- ")) for ln in added), added
    assert [ln for ln in old if ln not in new] == []


def test_the_link_form_resolves_in_the_os_lint(tmp_path, monkeypatch):
    # the OS lint resolves vault-root paths, and _js() builds the job-search root at this place in the vault
    monkeypatch.setattr(gl, "VAULT_PREFIX", "domains/career/projects/job-search")
    P = gl.VAULT_PREFIX  # noqa: N806
    sys.path.insert(0, str(__import__("agency_paths").KERNEL_DIR))
    import lint
    root = tmp_path / "os"
    js = _js(tmp_path)
    _dossier(js)
    _job(js)
    _prospect(js)
    tr.new_application(js, "acme-123", NOW)
    bad = [f for f in lint.check_links(root) if f.code == "broken-link"]
    assert bad == []
    (js / "applications" / "x").mkdir()
    (js / "applications" / "x" / "application.md").write_text(
        f"---\nlinks: ['[[{P}/jobs/acme|acme]]']\n---\n")          # basename exists, as companies/acme.md
    bad = [f.message for f in lint.check_links(root) if f.code == "broken-link"]
    assert len(bad) == 1 and "jobs/acme" in bad[0]                 # a path link must name the real file
