#!/usr/bin/env python3
"""review_sign.py: sign an editor's review.md without hand-editing its front matter.

Writes the top-level `verdict`, `round`, `variant_hash` (sha256 of V/resume.yaml now) and `signed_at` (local time with
offset) into V/review.md, keeps the previous signature as `previous_signature`, copies the signed text to
V/signed/<hash12>.yaml, then re-reads the front matter to prove it still parses. The body (the findings) is untouched.
Usage: python3 scripts/review_sign.py resumes/variants/<slug>/ --verdict approved|changes-requested --round N
Exit 0 signed, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import fm  # noqa: E402  (the department's front matter module)

VERDICTS = ("approved", "changes-requested")


def sign(vdir: Path, verdict: str, round_: int, now: datetime | None = None) -> dict:
    review, resume = vdir / "review.md", vdir / "resume.yaml"
    meta, body = fm.load(review)
    digest = hashlib.sha256(resume.read_bytes()).hexdigest()
    if meta.get("variant_hash") and meta.get("signed_at") and meta.get("variant_hash") != digest:
        meta["previous_signature"] = {"variant_hash": meta.get("variant_hash"), "signed_at": str(meta.get("signed_at")),
                                      "verdict": meta.get("verdict")}
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    meta.update({"verdict": verdict, "round": round_, "variant_hash": digest,
                 "signed_at": stamp if verdict == "approved" else ""})
    review.write_text(fm.dump(meta, body), encoding="utf-8")
    fm.load(review)                                   # still parses, or this raises
    if verdict == "approved":
        (vdir / "signed").mkdir(exist_ok=True)
        shutil.copyfile(resume, vdir / "signed" / f"{digest[:12]}.yaml")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("variant", type=Path)
    ap.add_argument("--verdict", choices=VERDICTS, required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--now", default=None,
                    help="the signing time as ISO 8601 with an offset (evals keep their task date); default: the clock")
    a = ap.parse_args(argv)
    now = None
    if a.now:
        try:
            now = datetime.fromisoformat(a.now)
        except ValueError:
            print(f"--now {a.now!r} is not an ISO 8601 time (for example 2026-01-15T10:00:00-08:00)", file=sys.stderr)
            return 2
        if now.tzinfo is None:
            print("--now needs a UTC offset (for example -07:00)", file=sys.stderr)
            return 2
    if not (a.variant / "review.md").is_file() or not (a.variant / "resume.yaml").is_file():
        print(f"need review.md and resume.yaml in {a.variant}", file=sys.stderr)
        return 2
    meta = sign(a.variant, a.verdict, a.round, now)
    print(f"signed {a.verdict} round {a.round} on {meta['variant_hash'][:12]} at {meta['signed_at'] or '(not signed)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
