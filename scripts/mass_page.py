#!/usr/bin/env python3
"""mass_page.py - the mass-tier page for one role, end to end, with no model call (resume-for --tier mass, step 4m).

Why: a mass page is the signed profile
base for the role's title, else the signed general base (or its signed
Experience-first copy), with the Skills rows reordered for the JD and at most three "missing but evidenced"
terms added, and every gate run. Every part of that is mechanical, and a model agent doing it takes dozens of tool
calls and a large context. This script does the same steps deterministically, in about a minute, and writes NOTES.md
the way the tailor does:

  1. source page: the title's profile base (resumes/bases/profiles/<slug>/resume.yaml) when that profile is
     usable (resume.yaml signed, PDF present, layout PASS: apply_batch.profile_page), never the general base while a
     profile fits. Otherwise the general base, in the order of the A/B of targets/mass.yaml
     (apply_batch.base_order_for on the job id; the Experience-first copy only while
     apply_batch.experience_first_copy says it is valid), else the Education-first base;
  2. lens: the profile/lenses file that shares the most distinctive words with the JD (recorded in the sidecar);
  3. Skills rows: sorted by how many of the JD's taxonomy skills (skills-taxonomy.yaml names and aliases) and
     distinctive JD words each row carries; ties keep the base order; words, ev- ids and rows unchanged;
  4. terms: up to three of ats_check's missing_evidenced terms, each appended to the row whose ev- ids include the
     term's evidence (else not added), one at a time, kept only if the page still renders to one page;
  5. gates: render (classic and inline, lint inside), ats_check on both PDFs with the JD, check_holds, the matrix
     skeleton (jd_matrix build) and rubric_check --tier mass, base_drift, page_layout_check on both PDFs.

Then the one step a script cannot do: the lane looks at page-1.png once.

Exit 0: every gate passed, the page ships on the gates (mass). Exit 1: a gate failed: the role is promoted to focused
(resume-for's rule), NOTES says which gate. It never edits the base, never adds a bullet, never writes a claim.

Usage:
  python3 scripts/mass_page.py <slug> [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from agency_paths import DATA_ROOT as JS  # noqa: E402
from agency_paths import SCRIPTS_DIR  # noqa: E402
sys.path.insert(0, str(SCRIPTS_DIR))
import apply_batch as ab  # noqa: E402
import evidence_slice as es  # noqa: E402

PY = sys.executable
MAX_TERMS = 3
SIDE = re.compile(r"^(sidecar:\n(?:[ \t]+.*\n)*)", re.M)


def run(*args: str, cwd: Path = JS) -> tuple[int, str]:
    r = subprocess.run([PY, *args], cwd=cwd, capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip()


def job_id(slug: str) -> str:
    app = JS / "applications" / slug / "application.md"
    if app.is_file():
        m = re.search(r"(?m)^job:\s*'?\[\[([^\]|]+)", app.read_text(errors="replace"))
        if m:
            return m.group(1).split("/")[-1]
    return slug


def source_page(job: str, title: str = "", profile: str = "auto") -> tuple[Path, str, str]:
    """(folder holding resume.yaml and the two templates, order, why).: the title's usable profile base first;
    the general base (with the A/B order) only when no profile fits or the profile is not usable."""
    route, note = ab.profile_page(JS, title, "classic", profile)
    if route is not None:
        return JS / Path(route.resume_yaml).parent, f"profile-{route.profile}", note[0]
    base_why = note[0]
    folder, order, why = _general_source(job)
    return folder, order, f"{why}; {base_why}"


def _general_source(job: str) -> tuple[Path, str, str]:
    cfg = ab.mass_config(JS)
    base = JS / ab.BASE_DIR
    if cfg["base_ab"] and ab.base_order_for(job) == "experience-first":
        pdf, why = ab.experience_first_copy(JS, "classic", False, cfg["experience_first"])
        if pdf:
            return JS / cfg["experience_first"], "experience-first", f"base_order_for({job}) = experience-first; copy valid"
        return base, "education-first", f"experience-first by A/B but the copy is not valid ({why}); fell back"
    return base, "education-first", ("A/B off" if not cfg["base_ab"] else f"base_order_for({job}) = education-first")


def jd_title(jd: str) -> str:
    m = re.search(r"(?m)^title:\s*['\"]?(.+?)['\"]?\s*$", jd) or re.search(r"(?m)^#\s+(.+)$", jd)
    return m.group(1) if m else ""


def pick_lens(jd: str) -> str:
    """The lens whose name the title carries wins (applied AI engineer -> ai-engineer); then shared distinctive words."""
    lenses = {p.stem: set(es.words(p.read_text(errors="replace"))) for p in (JS / "profile/lenses").glob("*.md")
              if p.stem != "README"}
    df = Counter(w for ws in lenses.values() for w in ws)
    jdw = Counter(es.words(jd))
    n = max(len(lenses), 1)
    title = set(es.words(jd_title(jd)) + re.findall(r"[a-z]{2,}", jd_title(jd).lower()))
    score = {k: sum(math.log((n + 1) / df[w]) * min(jdw[w], 3) for w in ws if w in jdw)
             + 100 * (sum(x in title for x in k.split("-")) / len(k.split("-")) == 1) for k, ws in lenses.items()}
    return max(score, key=score.get) if score else "ai-engineer"


def skills_block(text: str) -> tuple[int, int, list[str]]:
    """(start, end, rows) of the `Skills:` section's row chunks, as text, so formatting and comments survive."""
    m = re.search(r"(?m)^(\s+)Skills:\n", text)
    if not m:
        raise SystemExit("no Skills: section in resume.yaml")
    indent = m.group(1)
    start = m.end()
    nxt = re.search(rf"(?m)^{indent}\S[^\n]*:\n", text[start:])
    end = start + (nxt.start() if nxt else len(text) - start)
    body = text[start:end]
    rows = re.split(rf"(?m)^(?={indent}- label:)", body)
    return start, end, [r for r in rows if r.strip()]


def row_score(row: str, names: list[str], jdw: Counter) -> float:
    low = row.lower()
    hits = sum(3 for n in names if re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", low))
    return hits + sum(min(jdw[w], 2) * 0.25 for w in set(es.words(re.sub(r"<!--.*?-->", "", row))) if w in jdw)


def reorder(text: str, jd: str) -> tuple[str, list[tuple[str, float]]]:
    start, end, rows = skills_block(text)
    names = [n for r, _ in es.matched_skills(jd, es.taxonomy()) for n in es.skill_names(r)]
    jdw = Counter(es.words(jd))
    scored = [(row_score(r, names, jdw), i, r) for i, r in enumerate(rows)]
    new = [r for _, _, r in sorted(scored, key=lambda x: (-x[0], x[1]))]
    label = lambda r: re.search(r"label:\s*(.*)", r).group(1).strip()  # noqa: E731
    report = [(label(r), s) for s, _, r in sorted(scored, key=lambda x: (-x[0], x[1]))]
    return text[:start] + "".join(new) + text[end:], report


def add_term(text: str, term: str, evidence: list[str]) -> str | None:
    """Append `term` to the Skills row whose ev- ids include one of `evidence`; None when no row backs it."""
    start, end, rows = skills_block(text)
    for i, r in enumerate(rows):
        ids = set(re.findall(r"ev-[a-z0-9-]+", r))
        if ids & set(evidence) and term.lower() not in r.lower():
            rows[i] = re.sub(r"(details:\s*)(.*?)(\s*<!--)", lambda m: f"{m.group(1)}{m.group(2)}, {term}{m.group(3)}", r, count=1)
            return text[:start] + "".join(rows) + text[end:]
    return None


def render_both(v: Path) -> tuple[bool, dict[str, str]]:
    out = {}
    ok = True
    for tpl, dest in (("classic", v), ("inline", v / "inline")):
        dest.mkdir(parents=True, exist_ok=True)
        code, log = run("scripts/render_resume.py", "render", "--input", str(v / "resume.yaml"), "--out", str(dest),
                        "--evidence", "profile/evidence.yaml", "--template", tpl, "--density", "one-page",
                        "--max-pages", "1", "--basename", Path(ab.BASE_PDF[tpl]).stem)
        out[tpl] = f"exit {code}: {log.splitlines()[-1] if log else ''}"
        ok = ok and code == 0
    return ok, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--force", action="store_true", help="overwrite an existing variant folder")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    a = ap.parse_args(argv)
    jd_path = JS / "applications" / a.slug / "jd.md"
    if not jd_path.is_file():
        print(f"no {jd_path.relative_to(JS)}: a mass page needs the JD", file=sys.stderr)
        return 2
    jd = jd_path.read_text(errors="replace")
    job = job_id(a.slug)
    src, order, why = source_page(job, jd_title(jd))
    lens = pick_lens(jd)
    text = (src / "resume.yaml").read_text(encoding="utf-8")
    text = SIDE.sub(f"sidecar:\n  lens: {lens}\n  base_of: general\n  job: jobs/{job}.md\n", text, count=1)
    text, report = reorder(text, jd)
    if a.dry_run:
        print(f"order {order} ({why}); lens {lens}")
        for label, s in report:
            print(f"  {s:6.2f}  {label}")
        return 0
    v = JS / "resumes" / "variants" / a.slug
    if (v / "resume.yaml").exists() and not a.force:
        print(f"{v.relative_to(JS)} exists; --force to overwrite (never over a page a model or the owner worked on)",
              file=sys.stderr)
        return 2
    v.mkdir(parents=True, exist_ok=True)
    for t in ("template-classic.yaml", "template-inline.yaml"):
        if (src / t).is_file():
            shutil.copy2(src / t, v / t)
    (v / "resume.yaml").write_text(text, encoding="utf-8")
    ok, rend = render_both(v)
    pdf = v / ab.BASE_PDF["classic"]
    terms_log = []
    if ok:
        run("scripts/ats_check.py", "check", "--pdf", str(pdf), "--jd", str(jd_path), "--source", str(v / "resume.yaml"))
        import json
        rep = json.loads((v / "ats-report.json").read_text())
        for t in (rep.get("keywords") or {}).get("missing_evidenced", [])[:MAX_TERMS]:
            cand = add_term(text, t["term"], t.get("evidence") or [])
            if cand is None:
                terms_log.append(f"`{t['term']}`: not added (no Skills row carries its evidence {t.get('evidence')})")
                continue
            (v / "resume.yaml").write_text(cand, encoding="utf-8")
            good, _ = render_both(v)
            if good:
                text = cand
                terms_log.append(f"`{t['term']}`: added on the row carrying {t.get('evidence')}")
            else:
                (v / "resume.yaml").write_text(text, encoding="utf-8")
                terms_log.append(f"`{t['term']}`: not added (the page went past one page)")
        ok, rend = render_both(v)
    gates: list[tuple[str, int, str]] = [("render classic and inline (lint inside)", 0 if ok else 1,
                                          "; ".join(f"{k} {s}" for k, s in rend.items()))]
    if ok:
        ipdf = v / "inline" / ab.BASE_PDF["inline"]
        if not ipdf.is_file():
            ipdf = next(iter(sorted((v / "inline").glob("*.pdf"))), ipdf)
        for name, p, out in (("ats_check classic", pdf, v), ("ats_check inline", ipdf, v / "inline")):
            code, log = run("scripts/ats_check.py", "check", "--pdf", str(p), "--jd", str(jd_path), "--source",
                            str(v / "resume.yaml"), "--out", str(out))
            gates.append((name, code, log.splitlines()[-1] if log else ""))
        import ats_check
        for p, out in ((pdf, v), (ipdf, v / "inline")):   # the PDF text the holds and rubric gates read
            (out / "resume-text.txt").write_text(ats_check.extract_pdftotext(p).text or "", encoding="utf-8")
        texts = [str(x) for x in (v / "resume.yaml", v / "resume-text.txt", v / "inline" / "resume-text.txt") if x.is_file()]
        code, log = run("scripts/check_holds.py", *texts)
        gates.append(("check_holds", code, log.splitlines()[-1] if log else ""))
        matrix = JS / "research" / "targets" / f"{a.slug}.matrix.yaml"
        if not matrix.is_file():
            run("scripts/jd_matrix.py", "build", "--jd", str(jd_path), "--out", str(matrix))
        base_yaml = (src if order.startswith("profile-") else JS / ab.BASE_DIR) / "resume.yaml"   #: its own base
        code, log = run("scripts/rubric_check.py", "--resume", str(v / "resume.yaml"), "--base",
                        str(base_yaml), "--matrix", str(matrix), "--jd", str(jd_path),
                        "--pdf-text", str(v / "resume-text.txt"), "--tier", "mass")
        (v / "rubric-report.txt").write_text(log + "\n", encoding="utf-8")
        gates.append(("rubric_check --tier mass", code, "rubric-report.txt"))
        code, log = run("scripts/base_drift.py", str(v))
        gates.append(("base_drift", code, log.splitlines()[-1] if log else ""))
        for name, p in (("page_layout_check classic", pdf), ("page_layout_check inline", ipdf)):
            code, log = run("scripts/page_layout_check.py", str(p))
            gates.append((name, code, log.splitlines()[-1] if log else ""))
        if shutil.which("pdftoppm"):   # charter section 8 /: the rendered page is looked at, once, by the lane
            subprocess.run(["pdftoppm", "-png", "-r", "80", "-singlefile", str(pdf), str(v / "page-1")],
                           capture_output=True, timeout=60)
    passed = all(c == 0 for _, c, _ in gates)
    _, versions = run("scripts/gate_versions.py")
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    notes = [
        "---", "type: variant-notes", f"slug: {a.slug}", "base_of: general", "tier: mass",
        f"written: {now} by scripts/mass_page.py (resume-for --tier mass, no model call)", "---", f"# {a.slug}", "",
        f"**State: {'every gate exit 0. Ships on the gates (mass).' if passed else 'a gate failed: promote to focused (resume-for).'}**",
        "", "## Order used", f"{order}: {why}. Source page: `{src.relative_to(JS)}`.", "",
        "## Plan", f"Lens `{lens}` (profile/lenses, most distinctive shared words with the JD). Base words unchanged; no "
        "bullet changed, added or dropped. Skills rows by JD score (taxonomy skills the JD names x3, shared words x0.25):",
        *[f"{i}. {label} ({s:.2f})" for i, (label, s) in enumerate(report, 1)], "",
        "Terms (ats_check missing_evidenced, at most 3):", *([f"- {t}" for t in terms_log] or ["- none listed"]), "",
        "## Gates", versions.splitlines()[0] if versions else "", "", "| Gate | Exit | Result |", "|---|---|---|",
        *[f"| {n} | {c} | {r.replace(str(JS) + '/', '').replace('|', '/')[:160]} |" for n, c, r in gates], "",
        "## Look (charter section 8)", "`page-1.png`: the lane opens it once (one Read) before the batch card: one "
        "page, full to the bottom margin, no Skills row wrapping. Record `looked: ok` or what is wrong here.", ""]
    (v / "NOTES.md").write_text("\n".join(notes), encoding="utf-8")
    print(f"{v.relative_to(JS)}: {'PASS' if passed else 'FAIL'} ({sum(c == 0 for _, c, _ in gates)}/{len(gates)} gates)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
