#!/usr/bin/env python3
"""apply_batch.py - the batch path for the mass tier: many applications at once, one sheet.

Given N application slugs, build every apply pack with scripts/apply_pack.py (the same gates, nothing relaxed), lint
each, and write one sheet state/apply-batches/<date>-<n>.md: per role the apply link, the PDF to upload, the status and
its blockers or open questions, then the tracker command (and the approval receipt) per READY role to log the submit.
The agent submits (charter section 7): the owner's one batch yes, recorded in ONE approval file with the owner's words
(scripts/kernel/approvals.py decide, or day_queue.py build --yes-quote), covers exactly the items it lists; --approval
marks each READY row it covers "submit on A-...", and the apply lane fills and submits those rows on the employer's
own site in that session. The sheet is the record and receipt of that yes, never a second approval. This script itself
submits and sends nothing.

Tiers (.agents/skills/resume-for/SKILL.md "The three tiers"):
  focused, dream  the editor-approved variant in resumes/variants/<slug>/ is the page, exactly as apply_pack.py builds
                  it; no variant, no page (REFUSED).
  mass            an approved variant is used when there is one; when none is approved, the page is the title's
                  profile base (scripts/profile_route.py maps the title's lens to one of the
                  resumes/bases/profiles/<slug>/ folders and sends its PDF only when that profile is signed, resume.yaml
                  and all) and otherwise the general base (resumes/bases/general, the current promoted base,
                  pinned in its SHA256.txt; --pdf inline: the inline-dates PDF; --profile general forces it, --profile
                  <slug> forces that profile), by effort
                  (--effort, else scripts/effort.py on the job record and its companies.yaml entry):
    E2 (the base fits; no fit.md, no matrix) READY when the job passes fit_gates.py now (a real employer), the
       application is not applied before (applications/<slug>/application.md), the answers bank is clean
       (answers_check.py), the page's PDF is the signed profile base's or the base PDF and resume.yaml match
       resumes/bases/general/SHA256.txt, its layout report is a PASS (a missing report is measured now with
       page_layout_check.py, never re-rendered), and the apply link is the employer's own site. A LinkedIn Easy Apply,
       Indeed Apply or ZipRecruiter 1-Click link is CLICK: everything prepared, the owner's click (platform terms). A
       missing legal/checks/<company>.md is a note, "visa check pending"; a check that says blocked blocks, and so does
       e_verify no when policy.apply.require_everify_employer is on. Base order (charter section 8 A/B), one switch in
       targets/mass.yaml: base_ab off (the default) uses education-first everywhere; on lets base_order_for(job id)
       pick education-first or experience-first, about half each (general base only: a profile base has its own
       order), and an experience-first row uses the targets/mass.yaml experience_first copy only while it is the
       base's words in another order, pinned or signed, with a layout PASS (else education-first with a TODO note).
       The order used is written as base_order in application.md.
    E3 and above: a scored fit first (fit_score on the job or in applications/<slug>/fit.md), then
       (1) the base pins, (2) the layout PASS, (3) research/targets/<slug>.matrix.yaml and applications/<slug>/jd.md,
       (4) rubric_check.py --tier mass on the base with that matrix exits 0, (5) not applied and the visa gate
       (legal/checks/<company>.md) clear. A failure is REFUSED with "promote to focused".
    E1 (a gate fail) is REFUSED. No pack is built for a base row: answers come from `apply_pack.py answers`.

Reader model: a focused or dream role whose Role Package research/targets/<slug>.md has a section 6 that fails
`reader_model.py check` is BLOCKED with "reader model: <first error>". A package written without a section 6, or no
package file, is a WARNING on the row, except a DREAM row with no package file at all, which is BLOCKED "no role
package: run /role-package <slug>" (full research on every dream page). Mass is exempt.

Status per role:
  READY    the pack built with status ready and lint is clean (or an E2 base row passed): safe to fill the form;
           submitted by the agent only when --approval covers it ("submit on A-...").
  CLICK    an E2 base row whose only link is a one-click platform apply: prepared, the owner clicks.
  BLOCKED  the pack built with blockers (written to applications/<slug>/apply-pack.md, listed on the sheet).
  LINT     the pack built ready but lint found something (listed): fix, rebuild, then apply.
  REFUSED  no pack could be built (unknown application, variant not editor-approved, malformed slug); the reason
           is on the sheet.

CLI:  python3 scripts/apply_batch.py [--root R] [--today D] [--tier mass|focused|dream] [--pdf classic|inline]
                                     [--effort E2|E3|E4|E5] [--approval A-YYYY-NNNN] [--file LIST] [--dry-run] [slug ...]
      LIST: one slug per line; blank lines and lines starting with # are skipped. Duplicates run once, in order.
      exit 0 every role READY · 1 any role not READY (the sheet is still written) · 2 no slugs given
      --dry-run builds in memory only: no pack, no sheet is written; the summary is printed. Lint reads the written
      pack, so a dry-run READY means "built ready, not linted yet"; the real run lints every pack.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_pack as ap  # noqa: E402
import profile_route as pr  # noqa: E402
import agency_config as acfg  # noqa: E402
from js_common import JS_ROOT, os_root_for, cli_date, fm  # noqa: E402

TIERS = ("mass", "focused", "dream")
BASE_DIR = Path("resumes") / "bases" / "general"
BASE_PDF = dict(pr.PDF_NAMES)                      # the PDF file names, from identity.resume_pdf_name
NOT_APPROVED = "variant not editor-approved: "
PROMOTE = "promote to focused"
BATCH_DIR = Path("state") / "apply-batches"
STATUSES = ("ready", "click", "blocked", "lint", "refused")
EXP_FIRST_DIR = Path("resumes") / "bases" / "general-experience-first"   # the default home; targets/mass.yaml names it
MASS_CONFIG = Path("targets") / "mass.yaml"        # base_ab: off|on, experience_first: <folder> (the one switch)
ORDERS = ("education-first", "experience-first")
ONE_CLICK = ((r"(^|\.)linkedin\.com$", "LinkedIn Easy Apply"), (r"(^|\.)indeed\.com$", "Indeed Apply"),
             (r"(^|\.)ziprecruiter\.com$", "ZipRecruiter 1-Click"))
SIDECAR_RE = re.compile(r"^\s*(base_of|lens):")
VISA_NOTE = str(acfg.get("policy.apply.visa_note", "") or "")     # the written work-authorization answer, appended to a pending-check note


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def next_up_rows(root: Path, n: int) -> list[tuple[str, str]]:
    """(slug, pdf) of the top READY rows of the generated NEXT-UP, best first: a READY row is one whose
    application folder links a page apply_pack already ships (next_up.page_state), with the PDF choice that ships.
    The batch takes its roles from the one ranked list, never a hand file."""
    import next_up
    from datetime import datetime, timezone
    rows = next_up.build(Path(root), datetime.now(timezone.utc))["rows"]
    return [(r["slug"], r.get("pdf") or "classic") for r in rows if r["state"] == "READY" and r.get("slug")][:n]


def next_up_slugs(root: Path, n: int) -> list[str]:
    return [slug for slug, _pdf in next_up_rows(root, n)]


def read_slugs(args: argparse.Namespace) -> list[str]:
    slugs = list(args.slugs)
    if getattr(args, "next_up", None):
        cached = getattr(args, "_next_up_slugs", None)
        slugs += cached if cached is not None else next_up_slugs(args.root, args.next_up)
    if args.file:
        if not Path(args.file).is_file():
            raise FileNotFoundError(f"no such list: {args.file} (use --next-up N to take the READY rows of hub/NEXT-UP.md)")
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                slugs.append(line)
    seen: set[str] = set()
    return [s for s in slugs if not (s in seen or seen.add(s))]


def apply_link(root: Path, slug: str) -> str:
    """The job's apply URL (else its source URL) from jobs/<id>.md; empty when the records do not say."""
    try:
        _app, _job_id, job, _rel = ap.load_application(root, slug)
    except ap.Refusal:
        return ""
    return ap.one_line(job.get("apply_url") or job.get("source_url") or "")


def rubric_on_base(root: Path, slug: str, today: date, base_yaml: str | None = None) -> tuple[int, str]:
    """rubric_check.py on the base page (the general base, or the profile base's resume.yaml given as `base_yaml`,
    relative to the root) against the slug's matrix and JD: (exit code, the failing predicates)."""
    import rubric_check
    base = str(root / (base_yaml or (BASE_DIR / "resume.yaml").as_posix()))
    argv = ["--resume", base, "--base", base, "--matrix", str(root / "research" / "targets" / f"{slug}.matrix.yaml"),
            "--jd", str(root / "applications" / slug / "jd.md"), "--date", today.isoformat(), "--json", "--tier", "mass"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = rubric_check.main(argv)
        except SystemExit as exc:              # argparse usage errors
            code = int(exc.code or 2)
    return code, failed_predicates(out.getvalue())


def failed_predicates(report_json: str) -> str:
    """'B5 FAIL, B9 FAIL' from rubric_check --json (its `failed` list, else the predicates whose status is FAIL)."""
    try:
        report = json.loads(report_json)
    except ValueError:
        return ""
    ids = [str(i) for i in report.get("failed") or []] or [
        str(p.get("id")) for p in report.get("predicates") or [] if isinstance(p, dict) and p.get("status") == "FAIL"]
    return ", ".join(f"{i} FAIL" for i in ids)


def _pinned(root: Path, name: str, base_dir: Path = BASE_DIR) -> bool:
    pins = {}
    path = root / base_dir / "SHA256.txt"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                pins[parts[1]] = parts[0]
    f = root / base_dir / name
    return f.is_file() and pins.get(name) == hashlib.sha256(f.read_bytes()).hexdigest()


def base_layout(root: Path, pdf: str, write: bool, base_dir: Path = BASE_DIR) -> str | None:
    """On a base PDF: None when resumes/bases/<dir>/layout/<pdf>/layout-report.json is a PASS for exactly that
    PDF, else why not. A missing report is measured now (page_layout_check.py on the pinned PDF; a measurement, never
    a render): written into that folder on a real run, into a temp folder on a dry run (so a base row is not blocked
    only because the report was never written)."""
    pdf_name = BASE_PDF[pdf]
    base_pdf, out_rel = root / base_dir / pdf_name, f"{base_dir.as_posix()}/layout/{pdf}"
    sha = hashlib.sha256(base_pdf.read_bytes()).hexdigest()
    report = root / out_rel / ap.LAYOUT_REPORT

    def why_not(rep: Path) -> str | None:
        return ap.layout_refusal(rep, f"{out_rel}/{ap.LAYOUT_REPORT}", base_pdf, sha,
                                 f"{base_dir.as_posix()}/{pdf_name}", out_rel)
    why = why_not(report)
    if why is None or report.is_file():
        return why
    try:
        import page_layout_check
        if write:
            page_layout_check.check_pdf(base_pdf, out_dir=root / out_rel, max_pages=1)
            return why_not(report)
        with tempfile.TemporaryDirectory() as tmp:
            page_layout_check.check_pdf(base_pdf, out_dir=Path(tmp), max_pages=1)
            return why_not(Path(tmp) / ap.LAYOUT_REPORT)
    except Exception as exc:  # noqa: BLE001 - a toolchain error leaves the page unmeasured: it stays refused
        return f"{why} (measuring it now failed: {type(exc).__name__})"


def profile_layout(root: Path, route: pr.Route) -> str | None:
    """On a profile base PDF: None when the layout-report.json beside it is a PASS for exactly that PDF, else why
    not (a profile with no PASS report is not used; the general base is)."""
    pdf_path = root / route.pdf
    report = pdf_path.parent / ap.LAYOUT_REPORT
    shown = (Path(route.pdf).parent / ap.LAYOUT_REPORT).as_posix()
    return ap.layout_refusal(report, shown, pdf_path, hashlib.sha256(pdf_path.read_bytes()).hexdigest(), route.pdf)


def profile_page(root: Path, title: Any, pdf: str, profile: str = "auto") -> tuple[pr.Route | None, list[str]]:
    """(The signed profile base for this title, a note) or (None, a note saying why the general base is used).
    Only a usable profile (resume.yaml signed, PDF present) with a layout PASS is used; the general base is the
    fallback, and the caller still pins it against its SHA256.txt."""
    route = pr.resolve(root, ap.one_line(title), pdf, profile, verify_general=False)
    if route.source == "profile":
        why = profile_layout(root, route)
        if why is None:
            return route, [f"{route.profile} profile base ({route.why})"]
        return None, [f"profile {route.profile} not used, its page failed: {why}; general base used"]
    return None, [f"{route.why}"]


def base_row(root: Path, slug: str, today: date, pdf: str, row: dict[str, Any], why: str,
             effort: str | None = None, dry_run: bool = False, profile: str = "auto") -> dict[str, Any]:
    """The mass-tier fallback to the base (see the module docstring): E2 through e2_row, E3 and above here."""
    refuse = lambda reason: {**row, "status": "refused", "notes": [f"{reason}; {PROMOTE}"]}   # noqa: E731
    app, job_id, job, _rel = ap.load_application(root, slug)
    row.update(company=ap.one_line(job.get("company") or app.get("company")),
               title=ap.one_line(job.get("title") or app.get("title")), link=apply_link(root, slug), job=job_id)
    level, reason = row_effort(root, job, effort)
    row["effort"] = level
    if level == "E1":
        return {**row, "status": "refused", "notes": [f"E1: {reason}"]}
    if level == "E2":
        return e2_row(root, slug, today, pdf, row, app, job_id, job, dry_run, profile)
    if not scored_fit(root, slug, job):                  # a scored fit is required for E3 and above
        return refuse(f"{level} ({reason}) needs a scored fit: the fit-scorer writes applications/{slug}/fit.md")
    pdf_name = BASE_PDF[pdf]
    route, route_notes = profile_page(root, row.get("title") or job.get("title"), pdf, profile)
    if route is None:
        if not (_pinned(root, pdf_name) and _pinned(root, "resume.yaml")):
            return refuse(f"general base {pdf_name} or resume.yaml does not match {BASE_DIR}/SHA256.txt")
        layout_why = base_layout(root, pdf, write=not dry_run)
        if layout_why:
            return refuse(layout_why)
    matrix = root / "research" / "targets" / f"{slug}.matrix.yaml"
    if not matrix.is_file():
        return refuse(f"no matrix research/targets/{slug}.matrix.yaml (run /role-package {slug})")
    if not (root / "applications" / slug / "jd.md").is_file():
        return refuse(f"no applications/{slug}/jd.md")
    code, fails = (rubric_on_base(root, slug, today, route.resume_yaml) if route
                   else rubric_on_base(root, slug, today))
    if code != 0:
        page_name = f"the {route.profile} profile base" if route else "the general base"
        return refuse(f"rubric_check on {page_name} failed (exit {code}{': ' + fails if fails else ''})")
    blockers = []
    stage = str(app.get("stage") or "")
    if stage in ap.APPLIED_STAGES:
        blockers.append(f"application stage is {stage}: already applied; never apply twice (tracker-ops)")
    ctx = ap.load_ctx(root, today)
    blockers += ap.visa_gate(ctx, ap.norm_key(app.get("company_slug") or job.get("company_slug")))[1]
    row.update(pdf=route.pdf if route else f"{BASE_DIR.as_posix()}/{pdf_name}", base=True)
    if blockers:
        return {**row, "status": "blocked", "notes": blockers + route_notes}
    label = f"{route.profile} profile base" if route else "general base"
    return {**row, "status": "ready",
            "notes": [f"{label} page (mass tier: {why}); no pack: answer from `python3 scripts/apply_pack.py answers`"]
            + route_notes}


# --------------------------------------------------------------------------- E2: the base, no matrix (charter section 7)
def row_effort(root: Path, job: dict[str, Any], override: str | None) -> tuple[str, str]:
    """(E1..E5, why) for a base row: --effort when given, else effort.py on the job and its companies.yaml entry."""
    if override:
        return override, "--effort given"
    import effort as eff
    company = None
    try:
        from fetch_ats import load_companies
        slug = ap.norm_key(job.get("company_slug"))
        company = next((c for c in load_companies(root) if ap.norm_key(c.get("slug")) == slug), None)
    except Exception:  # noqa: BLE001 - no watchlist: the job record alone
        company = None
    return eff.effort_for(job, company)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scored_fit(root: Path, slug: str, job: dict[str, Any]) -> bool:
    """A fit score on the job record or in applications/<slug>/fit.md (the fit-scorer's)."""
    if _num(job.get("fit_score")) is not None:
        return True
    path = root / "applications" / slug / "fit.md"
    if not path.is_file():
        return False
    try:
        meta, _ = fm.load(path)
    except Exception:  # noqa: BLE001
        return False
    return _num(meta.get("fit_score")) is not None


def base_order_for(key: str) -> str:
    """Charter section 8 A/B: the order a mass application uses, from its job id (sha256, first byte even: education-first).
    Deterministic, about half each, and both orders run at many companies."""
    return ORDERS[hashlib.sha256(str(key).encode("utf-8")).digest()[0] % 2]


def mass_config(root: Path) -> dict[str, Any]:
    """targets/mass.yaml: base_ab (off unless it says on or true) and the experience_first folder."""
    path = root / MASS_CONFIG
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception:  # noqa: BLE001 - an unreadable config is the safe default: A/B off
        data = {}
    data = data if isinstance(data, dict) else {}
    on = data.get("base_ab") is True or str(data.get("base_ab") or "").strip().lower() == "on"
    return {"base_ab": on, "experience_first": str(data.get("experience_first") or EXP_FIRST_DIR.as_posix())}


def experience_first_copy(root: Path, pdf: str, write: bool, folder: str | None = None) -> tuple[str | None, str]:
    """(the PDF path, "") when the Experience-first folder (targets/mass.yaml experience_first) is an order-only copy
    of the base (every line of its resume.yaml is the base's, sidecar base_of and lens aside), pinned (its SHA256.txt,
    or an approved review.md whose variant_hash and pdf_sha256 match), with a layout PASS; else (None, why). The owner
    promotes it; this script never makes it."""
    rel = Path(folder or EXP_FIRST_DIR)
    copy, pdf_name = root / rel, BASE_PDF[pdf]
    if not (copy / "resume.yaml").is_file():
        return None, f"no {rel.as_posix()}/resume.yaml (the owner promotes an Experience-first copy of the base)"
    lines = lambda p: sorted(x for x in p.read_text(encoding="utf-8").splitlines() if not SIDECAR_RE.match(x))  # noqa: E731
    if lines(copy / "resume.yaml") != lines(root / BASE_DIR / "resume.yaml"):
        return None, f"{rel.as_posix()}/resume.yaml is not the base's words in another order"
    if (copy / "SHA256.txt").is_file():
        if not (_pinned(root, pdf_name, rel) and _pinned(root, "resume.yaml", rel)):
            return None, f"{rel.as_posix()}/{pdf_name} or resume.yaml does not match its SHA256.txt"
        why = base_layout(root, pdf, write, rel)
        return (None, why) if why else (f"{rel.as_posix()}/{pdf_name}", "")
    if pdf != "classic":                                 # a signed variant ships its classic send/ PDF only
        return None, f"{rel.as_posix()} has no {pdf} render"
    send = copy / "send" / pdf_name
    review = copy / "review.md"
    if not (send.is_file() and review.is_file()):
        return None, f"{rel.as_posix()} has no send/{pdf_name} or review.md"
    meta, _ = fm.load(review)
    yaml_sha = hashlib.sha256((copy / "resume.yaml").read_bytes()).hexdigest()
    pdf_sha = hashlib.sha256(send.read_bytes()).hexdigest()
    if meta.get("verdict") != "approved" or ap.norm_hash(meta.get("variant_hash")) != yaml_sha \
            or ap.norm_hash(meta.get("pdf_sha256")) != pdf_sha:
        return None, f"{rel.as_posix()}/review.md is not an approved verdict on this resume.yaml and PDF"
    send_rel = f"{rel.as_posix()}/send"
    why = ap.layout_refusal(copy / "send" / ap.LAYOUT_REPORT, f"{send_rel}/{ap.LAYOUT_REPORT}", send, pdf_sha,
                            f"{send_rel}/{pdf_name}")
    return (None, why) if why else (f"{send_rel}/{pdf_name}", "")


def one_click(url: str) -> str:
    """The one-click channel the platform terms bar for agents (charter section 7), or "" for an employer site."""
    host = (urlparse(url).hostname or "").lower()
    return next((name for pattern, name in ONE_CLICK if host and re.search(pattern, host)), "")


_ANSWERS: dict[str, int] = {}


def row_family(root: Path, title: Any) -> str | None:
    """The focus family a row's title reads as (focus_profiles.pick, best first), or None when it matches none."""
    import focus_profiles
    picked = focus_profiles.pick(root, str(title or ""))
    return picked[0]["family"] if picked else None


def answers_clean(root: Path, family: str | None = None) -> int:
    """answers_check.py exit code (0 clean), once per root and family per run. With a family only that family's bank
    (profile/answers/<family>.md) is checked, so a missing or dirty bank of another family blocks no row; without
    one, the whole bank, as the CLI's default run does."""
    key = f"{Path(root).resolve()}::{family or ''}"
    if key not in _ANSWERS:
        import answers_check
        args = ["--root", str(root)] + ([str(Path(root) / "profile" / "answers" / f"{family}.md")] if family else [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                _ANSWERS[key] = int(answers_check.main(args) or 0)
            except SystemExit as exc:
                _ANSWERS[key] = int(exc.code or 2)
    return _ANSWERS[key]


def gate_job(root: Path, job_id: str, job: dict[str, Any], slug: str) -> dict[str, Any]:
    """fit_gates.evaluate on the job now: the location rule, a real employer, the rest of the gates."""
    import fit_gates
    from fetch_ats import jd_text_from_body
    _, body = fm.load(root / "jobs" / f"{job_id}.md")
    jd = jd_text_from_body(body)
    if not jd.strip() and (root / "applications" / slug / "jd.md").is_file():
        jd = fm.load(root / "applications" / slug / "jd.md")[1]
    company = None
    try:
        from fetch_ats import load_companies
        company = next((c for c in load_companies(root)
                        if ap.norm_key(c.get("slug")) == ap.norm_key(job.get("company_slug"))), None)
    except Exception:  # noqa: BLE001
        company = None
    return fit_gates.evaluate(dict(job), jd, fit_gates.load_config(root), company)


def e2_visa(root: Path, company_slug: str) -> tuple[list[str], list[str]]:
    """(blockers, notes). Sponsorship is weighed, never a skip, so a missing check is a note ("visa check
    pending"); a check whose verdict is blocked blocks, and so does one that says e_verify no when
    policy.apply.require_everify_employer is on."""
    rel = f"legal/checks/{company_slug}.md"
    if not company_slug or not (root / rel).is_file():
        return [], [f"visa check pending ({rel} missing; legal-visa runs visa-check; weighed, never a skip)"
                    + (f". {VISA_NOTE}" if VISA_NOTE else "")]
    try:
        meta, _ = fm.load(root / rel)
    except Exception:  # noqa: BLE001
        return [], [f"visa check pending ({rel} unreadable; legal-visa)"]
    verdict = ap.one_line(meta.get("verdict")) or "no verdict"
    ev = meta.get("e_verify")
    ev = "yes" if ev is True else "no" if ev is False else (ap.one_line(ev).lower() or "unknown")
    blockers = []
    if verdict == "blocked":
        blockers.append(f"visa gate: {rel} verdict blocked (legal-visa; only the owner rules on it)")
    if ev == "no" and ap.REQUIRE_EVERIFY:
        blockers.append(f"visa gate: {rel} says e_verify no (this search needs an E-Verify employer)")
    return blockers, [f"visa: {verdict} (E-Verify {ev}; {rel})"]


def e2_row(root: Path, slug: str, today: date, pdf: str, row: dict[str, Any], app: dict[str, Any], job_id: str,
           job: dict[str, Any], dry_run: bool, profile: str = "auto") -> dict[str, Any]:
    """Charter section 7: an E2 mass role (the base fits) needs no fit.md and no matrix. READY when the job passes
    fit_gates now, it is not applied before, the answers bank is clean, and the apply link is the employer's own
    site; a LinkedIn Easy Apply, Indeed Apply or ZipRecruiter 1-Click link is CLICK (the owner's click, everything
    prepared). The page is the base in the order base_order_for(job id) picks (charter section 8 A/B); base_order goes into
    application.md on a real run."""
    stage = str(app.get("stage") or "")
    if stage in ap.APPLIED_STAGES or stage in ap.CLOSED_STAGES:
        return {**row, "status": "blocked",
                "notes": [f"application stage is {stage}: already applied; never apply twice (tracker-ops)"]}
    gate = gate_job(root, job_id, job, slug)
    if gate["gate"] == "fail":
        return {**row, "status": "refused", "notes": ["fit_gates fail: " + "; ".join(gate["reasons"][:3])]}
    blockers: list[str] = []
    if gate["gate"] == "review":
        blockers.append("fit_gates review open: " + "; ".join(r for r in gate["reasons"] if not r.startswith("note:"))[:300])
    family = row_family(root, job.get("title"))
    if family is None:                                   # missing is not empty: no family, no bank to trust
        blockers.append(f"answers bank not clean: no focus family for '{job.get('title')}' "
                        "(focus_profiles.py pick); the row has no bank to scope to")
    else:
        code = answers_clean(root, family)
        if code != 0:
            blockers.append(f"answers bank not clean for {family} (scripts/answers_check.py exit {code})")
    visa_block, notes = e2_visa(root, ap.norm_key(app.get("company_slug") or job.get("company_slug")))
    blockers += visa_block
    pdf_name = BASE_PDF[pdf]
    route, route_notes = profile_page(root, row.get("title") or job.get("title"), pdf, profile)
    notes += route_notes
    if route is not None:                                # the signed profile base is the page; no A/B on it
        order, page = f"profile-{route.profile}", route.pdf
    else:
        if not (_pinned(root, pdf_name) and _pinned(root, "resume.yaml")):
            return {**row, "status": "refused", "notes": [f"the base {pdf_name} or resume.yaml does not match "
                                                          f"{BASE_DIR}/SHA256.txt"]}
        why = base_layout(root, pdf, write=not dry_run)
        if why:
            return {**row, "status": "refused", "notes": [why]}
        cfg = mass_config(root)
        order, page = "education-first", f"{BASE_DIR.as_posix()}/{pdf_name}"
        if cfg["base_ab"] and base_order_for(job_id) == "experience-first":
            exp_pdf, why_not = experience_first_copy(root, pdf, not dry_run, cfg["experience_first"])
            if exp_pdf:
                order, page = "experience-first", exp_pdf
            else:
                notes.append(f"TODO: the A/B picked experience-first, education-first used: {why_not}")
    row.update(pdf=page, base=True, base_order=order)
    channel = one_click(row.get("link") or "")
    if not row.get("link"):
        blockers.append("no apply link in the job record")
    if blockers:
        return {**row, "status": "blocked", "notes": blockers + notes}
    if not dry_run:
        write_base_order(root, slug, order)
    head = (f"E2 {'profile' if route else 'base'} resume ({order}); no pack: answer from "
            "`python3 scripts/apply_pack.py answers`")
    if channel:
        return {**row, "status": "click", "notes": [f"needs the owner's click: {channel} (platform terms bar agent "
                                                    "applies); look for the same role on the employer's own career "
                                                    "page first (charter section 7)", head] + notes}
    return {**row, "status": "ready", "notes": [head] + notes}


def write_base_order(root: Path, slug: str, order: str) -> None:
    """base_order in applications/<slug>/application.md frontmatter (charter section 8); nothing else in the file changes."""
    path = root / "applications" / slug / "application.md"
    meta, body = fm.load(path)
    if meta.get("base_order") == order:
        return
    meta["base_order"] = order
    atomic_write(path, fm.dump(meta, body))


# --------------------------------------------------------------------------- the recorded yes (charter sections 6 and 7)
def os_root_of(root: Path) -> Path:
    return os_root_for(Path(root))


def load_yes(ref: str, os_root: Path, today: date) -> tuple[str, set[str], list[str], str]:
    """(approval id, the item ids it covers, the failed gates, the owner's words) of a recorded yes (approvals.py decide)."""
    import approvals
    path = os_root / "work" / "approvals" / f"{ref}.md" if approvals.ID_RE.fullmatch(ref) else Path(ref)
    if not path.is_file():
        return ref, set(), [f"no approval file at {path}"], ""
    meta, body = approvals.load(path)
    fails = [t for ok, t in approvals.gates(meta, body, today) if not ok]
    said = str(meta.get("decided_at") or "")[:10]
    if said and said != today.isoformat():               # the yes covers the day it was given on
        fails.append(f"the yes was given on {said}, not today ({today.isoformat()}): ask again")
    covered = {str(i) for i in meta.get("approved_items") or []}
    covered |= {str(i.get("id")) for i in meta.get("items") or [] if isinstance(i, dict) and i.get("id")} \
        if not covered else set()
    return str(meta.get("id") or path.stem), covered, fails, ap.one_line(meta.get("decided_quote"))


def apply_yes(rows: list[dict[str, Any]], aid: str, covered: set[str], fails: list[str]) -> None:
    """A READY row covered by the recorded yes is submitted by the agent on it (submit_on); every other READY row
    is not. Nothing here submits."""
    for r in rows:
        if r["status"] != "ready":
            continue
        if not fails and (r["slug"] in covered or (r.get("job") or "") in covered):
            r["submit_on"] = aid
        else:
            r["notes"] = list(r["notes"]) + [f"not covered by {aid}: no submit" if not fails else
                                             f"{aid} not usable ({'; '.join(fails)}): no submit"]


NO_READER_SECTION = "reader model: none (written before section 6 existed; the role-analyst adds it on the next pass)"


def reader_verdict(root: Path, slug: str, tier: str = "focused") -> tuple[str | None, str]:
    """("block", "reader model: <first error>"), ("warn", <why>) or (None, "") when the reader model is clean."""
    import reader_model
    package = root / "research" / "targets" / f"{slug}.md"
    if not package.is_file():
        if tier == "dream":                              # full research on every dream page
            return "block", f"no role package: run /role-package {slug}"
        return "warn", f"reader model: no Role Package research/targets/{slug}.md (run /role-package {slug})"
    errors = list(reader_model.check(package)["errors"])
    if not errors:
        return None, ""
    if errors[0].startswith("no `## 6 Reader model`"):
        return "warn", NO_READER_SECTION
    return "block", f"reader model: {errors[0]}"


def run_one(root: Path, slug: str, today: date, pdf: str, dry_run: bool, tier: str = "mass",
            effort: str | None = None, profile: str = "auto") -> dict[str, Any]:
    row = _run_one(root, slug, today, pdf, dry_run, tier, effort, profile)
    if tier in ("focused", "dream") and row["status"] == "ready":
        kind, why = reader_verdict(root, slug, tier)
        if kind == "block":
            row = {**row, "status": "blocked", "notes": [why]}
        elif kind == "warn":
            row = {**row, "notes": list(row["notes"]) + [why]}
    return row


def _run_one(root: Path, slug: str, today: date, pdf: str, dry_run: bool, tier: str = "mass",
             effort: str | None = None, profile: str = "auto") -> dict[str, Any]:
    row: dict[str, Any] = {"slug": slug, "company": "", "title": "", "link": "", "pdf": "", "portal": "",
                           "status": "refused", "notes": [], "base": False, "job": ""}
    if not ap.SLUG_RE.match(slug or ""):
        row["notes"] = [f"'{slug}' is not an application slug"]
        return row
    try:
        text, meta = ap.build_pack(root, slug, today, pdf)
    except ap.Refusal as exc:
        if tier == "mass" and str(exc).startswith(NOT_APPROVED):
            return base_row(root, slug, today, pdf, row, "no approved variant: " + str(exc)[len(NOT_APPROVED):],
                            effort, dry_run, profile)
        row["notes"] = [str(exc)]
        return row
    row.update(company=meta.get("company") or "", title=meta.get("title") or "", pdf=meta.get("pdf") or "",
               portal=meta.get("portal") or "", link=apply_link(root, slug), job=meta.get("job") or "")
    if meta.get("status") != "ready":
        row["status"], row["notes"] = "blocked", list(meta.get("blockers") or [])
        if not dry_run:
            atomic_write(root / "applications" / slug / ap.PACK, text)
        return row
    if dry_run:
        row["status"] = "ready"
        row["notes"] = [f"ask the owner: {item[2:]}" for item in ap.ask_items(text)]
        return row
    atomic_write(root / "applications" / slug / ap.PACK, text)
    findings = ap.lint_pack(root, slug)
    if findings:
        row["status"], row["notes"] = "lint", findings
    else:
        row["status"] = "ready"
        row["notes"] = [f"ask the owner: {item[2:]}" for item in ap.ask_items(text)]
    return row


def next_sheet(root: Path, today: date) -> Path:
    folder = root / BATCH_DIR
    n = 1
    while (folder / f"{today.isoformat()}-{n}.md").exists():
        n += 1
    return folder / f"{today.isoformat()}-{n}.md"


def render(rows: list[dict[str, Any]], today: date, tier: str, sheet_name: str,
           yes: tuple[str, list[str], str] | None = None) -> str:
    """The sheet: the record of the batch (the ready list is the record and receipt of the owner's one yes,
    never a second approval). yes = (approval id, its failed gates, the owner's words) when --approval was given."""
    counts = {"roles": len(rows), **{s: sum(r["status"] == s for r in rows) for s in STATUSES if s != "click"},
              "base_72": sum(bool(r.get("base")) and r["status"] == "ready" for r in rows)}
    meta = {"type": "apply-batch", "date": today.isoformat(), "tier": tier, "built_by": "scripts/apply_batch.py",
            "counts": counts, "slugs": [r["slug"] for r in rows]}
    clicks_n = sum(r["status"] == "click" for r in rows)
    if clicks_n:
        meta["clicks"] = clicks_n                        # CLICK rows: the owner's click (kept out of counts' schema)
    orders = {r["slug"]: r["base_order"] for r in rows if r.get("base_order")}
    if orders:
        meta["base_order"] = orders
    if yes:
        meta["approval"] = yes[0]
    c = ap.cell
    L = [f"# Apply batch {sheet_name} · {tier} tier · {counts['ready']} of {counts['roles']} ready", "",
         "The agent submits (charter section 7): a READY row marked \"submit on A-...\" is filled on the employer's site and "
         "submitted on the owner's one batch yes, recorded in that approval file with the owner's words; this sheet is the "
         "record of the batch and its receipts, never a second approval. A row with no recorded yes is not submitted. "
         "Upload the PDF named on the row and nothing else; answer from `applications/<slug>/apply-pack.md` or, for "
         "a base row, `python3 scripts/apply_pack.py answers`; then log it with the commands under \"Log each "
         "submit\". CLICK rows are the owner's click (LinkedIn Easy Apply, Indeed Apply, ZipRecruiter 1-Click).", ""]
    if yes is None:
        L += ["Submit authority: none recorded. Record the owner's yes first (`python3 scripts/kernel/approvals.py "
              "decide ...`, or `day_queue.py build --yes-quote`), then re-run with --approval.", ""]
    elif yes[1]:
        L += [f"Submit authority: {yes[0]} REFUSED ({'; '.join(yes[1])}). Nothing on this sheet is submitted.", ""]
    else:
        L += [f"Submit authority: {yes[0]}, the owner's words: \"{yes[2]}\".", ""]
    L += ["| # | slug | company | role | apply link | PDF to upload | portal | status | blockers and questions |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        state = r["status"].upper() + (f" · submit on {r['submit_on']}" if r.get("submit_on") else "")
        L.append(f"| {i} | {c(r['slug'])} | {c(r['company'])} | {c(r['title'])} | {c(r['link'])} | {c(r['pdf'])} | "
                 f"{c(r['portal'])} | {state} | {c('; '.join(r['notes'])) or '-'} |")
    L += ["", "## Log each submit", ""]
    ready = [r for r in rows if r["status"] == "ready"]
    if not ready:
        L.append("- none READY in this batch.")
    for r in ready:
        L.append(f"- {r['company']} · {r['title']}: `python3 scripts/tracker.py event {r['slug']} applied --resume "
                 f"{r['pdf'] or '<the PDF that went>'} --at "
                 "\"<submit time with timezone>\" --evidence \"<confirmation text or the owner's words>\" --via ats`")
        if r.get("submit_on"):
            L.append(f"  then the receipt: `python3 scripts/kernel/approvals.py receipt work/approvals/{r['submit_on']}.md "
                     f"--result sent --message-id \"{r['slug']}: <confirmation id or text>\" --no-channel-log` (from "
                     "the runtime root)")
    clicks = [r for r in rows if r["status"] == "click"]
    if clicks:
        L += ["", "## Your clicks", ""]
        L += [f"- {r['company']} · {r['title']}: {r['link']} (PDF {r['pdf']}; {r['notes'][0]})" for r in clicks]
    L += ["", "## Not ready", ""]
    rest = [r for r in rows if r["status"] not in ("ready", "click")]
    L += [f"- {r['slug']} ({r['status'].upper()}): " + ("; ".join(r["notes"]) or "-") for r in rest] or ["- none."]
    return fm.dump(meta, "\n".join(L) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT, help="the job-search folder (default: this department)")
    p.add_argument("--today", type=cli_date, default=date.today(), help="run date YYYY-MM-DD (default: today)")
    p.add_argument("--tier", choices=TIERS, default=None, help="mass by default; focused with --next-up (NEXT-UP rows are "
                   "focused pages, and focused runs the reader-model check)")
    p.add_argument("--pdf", choices=ap.PDF_CHOICES, default="classic")
    p.add_argument("--file", default=None, help="a file with one slug per line (# comments)")
    p.add_argument("--next-up", type=int, default=None, metavar="N",
                   help="take the top N READY rows of the generated NEXT-UP (scripts/next_up.py)")
    p.add_argument("--dry-run", action="store_true", help="build in memory (no lint); write no pack and no sheet")
    p.add_argument("--effort", choices=("E2", "E3", "E4", "E5"), default=None,
                   help="mass base rows: the effort level (default: scripts/effort.py on the job record)")
    p.add_argument("--profile", choices=("auto", "general", *pr.PROFILE_SLUGS), default="auto",
                   help="mass base rows: auto = the profile base the title's lens maps to when it is signed, "
                        "else the general base; general = force the general base; <slug> = force that profile "
                        "(still the general base when it is not usable)")
    p.add_argument("--approval", default=None, metavar="A-YYYY-NNNN",
                   help="the owner's recorded batch yes (approvals.py decide): READY rows it covers are marked submit on it")
    p.add_argument("--os-root", type=Path, default=None, help="runtime root for work/approvals (default: derived)")
    p.add_argument("slugs", nargs="*")
    args = p.parse_args(argv)
    if args.next_up is not None and args.next_up < 1:
        print("refused: --next-up takes a number of rows, 1 or more", file=sys.stderr)
        return 2
    args.tier = args.tier or ("focused" if args.next_up else "mass")
    pdf_for = dict(next_up_rows(args.root, args.next_up)) if args.next_up else {}
    args._next_up_slugs = list(pdf_for)                  # built once (about 15 s): read_slugs reuses it
    try:
        slugs = read_slugs(args)
    except FileNotFoundError as exc:                     # a missing list is said plainly, never a traceback
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if not slugs:
        print("no slugs: pass slugs, --file LIST or --next-up N (no READY row in NEXT-UP means no page passes "
              "apply_pack yet)", file=sys.stderr)
        return 2
    extra = {"effort": args.effort} if args.effort else {}
    if args.profile != "auto":
        extra["profile"] = args.profile
    rows = [run_one(args.root, s, args.today, pdf_for.get(s, args.pdf), args.dry_run, args.tier, **extra)
            for s in slugs]
    yes = None
    if args.approval:
        aid, covered, fails, quote = load_yes(args.approval, args.os_root or os_root_of(args.root), args.today)
        apply_yes(rows, aid, covered, fails)
        yes = (aid, fails, quote)
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    for r in rows:
        print(f"{r['status'].upper():8} {r['slug']}" + (f" (submit on {r['submit_on']})" if r.get("submit_on") else "")
              + (f": {'; '.join(r['notes'])}" if r["notes"] and r["status"] != "ready" else ""))
    summary = (f"{counts['ready']} ready, {counts['click']} click, {counts['blocked']} blocked, {counts['lint']} lint, "
               f"{counts['refused']} refused of {len(rows)}")
    if args.dry_run:
        print(summary + " (dry-run: no pack and no sheet written)")
    else:
        path = next_sheet(args.root, args.today)
        atomic_write(path, render(rows, args.today, args.tier, path.stem, yes))
        print(summary + f"; sheet {path.relative_to(args.root)}")
    return 0 if counts["ready"] == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
