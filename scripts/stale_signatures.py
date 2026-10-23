#!/usr/bin/env python3
"""stale_signatures.py: every page whose sign-off no longer matches the page, with a re-sign or retire call.

A signature is stale when a hash in review.md (variant_hash; classic, inline or plain pdf_sha256), recruiter.md
(resume_sha256, pdf_sha256) or screener.md (resume_sha256) matches no file of that kind in the variant folder now:
the page changed after it was signed. A page the editor approved with no recruiter APPROVE is listed too,
as `no recruiter APPROVE`. The call per row, from the application that uses the page
(applications/*/application.md `resume_variant`, else the application slug the folder name starts with):
  re-sign  the application is before `applied` or on hold: the page can still go out, so its readers sign it again
  retire   the page went out (applied or later) or no application uses it: the signature stays as history
Usage: python3 scripts/stale_signatures.py [--root JS] [--json]
Exit 0 nothing listed, 1 rows listed, 2 usage error. Reads only; no network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import fm  # noqa: E402  (the department's front matter module)

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
RESIGN_STAGES = ("discovered", "fit_checked", "tailored", "on_hold")
HASH = re.compile(r"(?:sha256:)?([0-9a-f]{64})")
# (file, field, which files it signs): yaml = resume.yaml; classic = PDFs in the folder; inline = PDFs in inline/
FIELDS = (("review.md", "variant_hash", "yaml"), ("review.md", "classic_pdf_sha256", "classic"),
          ("review.md", "pdf_classic_sha256", "classic"), ("review.md", "inline_pdf_sha256", "inline"),
          ("review.md", "pdf_inline_sha256", "inline"), ("review.md", "pdf_sha256", "pdf"),
          ("recruiter.md", "resume_sha256", "yaml"), ("recruiter.md", "pdf_sha256", "pdf"),
          ("screener.md", "resume_sha256", "yaml"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _meta(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        meta, _ = fm.load(path)
    except fm.FrontmatterError:
        return {"_unparsable": True}
    return meta if isinstance(meta, dict) else {"_unparsable": True}


def applications(root: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """(variant folder name -> (application id, stage) from `resume_variant`, application id -> stage)."""
    by_variant: dict[str, tuple[str, str]] = {}
    stages: dict[str, str] = {}
    for app in sorted((root / "applications").glob("*/application.md")):
        meta = _meta(app) or {}
        aid, stage = str(meta.get("id") or app.parent.name), str(meta.get("stage") or "")
        stages[aid] = stage
        v = str(meta.get("resume_variant") or "").rstrip("/")
        if v:
            by_variant[Path(v).name] = (aid, stage)
    return by_variant, stages


def application_of(variant: str, by_variant: dict[str, tuple[str, str]], stages: dict[str, str]) -> tuple[str, str]:
    """The application that names this page, else the longest application id the folder name starts with
    (variants are named <application slug>-b<base>)."""
    if variant in by_variant:
        return by_variant[variant]
    ids = [a for a in stages if variant == a or variant.startswith(a + "-")]
    if not ids:
        return "", ""
    aid = max(ids, key=len)
    return aid, stages[aid]


def stale(vdir: Path) -> list[str]:
    """The stale signatures of one variant folder, as 'file field' strings."""
    have = {"yaml": {_sha(vdir / "resume.yaml")},
            "classic": {_sha(p) for p in vdir.glob("*.pdf")},
            "inline": {_sha(p) for p in (vdir / "inline").glob("*.pdf")} if (vdir / "inline").is_dir() else set()}
    have["pdf"] = have["classic"] | have["inline"]
    out, metas = [], {}
    for name, field, kind in FIELDS:
        meta = metas.setdefault(name, _meta(vdir / name))
        if not meta:
            continue
        if meta.get("_unparsable"):
            if f"{name} unparsable" not in out:
                out.append(f"{name} unparsable")
            continue
        m = HASH.fullmatch(str(meta.get(field) or "").strip())
        if m and have[kind] and m.group(1) not in have[kind]:
            out.append(f"{name} {field}")
    return out


def no_recruiter_approve(vdir: Path) -> bool:
    review = _meta(vdir / "review.md") or {}
    if str(review.get("verdict") or "").strip().lower() != "approved":
        return False
    rec = _meta(vdir / "recruiter.md")
    if not rec or rec.get("_unparsable"):
        return True
    verdict = str(rec.get("verdict") or "").strip()
    return not (re.fullmatch(r"APPROVED?\.?", verdict, re.I) or
                (verdict.upper().startswith("GAP") and str(rec.get("owner_decision") or "").strip()))


def report(root: Path) -> list[dict[str, Any]]:
    by_variant, stages = applications(root)
    rows = []
    for vdir in sorted((root / "resumes" / "variants").glob("*/")):
        if not (vdir / "resume.yaml").is_file() or not (vdir / "review.md").is_file():
            continue
        what = stale(vdir) + (["no recruiter APPROVE"] if no_recruiter_approve(vdir) else [])
        if not what:
            continue
        app, stage = application_of(vdir.name, by_variant, stages)
        call = "re-sign" if stage in RESIGN_STAGES else "retire"
        why = f"stage {stage}" if app else "no application uses this page"
        rows.append({"variant": vdir.name, "application": app, "stage": stage, "stale": what, "call": call,
                     "why": why})
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    n = sum(1 for r in rows if any(s != "no recruiter APPROVE" for s in r["stale"]))
    out = [f"stale signatures: {len(rows)} page(s); {n} changed after signing, "
           f"{len(rows) - n} with only no recruiter APPROVE", "",
           "| variant | application | stage | stale | call | why |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['variant']} | {r['application'] or '-'} | {r['stage'] or '-'} | {'; '.join(r['stale'])} | "
                   f"{r['call']} | {r['why']} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT, help="the job-search folder (default: this script's)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (args.root / "resumes" / "variants").is_dir():
        print(f"usage error: no resumes/variants/ under {args.root}", file=sys.stderr)
        return 2
    rows = report(args.root)
    print(json.dumps(rows, indent=2) if args.json else render(rows))
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
