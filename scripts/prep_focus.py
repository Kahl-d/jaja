#!/usr/bin/env python3
"""prep_focus.py - the first prep pack for any screen, in one command.

Given an application slug it prints:
  - the focus: focus_profiles.pick on the application's job title (applications/<slug>/application.md `title`, else
    jd.md); "none" when no profile matches, said, never guessed;
  - the story to tell: the prep/stories/*.md paths named in the profile's section 7 "Story to tell", each with its
    "One line (30 seconds)";
  - ten questions from prep/questions.yaml for that focus: a question tagged `focus: [<family>]` first, then questions
    reported in this company's loop (company_loops), then reported before representative, spread across the focus's
    topics (FOCUS_TOPICS: the lens's topic ids in prep/questions.yaml) with behavioral always in the mix;
  - the Reader model wants (research/targets/<slug>.md section 6, `W1 | want | source`) when the package has them,
    else "Reader model: none" (missing is not empty).

Read only. Exit 0, or 1 when the slug has no application record.

CLI:  python3 scripts/prep_focus.py [--root JS] <slug>
      python3 scripts/prep_focus.py --check        (every focus profile has a topic map; exit 1 when one does not)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import focus_profiles as fp  # noqa: E402
import reader_model as rm  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

N_QUESTIONS = 10
# The topics of prep/questions.yaml each focus is asked about most, in order (behavioral is always added): the owner's
# policy.prep.focus_topics maps a focus family to its topic ids; a family with no entry takes policy.prep.default_topics.
FOCUS_TOPICS: dict[str, list[str]] = {str(k): [str(t) for t in v] for k, v in
                                      (cfg.get("policy.prep.focus_topics", {}) or {}).items() if isinstance(v, list)}
DEFAULT_TOPICS = [str(t) for t in (cfg.get("policy.prep.default_topics", []) or [])] or ["system-design", "coding"]
STORY_RE = re.compile(r"prep/stories/[a-z0-9-]+\.md")


def _front(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    try:
        return (yaml.safe_load(m.group(1)) or {}) if m else {}
    except yaml.YAMLError:
        return {}


def application(root: Path, slug: str) -> dict[str, Any] | None:
    d = Path(root) / "applications" / slug
    if not d.is_dir():
        return None
    meta = _front(d / "application.md")
    if not meta.get("title"):
        meta = {**_front(d / "jd.md"), **{k: v for k, v in meta.items() if v}}
    return meta


def stories(root: Path, family: str | None) -> list[dict[str, str]]:
    lens = next((x for x in fp.lenses(root) if x["family"] == family), None) if family else None
    if not lens:
        return []
    m = re.search(r"\*\*Story to tell[^*]*\*\*:?(.*?)(?=\n\s*[-*]?\s*\*\*|\n## |\Z)", lens["section7"], re.S | re.I)
    out = []
    for rel in dict.fromkeys(STORY_RE.findall(m.group(1) if m else "")):
        p = Path(root) / rel
        one = ""
        if p.is_file():
            mo = re.search(r"\*\*One line \(30 seconds\)\.\*\*\s*(.+)", p.read_text(encoding="utf-8"))
            one = mo.group(1).strip() if mo else ""
        out.append({"path": rel, "one_line": one if p.is_file() else "(story file missing)"})
    return out


def wants(root: Path, slug: str) -> list[dict[str, str]]:
    p = Path(root) / "research" / "targets" / f"{slug}.md"
    if not p.is_file():
        return []
    sec = rm.sections(p.read_text(encoding="utf-8")).get("6", "")
    out = []
    for line in sec.splitlines():
        m = rm.WANT.match(line)
        if m:
            out.append({"id": m.group(1), "want": m.group(2), "source": m.group(3)})
    return out


def questions(root: Path, family: str | None, company: str = "") -> list[dict[str, Any]]:
    path = Path(root) / "prep" / "questions.yaml"
    bank = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("questions") or [] if path.is_file() else []
    topics = list(FOCUS_TOPICS.get(family or "", DEFAULT_TOPICS)) + ["behavioral"]
    co = company.lower().strip()

    def key(q: dict[str, Any]) -> tuple:
        loops = [str(c).lower() for c in (q.get("company_loops") or [])]
        return (family not in (q.get("focus") or []), not (co and co in loops), q.get("provenance") != "reported", q["id"])

    tagged = sorted([q for q in bank if family and family in (q.get("focus") or [])], key=key)
    picked: list[dict[str, Any]] = tagged[:N_QUESTIONS]
    seen = {q["id"] for q in picked}
    loop_hits = sorted([q for q in bank if co and co in [str(c).lower() for c in (q.get("company_loops") or [])]
                        and q["id"] not in seen and (q.get("topic") in topics or q.get("cluster") in topics)], key=key)
    for q in loop_hits:
        if len(picked) < N_QUESTIONS:
            picked.append(q)
            seen.add(q["id"])
    pools = {t: sorted([q for q in bank if q.get("topic") == t and q["id"] not in seen], key=key) for t in topics}
    while len(picked) < N_QUESTIONS and any(pools.values()):
        for t in topics:                                   # round robin: every topic of the focus gets a turn
            if pools[t] and len(picked) < N_QUESTIONS:
                q = pools[t].pop(0)
                if q["id"] not in seen:
                    picked.append(q)
                    seen.add(q["id"])
    return picked


def build(root: Path, slug: str) -> dict[str, Any] | None:
    app = application(root, slug)
    if app is None:
        return None
    title = str(app.get("title") or "")
    ranked = fp.pick(root, title) if title else []
    family = ranked[0]["family"] if ranked else None
    company = str(app.get("company") or "")
    return {"slug": slug, "title": title, "company": company, "focus": family,
            "why": ranked[0]["why"] if ranked else "", "stories": stories(root, family),
            "questions": questions(root, family, company), "wants": wants(root, slug)}


def render(pack: dict[str, Any]) -> str:
    L = [f"# First prep pack · {pack['company']} · {pack['title']} ({pack['slug']})", ""]
    L.append(f"focus: {pack['focus']} ({pack['why']})" if pack["focus"] else
             f"focus: none (no focus profile matches '{pack['title']}'; questions from the default topics)")
    L += ["", "Story to tell (profile section 7):"]
    L += [f"  {s['path']}: {s['one_line']}" for s in pack["stories"]] or ["  none named"]
    L += ["", "Reader model wants (package section 6):" if pack["wants"] else
          "Reader model: none (research/targets/<slug>.md has no section 6 wants yet)"]
    L += [f"  {w['id']} | {w['want']} | {w['source']}" for w in pack["wants"]]
    L += ["", f"Questions ({len(pack['questions'])} from prep/questions.yaml):"]
    for q in pack["questions"]:
        tag = "reported" if q.get("provenance") == "reported" else (q.get("provenance") or "")
        L.append(f"  {q['id']} [{q.get('topic')}{', ' + tag if tag else ''}] {' '.join(str(q['q']).split())}")
    return "\n".join(L).replace("\u2014", "-") + "\n"


def unmapped(root: Path) -> list[str]:
    return [x["family"] for x in fp.lenses(root) if x["family"] not in FOCUS_TOPICS]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--check", action="store_true", help="every focus profile has a topic map")
    p.add_argument("slug", nargs="?")
    args = p.parse_args(argv)
    if args.check:
        miss = unmapped(args.root)
        print(f"prep_focus: {len(miss)} focus profile(s) without a topic map" + (f": {', '.join(miss)}" if miss else ""))
        return 1 if miss else 0
    if not args.slug:
        p.error("a slug is required (or --check)")
    pack = build(args.root, args.slug)
    if pack is None:
        print(f"no application record: applications/{args.slug}/", file=sys.stderr)
        return 1
    print(render(pack), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
