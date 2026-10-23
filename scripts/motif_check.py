#!/usr/bin/env python3
"""motif_check.py: repeated phrasing on a page (editorial-review step 3): n-grams and hyphenated compounds used 3 or
more times, repeated bullet openers and runs of three bullets with one opener.

Moved from the inline snippet in .agents/skills/editorial-review/references/motif-check.md (trust test 2026-10-01,
F66: the snippet sat in indented fences and could not be extracted). Same logic.
Usage: python3 scripts/motif_check.py V/resume.yaml
"""
from __future__ import annotations

import collections
import re
import sys
from typing import Any

import yaml

STOP = set("a an the and or of to in on for with by from as at into over across each per that this their its is was were "
           "be been are so could".split())


def collect(cv: dict[str, Any]) -> tuple[list[str], list[str]]:
    texts, bullets = [], []

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k != "evidence":
                    walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            t = re.sub(r"<!--.*?-->|https?://\S+|\S+@\S+|\b[\w.-]+\.(?:com|io|ai|app|dev|org)\S*", "", x).strip()
            texts.append(t)
            if key == "highlights":
                bullets.append(t)
    walk(cv)
    return texts, bullets


def report(cv: dict[str, Any]) -> dict[str, str]:
    texts, bullets = collect(cv)
    grams, compounds = collections.Counter(), collections.Counter()
    for t in texts:
        words = [w for w in re.findall(r"[a-z][a-z0-9+#.-]*", t.lower()) if w not in STOP]
        compounds.update(w for w in words if "-" in w.strip("-"))
        for n in (2, 3):
            grams.update(" ".join(words[i:i + n]) for i in range(len(words) - n + 1))
    rep = [f"{c}x {g}" for g, c in grams.most_common() if c >= 3]
    rep += [f"{c}x {g} (compound)" for g, c in compounds.most_common() if c >= 3]
    first = collections.Counter(b.split()[0].lower() for b in bullets if b)
    runs = [bullets[i].split()[0] for i in range(len(bullets) - 2)
            if bullets[i] and bullets[i + 1] and bullets[i + 2]
            and bullets[i].split()[0] == bullets[i + 1].split()[0] == bullets[i + 2].split()[0]]
    return {"MOTIFS": "; ".join(rep) if rep else "none over 2",
            "OPENERS": "; ".join(f"{c}x {w}" for w, c in first.most_common() if c > 2) or "no opener over 2",
            "RUNS OF 3": ", ".join(runs) if runs else "none"}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: motif_check.py V/resume.yaml", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as fh:
        cv = (yaml.safe_load(fh) or {}).get("cv", {})
    for k, v in report(cv).items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
