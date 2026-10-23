#!/usr/bin/env python3
"""cite_sample.py - citation support sampling for Role Packages (the orchestrator design section 7).

check_dossier.py proves every [n] resolves and every quoted phrase exists in its local source. It cannot say whether the
source supports the sentence. This script samples cited sentences and prints each one next to the passage of its local
source that best matches it, so a reviewer reads both and marks it supported, partly or not.

Sampling: sentences outside ## Sources (and the other check_dossier exempt sections) that end with a citation whose
entries name at least one local file (not a .py script). A sentence citing only URLs is never sampled: there is
nothing local to quote. The draw is repeatable (seeded by the file name; --seed changes it) and prefers sentences that
cite different sources. The passage is the window of up to three source lines sharing the most content words with the
sentence (a word with a digit, an id or a number, counts three times); a pair under 40% is marked LOW: read it first.

Read only, no network. Exit 0, 1 when a package has no checkable sentence (missing is not empty), 2 on a usage error.

CLI:  python3 scripts/cite_sample.py research/targets/<slug>.md [...] [--n 3] [--seed S] [--root JS]
"""
from __future__ import annotations

import argparse
import hashlib
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_dossier as cd  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

LOW = 0.4
WINDOW = 3
STOP = set("""a an the and or of to in on for with by at from as is are was were be been it its this that these those
we you our your their they he she his her i not no but so than then there here into over under about after before
which who what when where how all any each every per via also more most only same such can will would should could
has have had do does did one two""".split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9.%$-]*", text.lower().replace("’", "'"))
            if w.strip(".") not in STOP and len(w.strip(".")) > 1]


def _entries(items: list[Any], root: Path) -> dict[int, Any]:
    out = {}
    for it in items:
        if it.section.lower() != "sources":
            continue
        m = cd.ENTRY_RE.match(it.lines[0])
        if m:
            out[int(m.group(1) or m.group(2))] = cd._entry(int(m.group(1) or m.group(2)), it, root)
    return out


def candidates(path: Path, root: Path) -> list[dict[str, Any]]:
    """Every cited sentence with at least one local source file, in document order."""
    text = Path(path).read_text(encoding="utf-8")
    items = cd.parse_items(text)
    entries = _entries(items, root)
    out = []
    for it in items:
        if it.section.lower() in cd.EXEMPT_SECTIONS or it.text.lstrip().startswith("|"):
            continue
        for sent in cd.sentences(" ".join(it.text.split())):
            if not cd.CITE_END_RE.search(sent):
                continue
            nums = [int(n) for g in cd.CITE_RE.findall(sent) for n in re.split(r"\s*[,;]\s*", g)]
            files = [f for n in nums if n in entries for f in entries[n].files]
            if files:
                out.append({"line": it.line, "sentence": sent, "cites": nums, "files": list(dict.fromkeys(files))})
    return out


def best_passage(sentence: str, files: list[Path], root: Path) -> dict[str, Any]:
    want = set(_words(cd.CITE_RE.sub(" ", sentence)))
    weight = {w: (3 if re.search(r"\d", w) else 1) for w in want}     # ids and numbers say which row it is
    total = sum(weight.values())
    best = {"source": "", "source_line": 0, "passage": "", "share": 0.0}
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i in range(len(lines)):
            if not lines[i].strip():
                continue
            win = " ".join(x.strip() for x in lines[i:i + WINDOW] if x.strip())
            share = sum(weight[w] for w in want & set(_words(win))) / total if total else 0.0
            if share > best["share"]:
                try:
                    rel = f.resolve().relative_to(Path(root).resolve()).as_posix()
                except ValueError:
                    rel = str(f)
                best = {"source": rel, "source_line": i + 1, "passage": win[:400], "share": round(share, 2)}
    return best


def sample(path: Path, root: Path, n: int = 3, seed: int | None = None) -> list[dict[str, Any]]:
    cands = candidates(path, root)
    rng = random.Random(seed if seed is not None else int(hashlib.sha256(Path(path).name.encode()).hexdigest()[:8], 16))
    rng.shuffle(cands)
    picked, used = [], set()
    for c in cands:                                   # first pass: one sentence per source file
        if len(picked) < n and not (set(c["files"]) & used):
            picked.append(c)
            used |= set(c["files"])
    for c in cands:
        if len(picked) < n and c not in picked:
            picked.append(c)
    picked.sort(key=lambda c: c["line"])
    out = []
    for c in picked:
        b = best_passage(c["sentence"], c["files"], root)
        out.append({"line": c["line"], "sentence": c["sentence"], "cites": c["cites"], **b, "low": b["share"] < LOW})
    return out


def render(shown: str, pairs: list[dict[str, Any]]) -> str:
    L = [f"# Citation sample · {shown} · {len(pairs)} pair(s)"]
    for i, p in enumerate(pairs, 1):
        flag = "  LOW: read this one first" if p["low"] else ""
        L += ["", f"{i}. CLAIM {shown}:{p['line']}: {p['sentence']}",
              f"   SOURCE {p['source']}:{p['source_line']} (shares {int(p['share'] * 100)}% of the claim's words){flag}: "
              f"{p['passage'] or '(no matching line)'}",
              "   supported? yes / partly / no"]
    return "\n".join(L).replace("\u2014", "-") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    args = ap.parse_args(argv)
    rc = 0
    for f in args.files:
        if not f.is_file():
            print(f"no such file: {f}", file=sys.stderr)
            return 2
        shown = f.resolve().relative_to(args.root.resolve()).as_posix() if f.resolve().is_relative_to(args.root.resolve()) else str(f)
        pairs = sample(f, args.root, args.n, args.seed)
        if not pairs:
            print(f"{shown}: no cited sentence with a local source to sample (missing is not empty)")
            rc = 1
            continue
        print(render(shown, pairs), end="")
    return rc


if __name__ == "__main__":
    sys.exit(main())
