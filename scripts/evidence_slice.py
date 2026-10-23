#!/usr/bin/env python3
"""evidence_slice.py - the part of the owner's record one role needs, so writers and judges stop reading all of it.

Why: the tailor, editor, comparer and readers would each read the whole of PROFILE.md, evidence.yaml and corpus/**
(hundreds of KB, around 200k tokens per agent), and they re-send it on every tool call. A role needs a few dozen
evidence entries. This script picks them deterministically from the JD:

  1. skills: every skills-taxonomy.yaml entry whose skill name or alias appears in the JD (with level, evidence ids,
     notes; `confirm: true` and internal-only entries are marked, never hidden);
  2. evidence: the entries those skills cite, then the entries whose note shares the most distinctive JD words
     (idf-weighted), up to the cap; each with its note and discretion;
  3. profile: the PROFILE.md paragraphs that cite the chosen ids (the final wording the page draws from);
  4. sources: where each chosen id's proof lives (path and locator), to open only when a claim needs it;
  5. per requirement (with --slug and its research/targets/<slug>.matrix.yaml): the WHOLE record searched for each
     R, Q and P row: the best
     evidence entries and corpus files for that row's words, so a strong true line outside the skills list is found
     by search instead of by reading 800 KB.

The truth check stays exact: every line on a page still carries ids, and a judge reads those ids' records (here, or
by `--ids`). Nothing is summarised or reworded: every line printed is copied from the record. Read-only.

Usage:
  python3 scripts/evidence_slice.py --slug <slug>                 # JD from applications/<slug>/jd.md or the package
  python3 scripts/evidence_slice.py --jd path/to/jd.md [--cap 15000] [--out resumes/variants/<slug>/slice.md]
  python3 scripts/evidence_slice.py --ids ev-001 ev-002   # the records behind cited ids (a judge's read)
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from agency_paths import DATA_ROOT as JS  # noqa: E402
PROFILE = JS / "profile"
CAP = 18_000
ROW_TYPES = ("R", "Q", "P")
STOP = set("""a about above across after again against all also an and any are as at be because been being both but by
can could did do does doing during each either etc few for from further had has have having he her here his how i if
in into is it its itself just may me more most must my no nor not of off on once only or other our out over own per
same she should so some such than that the their them then there these they this those through to too under until up
very was we were what when where which while who whom why will with within would you your yours role team work working
experience years year strong ability skills skill including include etc using use used build building help across
plus preferred required requirements qualifications responsibilities job company new our we're you'll candidate""".split())
WORD = re.compile(r"[a-z][a-z0-9+#.-]{2,}")
EV = re.compile(r"\bev-[a-z0-9-]+\b")


def words(text: str) -> list[str]:
    return [w.strip(".-") for w in WORD.findall(text.lower()) if w.strip(".-") not in STOP and len(w.strip(".-")) > 2]


def jd_text(slug: str | None, jd: Path | None) -> str:
    if jd:
        return jd.read_text(errors="replace")
    for p in (JS / "applications" / slug / "jd.md", JS / "research" / "targets" / f"{slug}.md"):
        if p.is_file():
            return p.read_text(errors="replace")
    raise SystemExit(f"no JD for {slug}: pass --jd")


def taxonomy() -> list[dict]:
    data = yaml.safe_load((PROFILE / "skills-taxonomy.yaml").read_text()) or {}
    out = []
    for group, rows in (data.get("groups") or {}).items():
        for r in rows or []:
            if isinstance(r, dict) and r.get("skill"):
                out.append({**r, "group": group})
    return out


def skill_names(r: dict) -> list[str]:
    """The skill's own names: the part before a parenthesis, each side of a slash, the parenthesised items, aliases."""
    s = str(r["skill"])
    names = [re.sub(r"\s*\(.*?\)", "", s).strip()]
    names += [p.strip() for p in re.split(r"\s*/\s*", names[0]) if p.strip()]
    for inner in re.findall(r"\((.*?)\)", s):
        names += [p.strip() for p in re.split(r",\s*|\s+and\s+", inner) if len(p.strip()) > 2]
    names += [str(a) for a in r.get("aliases") or []]
    return [n for n in dict.fromkeys(names) if len(n) >= 2]


def matched_skills(jd: str, rows: list[dict]) -> list[tuple[dict, list[str]]]:
    low = jd.lower()
    out = []
    for r in rows:
        hits = [n for n in skill_names(r) if re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", low)]
        if hits:
            out.append((r, hits))
    return out


def evidence() -> dict[str, dict]:
    return (yaml.safe_load((PROFILE / "evidence.yaml").read_text()) or {}).get("evidence") or {}


def profile_paragraphs() -> list[str]:
    text = (PROFILE / "PROFILE.md").read_text(errors="replace")
    return [p.strip() for p in re.split(r"\n\s*\n|\n(?=- |\* |#)", text) if EV.search(p)]


def rank_evidence(jd: str, ev: dict[str, dict], seed: list[str]) -> list[str]:
    jd_words = Counter(words(jd))
    docs = {k: set(words(f"{v.get('note', '')} {v.get('source', '')}")) for k, v in ev.items()}
    df = Counter(w for d in docs.values() for w in d)
    n = max(len(docs), 1)
    score = {}
    for k, d in docs.items():
        s = sum(math.log(n / df[w]) * min(jd_words[w], 3) for w in d if w in jd_words)
        if k in seed:
            s += 50
        if s > 0:
            score[k] = s
    return sorted(score, key=lambda k: -score[k])


def corpus_index() -> dict[str, Counter]:
    return {p.relative_to(JS).as_posix(): Counter(words(p.read_text(errors="replace")))
            for p in sorted((PROFILE / "corpus").rglob("*.md"))}


def per_row(matrix: Path, ev: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Lines for every R/Q/P row (best evidence ids and corpus files for its words) and the ids they name."""
    rows = [r for r in (yaml.safe_load(matrix.read_text()) or {}).get("rows") or [] if r.get("type") in ROW_TYPES]
    if not rows:
        return [], []
    docs = {k: set(words(f"{v.get('note', '')} {v.get('source', '')}")) for k, v in ev.items()}
    df = Counter(w for d in docs.values() for w in d)
    corpus = corpus_index()
    cdf = Counter(w for c in corpus.values() for w in c)
    n, cn = max(len(docs), 1), max(len(corpus), 1)
    out, ids = [], []
    for r in sorted(rows, key=lambda r: (r.get("rank", 9), r["id"])):
        text = " ".join([str(r.get("text", "")), *map(str, r.get("terms") or []), *map(str, r.get("synonyms") or [])])
        rw = set(words(text))
        sc = {k: sum(math.log(n / df[w]) for w in d & rw) for k, d in docs.items()}
        best = [k for k in sorted(sc, key=lambda k: -sc[k]) if sc[k] > 0][:3]
        best = list(dict.fromkeys([*map(str, r.get("candidate_evidence") or [])][:2] + best))[:4]
        csc = {f: sum(math.log(cn / cdf[w]) * min(c[w], 3) for w in rw if c[w]) for f, c in corpus.items()}
        files = [f.replace("profile/corpus/", "") for f in sorted(csc, key=lambda f: -csc[f]) if csc[f] > 0][:2]
        out.append(f"- {r['id']} (rank {r.get('rank')}): {clip(r.get('text', ''), 110)} → "
                   f"{' '.join(best) or 'no evidence match: a question for the candidate or an honest gap'}"
                   f"{' · corpus: ' + ', '.join(files) if files else ''}")
        ids += best
    return out, ids


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build(jd: str, cap: int = CAP, matrix: Path | None = None) -> str:
    rows = taxonomy()
    ev = evidence()
    skills = matched_skills(jd, rows)
    row_lines, row_ids = per_row(matrix, ev) if matrix and matrix.is_file() else ([], [])
    seed = [i for r, _ in skills for i in r.get("evidence") or []] + [i for i in row_ids if i in ev]
    out = ["<!-- GENERATED by scripts/evidence_slice.py - copied from profile/ records; do not edit -->",
           "# Evidence slice for this role",
           "Copied from profile/skills-taxonomy.yaml, evidence.yaml and PROFILE.md; nothing reworded. Open a source only "
           "when a claim needs more than its note. `--ids <ev-...>` prints any other record.", "",
           f"## Skills the JD names ({len(skills)})"]
    for r, hits in skills:
        flags = " ".join(f for f, on in (("CONFIRM-FIRST", r.get("confirm")), ("INTERNAL-ONLY",
                         r.get("discretion") == "internal-only")) if on)
        note = f" — {clip(r['note'], 220)}" if r.get("note") else ""
        out.append(f"- **{r['skill']}** ({r.get('level')}, last {r.get('last_used')}; JD says: {', '.join(hits)}) "
                   f"{' '.join(r.get('evidence') or [])} {flags}{note}".rstrip())
    if row_lines:
        out += ["", f"## Per requirement: the whole record searched for each R, Q, P row ({matrix.name})", *row_lines]
    used = sum(len(x) + 1 for x in out)
    chosen: list[str] = []
    chunk = ["", "## Evidence (skills' own ids first, then by shared JD words)"]
    for k in rank_evidence(jd, ev, seed):
        v = ev[k]
        line = f"- `{k}`{' [internal-only]' if v.get('discretion') == 'internal-only' else ''}: {clip(v.get('note', ''), 300)}"
        if used + len(line) > cap * 0.6:
            break
        chunk.append(line)
        chosen.append(k)
        used += len(line) + 1
    out += chunk
    paras = [p for p in profile_paragraphs() if set(EV.findall(p)) & set(chosen)]
    paras.sort(key=lambda p: -len(set(EV.findall(p)) & set(chosen)))
    out += ["", "## PROFILE.md lines that cite them (final wording)"]
    for p in paras:
        line = clip(p, 700)
        if used + len(line) > cap * 0.9:
            break
        out.append(line if line.startswith(("-", "*", "#")) else f"- {line}")
        used += len(line) + 1
    out += ["", "## Where the proof lives (open only when needed)"]
    for k in chosen:
        line = f"- `{k}`: {clip(ev[k].get('source', ''), 140)} ({clip(ev[k].get('locator', ''), 40)})"
        if used + len(line) > cap:
            out.append(f"- … {len(chosen) - chosen.index(k)} more: `--ids` prints them")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) + "\n"


def show_ids(ids: list[str]) -> str:
    ev = evidence()
    out = []
    for i in ids:
        v = ev.get(i)
        out.append(f"{i}: not in evidence.yaml" if v is None else
                   f"{i}\n  source: {v.get('source')}\n  locator: {v.get('locator')}\n  note: {clip(v.get('note', ''), 2000)}"
                   + (f"\n  discretion: {v['discretion']}" if v.get("discretion") else ""))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slug")
    ap.add_argument("--jd", type=Path)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--cap", type=int, default=CAP)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.ids:
        print(show_ids(a.ids))
        return 0
    if not (a.slug or a.jd):
        ap.error("pass --slug, --jd or --ids")
    matrix = JS / "research" / "targets" / f"{a.slug}.matrix.yaml" if a.slug else None
    text = build(jd_text(a.slug, a.jd), a.cap, matrix)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out} ({len(text.encode())} bytes)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
