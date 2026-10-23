#!/usr/bin/env python3
"""Redact secret-shaped strings in text files in place.

Used on session digests, transcripts and receipts before they are committed or shared. Conservative: it only touches
strings that match well-known credential shapes, and replaces them with a typed
marker so the surrounding context stays readable.

Usage:
  python3 scripts/kernel/redact.py <file-or-dir> [...]
  python3 scripts/kernel/redact.py --check <file-or-dir> [...]   # exit 1 if anything would change
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("openai-key", re.compile(r"sk-(?:proj-|live-)?[A-Za-z0-9]{20,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack-token", re.compile(r"xox[abp]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("vercel-token", re.compile(r"vercel_[A-Za-z0-9]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]{16,}")),
    ("atlassian-token", re.compile(r"ATATT3[A-Za-z0-9_=-]{20,}")),
    # KEY=value style assignments: any variable whose name contains KEY/TOKEN/SECRET/PASSWORD/ISSUER
    ("secret-assignment", re.compile(r"(?i)([A-Z0-9_]*(?:api[_-]?key|key[_-]?id|issuer|secret|token|password|passwd)[A-Z0-9_]*\s*[=:]\s*['\"]?)(?!\$\{)(?!\[REDACTED)[A-Za-z0-9._~+/=-]{8,}")),
]

TEXT_SUFFIXES = {".md", ".txt", ".jsonl", ".json", ".yaml", ".yml", ".log", ".env"}


def redact_text(text: str):
    counts = {}
    for name, rx in PATTERNS:
        def sub(m, name=name):
            counts[name] = counts.get(name, 0) + 1
            if m.lastindex:  # keep the prefix group (e.g. "Authorization: Bearer ")
                return m.group(1) + f"[REDACTED:{name}]"
            return f"[REDACTED:{name}]"
        text = rx.sub(sub, text)
    return text, counts


def iter_files(paths):
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.suffix in TEXT_SUFFIXES and ".git" not in f.parts:
                    yield f
        elif p.is_file():
            yield p


def main(argv):
    check = "--check" in argv
    paths = [a for a in argv if a != "--check"]
    if not paths:
        print(__doc__)
        return 2
    changed = 0
    total = {}
    for f in iter_files(paths):
        try:
            original = f.read_text(errors="replace")
        except Exception as e:  # noqa: BLE001
            print(f"skip {f}: {e}")
            continue
        new, counts = redact_text(original)
        if counts:
            changed += 1
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            print(f"{'WOULD REDACT' if check else 'redacted'} {f}: {counts}")
            if not check:
                f.write_text(new)
    print(f"files affected: {changed}; by type: {total or 'none'}")
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
