"""cite_sample.py (SETUP-2 design section 7): citation support sampling. For a Role Package, three cited sentences whose
sources are local files are printed next to the passage of the source that best matches them, for a reviewer to mark
supported or not. check_dossier proves a citation resolves; this shows whether the source says it."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import cite_sample as cs  # noqa: E402

PKG = """---
title: pkg
---
# Quillon · Agent Evaluation Engineer

## 2 The team's problem
The team builds evaluation environments from real user prompts [1]. Releases are gated on agent quality [1].

## 4 Company signal
Quillon raised a Series A in August 2026 [2]. The team has twelve engineers [3]. Quillon sells to banks [4].

## Sources

1. applications/quillon/jd.md : the JD, read 2026-10-01.
2. research/cache/quillon-news.md : launch post, read 2026-10-01.
3. research/cache/quillon-team.md : team page, read 2026-10-01.
4. https://quillon.example.com/customers : customers page, accessed 2026-10-01.
"""
JD = ("# JD\n\nAbout the role\n\nYou will build evaluation environments from real user prompts and grade agent runs.\n\n"
      "We gate every release on agent quality measured on those prompts.\n")
NEWS = "# News\n\nQuillon announced a Series A round in August 2026, led by a fictional fund.\n"
TEAM = "# Team\n\nOur team is small and growing; we are hiring across research and product.\n"


def world(root):
    (root / "applications" / "quillon").mkdir(parents=True)
    (root / "applications" / "quillon" / "jd.md").write_text(JD)
    (root / "research" / "cache").mkdir(parents=True)
    (root / "research" / "cache" / "quillon-news.md").write_text(NEWS)
    (root / "research" / "cache" / "quillon-team.md").write_text(TEAM)
    (root / "research" / "targets").mkdir(parents=True)
    p = root / "research" / "targets" / "quillon-agent-evaluation-engineer-202610.md"
    p.write_text(PKG)
    return p


def test_three_local_pairs_never_a_url_only_sentence(root):
    pkg = world(root)
    pairs = cs.sample(pkg, root, n=3)
    assert len(pairs) == 3
    assert all("banks" not in p["sentence"] for p in pairs)            # [4] is URL-only: nothing local to quote
    for p in pairs:
        assert p["source"].startswith(("applications/", "research/cache/")) and p["passage"]
        assert p["line"] > 0 and p["source_line"] > 0


def test_best_passage_is_the_matching_lines_and_low_overlap_is_flagged(root):
    pkg = world(root)
    pairs = {p["sentence"]: p for p in cs.sample(pkg, root, n=10)}
    series = next(v for k, v in pairs.items() if "Series A" in k)
    assert "Series A round in August 2026" in series["passage"] and not series["low"]
    twelve = next(v for k, v in pairs.items() if "twelve engineers" in k)
    assert twelve["low"]                                                 # the team page never says twelve
    assert len(pairs) == 4                                               # five cited sentences, one URL-only


def test_sampling_is_repeatable_and_seedable(root):
    pkg = world(root)
    a = [p["sentence"] for p in cs.sample(pkg, root, n=3)]
    assert a == [p["sentence"] for p in cs.sample(pkg, root, n=3)]
    seeds = {tuple(p["sentence"] for p in cs.sample(pkg, root, n=2, seed=s)) for s in range(12)}
    assert len(seeds) > 1


def test_cli_prints_pairs_reads_only_and_exits_1_with_nothing_checkable(root, capsys):
    pkg = world(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert cs.main(["--root", str(root), str(pkg)]) == 0
    out = capsys.readouterr().out
    assert out.count("CLAIM ") == 3 and out.count("SOURCE ") == 3 and "supported? yes / partly / no" in out
    assert "\u2014" not in out
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    bare = root / "research" / "targets" / "bare.md"
    bare.write_text("# Bare\n\n## 4 Company signal\nNo citations here.\n")
    assert cs.main(["--root", str(root), str(bare)]) == 1
