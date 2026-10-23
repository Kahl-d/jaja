#!/usr/bin/env python3
"""network_plan.py - the day's network plan.

The aim is a network of people who make sense, not random people but people who can actually help land the job:
connect with them, read their hiring posts, and only sometimes comment on a post, giving the owner's own view of its
subject. Commenting is rare.

Writes state/network/<date>.md with:
  - at most 10 connection suggestions: who, why (a hiring poster, a team at a NEXT-UP company, the path to an open role,
    a referrer path, an alum), sent WITHOUT a note (no script writes an opener; once the person accepts, messenger opens
    the conversation with a question about the person's own work, starting from the why line). Nobody
    without a reason is suggested; nobody already connected, drafted, closed or do-not-contact;
  - the people in a live thread: no cold note; a "continue from here" (their last Timeline line, verbatim) for
    messenger;
  - each NEXT-UP company with nobody known: the people_traces.py command that finds its people (never run here);
  - at most 3 posts worth a comment (the X watch review files of the last 3 days and state/network/posts.yaml, the
    LinkedIn posts read in the owner's browser): only posts whose subject is the owner's work; the drafted comment is the
    owner's view on that subject, the focus profile's vision line, from profile/answers/<family>.md (every sentence cited there);
  - one post idea a week (the first plan of an ISO week): a topic whose story has not been an idea lately, for
    linkedin-editor to draft.
Each comment is also a staged draft in state/drafts/N-<date>/ (channel linkedin-comment or x-reply; X replies in state/drafts/N-<date>-x/, since outbox files LinkedIn and email only), checked with outbox.py
lint (M16 included); nothing is filed (`outbox.py create`, or `x_api.py reply --request`, is a separate step) and
nothing is sent. Records (prospects, targets, outbox) are read, never written.

Sources: hub/NEXT-UP.md, prospects/*.md, targets/people-watch.yaml when it exists, state/network/network-map.json when
network_map.py writes it (rows {name, company, company_slug, linkedin_url, why, connected}), the X watch review
files, state/network/posts.yaml. A missing source is said in the plan (missing is not empty).

--evening prints the evening review's one network line and writes nothing: of today's plan suggestions, the
connection notes and the X follows still waiting (a person since connected, closed or followed is no longer waiting).

CLI:  python3 scripts/network_plan.py [--root JS] [--today D] [--dry-run | --evening]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402

MAX_CONNECT, MAX_COMMENTS, MAX_THREADS = 10, 3, 5
NOTE_LIMIT, X_LIMIT = 200, 280
CONNECTED = re.compile(r"1st[- ]degree|connection accepted|\bconnected\b", re.I)


def _schools() -> list[tuple[str, re.Pattern[str]]]:
    """The schools whose alumni are worth a connection: config identity.schools, a list of {name, pattern}."""
    out = []
    for entry in cfg.get("identity.schools", []) or []:
        if isinstance(entry, dict) and entry.get("name") and entry.get("pattern"):
            out.append((str(entry["name"]), re.compile(str(entry["pattern"]), re.I)))
    return out


def alum_of(text: str) -> str | None:
    """The configured school the text names (a headline, a relation), or None."""
    return next((name for name, rx in _schools() if rx.search(text)), None)


CLOSED = {"do_not_contact", "closed", "drafted", "replied", "in_conversation", "interviewing"}
# The owner's subjects (config policy.network.topics: {slug: {title, keywords}}): a post is worth a comment when it
# is about one of them. The slug names the answer bank profile/answers/<slug>.md the comment is drawn from.
_TOPIC_CFG = cfg.get("policy.network.topics", {}) or {}
TOPICS: dict[str, tuple[str, ...]] = {str(slug): tuple(str(k).lower() for k in (spec or {}).get("keywords", []) or [])
                                      for slug, spec in _TOPIC_CFG.items() if isinstance(spec, dict)}
TITLE = {str(slug): str(spec.get("title") or slug) for slug, spec in _TOPIC_CFG.items() if isinstance(spec, dict)}
VIEW_SECTION = str(cfg.get("policy.network.view_section", "Why this field"))          # answer-bank heading: the owner's view
STORY_SECTION = str(cfg.get("policy.network.story_section", "A project you are proud of"))   # heading: the owner's story
FALLBACK_TOPIC = str(cfg.get("policy.network.fallback_topic", "") or "")              # answer bank used when a topic has none
TAG = re.compile(r"\s*\[ev:([^\]]*)\]")


# ------------------------------------------------------------------------------------------------------------ sources
def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def next_up(root: Path) -> list[dict[str, Any]]:
    p = Path(root) / "hub" / "NEXT-UP.md"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not re.match(r"^\d+[a-z]?$", cells[0]) or " · " not in cells[1]:
            continue
        company, role = cells[1].split(" · ", 1)
        state = next((s for s in ("READY", "PACKAGE", "JOB", "BLOCKED") if s in " ".join(cells[5:8])), "")
        out.append({"rank": len(out) + 1, "company": company.strip(), "role": role.strip(), "state": state})
    return out


def _company_row(rows: list[dict[str, Any]], company: Any, slug: Any) -> dict[str, Any] | None:
    keys = {k for k in (_norm(company), _norm(slug)) if k}
    for r in rows:
        k = _norm(r["company"])
        if k and any(x == k or x.startswith(k) or k.startswith(x) for x in keys):
            return r
    return None


def prospects(root: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((Path(root) / "prospects").glob("*.md")):
        meta, body = jc.read_record(p)
        timeline = [l[2:].strip() for l in body.split("## Timeline", 1)[-1].splitlines() if l.startswith("- ")] \
            if "## Timeline" in body else []
        out.append({**meta, "slug": p.stem, "_timeline": timeline})
    return out


def _yaml(path: Path, key: str) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data.get(key) if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict)]


def watch_posts(root: Path, today: date) -> list[dict[str, Any]]:
    """Posts from the X watch review files of the last 3 days and from state/network/posts.yaml."""
    out = []
    for d in range(3):
        p = Path(root) / "state" / "review" / f"x-watch-{(today - timedelta(days=d)).isoformat()}.md"
        if not p.is_file():
            continue
        for sec in re.split(r"(?m)^## ", p.read_text(encoding="utf-8"))[1:]:
            head, _, rest = sec.partition("\n")
            parts = [x.strip() for x in head.split(" · ")]
            text = "\n".join(l for l in rest.splitlines() if l.strip() and not l.startswith("- ")).strip()
            if len(parts) >= 3:
                out.append({"author": parts[0], "url": parts[2], "text": text, "channel": "x-reply", "source": p.name})
    for r in _yaml(Path(root) / "state" / "network" / "posts.yaml", "posts") or []:
        if r.get("url") and r.get("text"):
            ch = "x-reply" if "x.com/" in str(r["url"]) or "twitter.com/" in str(r["url"]) else "linkedin-comment"
            out.append({"author": r.get("author") or "", "url": r["url"], "text": str(r["text"]), "channel": ch,
                        "source": "state/network/posts.yaml"})
    return out


def answers(root: Path, family: str) -> dict[str, tuple[str, list[str]]]:
    """{section: (first sentence without its tag, its ev ids)} from profile/answers/<family>.md."""
    p = Path(root) / "profile" / "answers" / f"{family}.md"
    if not p.is_file():
        return {}
    out = {}
    for sec in re.split(r"(?m)^## ", p.read_text(encoding="utf-8"))[1:]:
        head, _, rest = sec.partition("\n")
        line = next((l.strip() for l in rest.splitlines() if TAG.search(l)), "")
        if line:
            ids = [i.strip() for m in TAG.finditer(line) for i in m.group(1).split(",") if i.strip()]
            out[re.sub(r"\s*\(\d+ words max\)\s*$", "", head).strip()] = (TAG.sub("", line).strip(), ids)
    return out


# ------------------------------------------------------------------------------------------------------------ drafts
def _first(name: Any) -> str:
    return (str(name or "").split() or ["there"])[0]


def note(name: Any, reason_kind: str, company: str, role: str) -> str:
    """No script writes an opener. A template note ("close to what I build ... glad to connect") reads as a sales
    pitch, so the connection goes without a note and the messenger opens the conversation after the person accepts,
    with a question about the person's own work (the plan's why line is the starting point)."""
    return ""


def _lint(channel: str, body: str, kind: str, root: Path) -> list[str]:
    import outbox
    return outbox.lint_draft(channel, body, kind=kind, root=root)


# ------------------------------------------------------------------------------------------------------------ plan
def build(root: Path, today: date) -> dict[str, Any]:
    root = Path(root)
    nu = next_up(root)
    people = prospects(root)
    watch = _yaml(root / "targets" / "people-watch.yaml", "people")
    netmap = _yaml(root / "state" / "network" / "network-map.json", "people")
    cands: list[dict[str, Any]] = []
    threads: list[dict[str, Any]] = []
    watched = {_norm(r.get("name")): r for r in (watch or []) if str(r.get("status") or "watch") != "dropped"}
    for p in people:
        status = str(p.get("status") or "")
        if status in ("do_not_contact", "closed"):
            continue
        has_thread = bool(p.get("linkedin_threads") or p.get("thread_ids") or int(p.get("inbound_count") or 0) > 0)
        connected = bool(CONNECTED.search(" ".join(str(p.get(k) or "") for k in ("relation", "found_via", "source"))))
        why, kinds = [], []
        row = _company_row(nu, p.get("company"), p.get("company_slug"))
        rel = str(p.get("relation") or "")
        if "target of" in rel and row:
            why.append(f"on the path to NEXT-UP #{row['rank']} {row['company']} · {row['role']} ({row['state'] or 'listed'})")
            kinds.append("role-path")
        elif row:
            why.append(f"works at {row['company']}, NEXT-UP #{row['rank']} ({row['state'] or 'listed'})")
            kinds.append("next-up")
        if "referrer" in (p.get("tags") or []):
            why.insert(0, f"a referrer path at {p.get('company') or 'their company'}")
            kinds.insert(0, "referrer")
        w = watched.get(_norm(p.get("name")))
        if w:
            why.insert(0, f"posts about hiring: {w.get('why')}")
            kinds.insert(0, "hiring")
        school = alum_of(" ".join(str(p.get(k) or "") for k in ("headline", "relation", "title")))
        if school:
            why.append(f"an alum ({school})")
            kinds.append("alum")
        if not why:
            continue                                   # never random: no reason, no suggestion
        if has_thread and status not in ("do_not_contact",):
            last = p["_timeline"][-1] if p["_timeline"] else "(no Timeline line)"
            threads.append({"slug": p["slug"], "name": p.get("name"), "company": p.get("company"),
                            "continue_from": last, "why": why})
            continue
        if connected or status in CLOSED or not p.get("linkedin_url"):
            continue
        if not p.get("linkedin_url") and w and w.get("x_handle") and str(w.get("status") or "watch") != "followed":
            cands.append({"slug": p["slug"], "name": p.get("name"), "company": p.get("company") or "", "why": why,
                          "kind": "hiring", "role": "", "to": f"https://x.com/{str(w['x_handle']).lstrip('@')}",
                          "channel": "x-follow", "handle": w["x_handle"], "score": 4 + (1 if row else 0),
                          "rank": row["rank"] if row else 50, "source": "prospects + people-watch"})
            continue
        score = (4 if "hiring" in kinds else 0) + (3 if row and row["state"] in ("READY", "PACKAGE") else 2 if row else 0) + (2 if "role-path" in kinds else 0) \
            + (2 if "referrer" in kinds else 0) + (1 if "alum" in kinds else 0) + (1 if p.get("priority") in ("P0", "P1") else 0)
        cands.append({"slug": p["slug"], "name": p.get("name"), "company": p.get("company") or "", "why": why,
                      "kind": kinds[0], "role": row["role"] if row and kinds[0] == "role-path" else "",
                      "to": p.get("linkedin_url"), "score": score, "rank": row["rank"] if row else 99, "source": "prospects"})
    known = {_norm(c["name"]) for c in cands} | {_norm(p.get("name")) for p in people}
    for r in (watch or []):
        if str(r.get("status") or "watch") in ("dropped", "followed") or _norm(r.get("name")) in known:
            continue
        if not (r.get("linkedin_url") or r.get("x_handle")):
            continue
        row = _company_row(nu, r.get("company"), r.get("company"))
        channel = "linkedin-connect" if r.get("linkedin_url") else "x-follow"
        to = r.get("linkedin_url") or f"https://x.com/{str(r['x_handle']).lstrip('@')}"
        cands.append({"slug": jc.slugify(str(r.get("name"))), "name": r.get("name"),
                      "company": row["company"] if row else str(r.get("company") or ""),
                      "why": [f"posts about hiring: {r.get('why')}"], "kind": "hiring", "role": "", "to": to,
                      "channel": channel, "handle": r.get("x_handle"),
                      "score": 4 + (1 if row else 0), "rank": row["rank"] if row else 50, "source": "people-watch"})
    for r in (netmap or []):
        if r.get("connected") or not r.get("linkedin_url") or not r.get("why") or _norm(r.get("name")) in known:
            continue
        row = _company_row(nu, r.get("company"), r.get("company_slug"))
        cands.append({"slug": _norm(r.get("name")), "name": r.get("name"), "company": r.get("company") or "",
                      "why": [str(r["why"])], "kind": "next-up" if row else "referrer", "role": "", "to": r["linkedin_url"],
                      "score": 3 if row else 1, "rank": row["rank"] if row else 99, "source": "network-map"})
    cands.sort(key=lambda c: (-c["score"], c["rank"], c["slug"]))
    connect = []
    for c in cands[:MAX_CONNECT]:
        if c.get("channel") == "x-follow":             # a follow carries no text; it runs only through an approval
            connect.append({**c, "note": "", "lint": []})
            continue
        connect.append({**c, "channel": "linkedin-connect", "note": note(c["name"], c["kind"], c["company"], c["role"]),
                        "lint": []})                   # no note, nothing to lint
    covered = {_norm(p.get("company")) for p in people if p.get("company")} | {_norm(c["company"]) for c in connect}
    leads = [r for r in nu if _norm(r["company"]) not in covered]
    comments = []
    for post in watch_posts(root, today):
        low = post["text"].lower()
        hits = {fam: sum(low.count(k) for k in keys) for fam, keys in TOPICS.items()}
        fam, n = max(hits.items(), key=lambda kv: kv[1])
        if n == 0:
            continue                                   # not the owner's subject: no comment
        bank = answers(root, fam) or (answers(root, FALLBACK_TOPIC) if FALLBACK_TOPIC else {})
        view = bank.get(VIEW_SECTION)
        if not view:
            continue
        work = bank.get(STORY_SECTION)
        text = view[0] if post["channel"] == "x-reply" or not work else f"{view[0]} {work[0]}"
        if post["channel"] == "x-reply" and len(text) > X_LIMIT:
            continue
        ids = sorted(set(view[1] + (work[1] if work and text != view[0] else [])))
        comments.append({**post, "family": fam, "hits": n, "draft": text, "evidence_ids": ids,
                         "lint": _lint(post["channel"], text, "comment", root)})
    comments = sorted(comments, key=lambda c: -c["hits"])[:MAX_COMMENTS]
    return {"date": today.isoformat(), "connect": connect, "threads": threads[:MAX_THREADS], "leads": leads,
            "comments": comments, "post_idea": post_idea(root, today),
            "sources": {"next_up": len(nu), "prospects": len(people),
                        "people_watch": None if watch is None else len(watch),
                        "network_map": None if netmap is None else len(netmap),
                        "posts": len(watch_posts(root, today))}}


def post_idea(root: Path, today: date) -> dict[str, Any] | None:
    folder = Path(root) / "state" / "network"
    year, week, _ = today.isocalendar()
    used: dict[str, str] = {}
    for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        m = re.search(r"^Post idea: (\S+)", p.read_text(encoding="utf-8"), re.M)
        if not m or d >= today:
            continue
        if d.isocalendar()[:2] == (year, week):
            return None                                # one idea a week
        used[m.group(1)] = p.stem
    fams = [f for f in TOPICS if answers(root, f)]
    if not fams:
        return None
    fam = sorted(fams, key=lambda f: (used.get(f, ""), list(TOPICS).index(f)))[0]
    bank = answers(root, fam)
    story, view = bank.get(STORY_SECTION), bank.get(VIEW_SECTION)
    return {"family": fam, "title": TITLE.get(fam, fam), "story": story, "view": view}


# ------------------------------------------------------------------------------------------------------------ output
def render(plan: dict[str, Any], lint_result: str | None) -> str:
    s = plan["sources"]
    absent = lambda v, what: f"{v}" if v is not None else f"absent ({what})"   # noqa: E731
    L = [f"# Network plan · {plan['date']} ", "",
         "Generated by scripts/network_plan.py. Drafts for the owner's yes: nothing is filed or sent; the owner sends "
         "every connection request and comment personally (LinkedIn is never automated).", "",
         "## Sources read",
         f"- hub/NEXT-UP.md rows: {s['next_up']} · prospects: {s['prospects']} · posts (X watch, posts.yaml): {s['posts']}",
         f"- targets/people-watch.yaml: {absent(s['people_watch'], 'not created yet')}",
         f"- state/network/network-map.json: {absent(s['network_map'], 'network_map.py has not written it')}",
         "- LinkedIn connections: inferred from prospect records until the LinkedIn export is parsed "
         "(the first step is the owner's)", "",
         f"## Connection suggestions ({len(plan['connect'])}, at most {MAX_CONNECT})", "",
         "| # | who | company | why | note (chars), or the X follow | lint |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(plan["connect"], 1):
        what = (f"X follow, no note: `python3 scripts/x_api.py follow --handle {str(c.get('handle') or '').lstrip('@')} "
                f"--request` (an approval; after X credits)") if c.get("channel") == "x-follow" \
            else "connect with no note; messenger opens after the person accepts"
        L.append(f"| {i} | {c['name']} ({c['source']}) | {c['company'] or '-'} | {'; '.join(c['why'])} | "
                 f"{what} | {'clean' if not c['lint'] else '; '.join(c['lint'])} |")
    L += ["", f"## In a live thread: messenger continues, no cold note ({len(plan['threads'])})"]
    L += [f"- {t['name']} ({t['company'] or '-'}): last record line: {t['continue_from']} (messenger opens the thread "
          f"and starts from the last message, verbatim)" for t in plan["threads"]] or ["- none"]
    L += ["", "## NEXT-UP companies with nobody known (find the team first; run by hand, it reads the web)"]
    L += [f"- #{r['rank']} {r['company']} · {r['role']}: `python3 scripts/people_traces.py --company "
          f"{re.sub(r'[^a-z0-9]+', '-', r['company'].lower()).strip('-')} --team agents`" for r in plan["leads"]] or ["- none"]
    L += ["", f"## Posts worth a comment ({len(plan['comments'])}, at most {MAX_COMMENTS})"]
    for c in plan["comments"]:
        L += [f"- {c['author']} · {c['url']} ({c['source']}; subject: {c['family']})",
              f"  - post: {' '.join(c['text'].split())[:240]}",
              f"  - draft ({c['channel']}, {len(c['draft'])} chars; evidence {', '.join(c['evidence_ids'])}): {c['draft']}",
              f"  - lint: {'clean' if not c['lint'] else '; '.join(c['lint'])}"]
    if not plan["comments"]:
        L.append("- none (no post in the sources is about the owner's work)")
    L += ["", "## Post idea"]
    pi = plan["post_idea"]
    if pi:
        L += [f"Post idea: {pi['family']}", f"- Theme: {pi['title']}",
              f"- The story: {pi['story'][0] if pi['story'] else '(none)'} (evidence {', '.join(pi['story'][1]) if pi['story'] else '-'})",
              f"- The owner's angle: {pi['view'][0] if pi['view'] else '(none)'}",
              "- For linkedin-editor to draft from the record (posts are rare)."]
    else:
        L.append("- none this time (one a week; this week's idea is in an earlier plan)")
    L += ["", "## Checks", f"- outbox lint: {lint_result}" if lint_result is not None else "- outbox lint: not run (dry run)"]
    return "\n".join(L).replace("\u2014", "-") + "\n"


_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(?P<who>[^|]+?)\s*\|[^|]*\|[^|]*\|\s*(?P<what>[^|]*)\|")
DONE_STATUS = {"do_not_contact", "closed", "replied", "in_conversation", "interviewing"}


def evening(root: Path, today: date) -> dict[str, Any]:
    """Read only: today's plan suggestions (state/network/<date>.md) still waiting, connection notes and X follows
    apart. Waiting means the person is not since connected, closed or replied (prospects) or followed or dropped
    (people-watch). A missing plan is said, never counted as zero."""
    root = Path(root)
    rel = f"state/network/{today.isoformat()}.md"
    path = root / rel
    if not path.is_file():
        return {"plan": None, "line": f"network: no plan for {today.isoformat()} (network_plan.py has not run today; "
                                      "not zero, missing)"}
    text = path.read_text(encoding="utf-8")
    table = text.split("## Connection suggestions", 1)[-1].split("\n## ", 1)[0]
    rows = []
    for line in table.splitlines():
        m = _ROW.match(line)
        if m:
            who = m.group("who").rsplit(" (", 1)[0].strip()
            rows.append((who, "x-follow" if m.group("what").strip().startswith("X follow") else "linkedin-connect"))
    done = set()
    for p in prospects(root):
        status = str(p.get("status") or "")
        linked = " ".join(str(p.get(k) or "") for k in ("relation", "found_via", "source"))
        if status in DONE_STATUS or CONNECTED.search(linked):
            done.add(_norm(p.get("name")))
    for r in _yaml(root / "targets" / "people-watch.yaml", "people") or []:
        if str(r.get("status") or "watch") in ("followed", "dropped"):
            done.add(_norm(r.get("name")))
    out = {"plan": rel}
    for kind, key in (("linkedin-connect", "connect"), ("x-follow", "follows")):
        mine = [w for w, k in rows if k == kind]
        out[f"{key}_total"], out[f"{key}_waiting"] = len(mine), sum(1 for w in mine if _norm(w) not in done)
    out["line"] = (f"network ({rel}): connection suggestions waiting {out['connect_waiting']} of {out['connect_total']} "
                   f"(the owner sends each); X follows waiting {out['follows_waiting']} of {out['follows_total']} "
                   "(each needs an approval and X credits)")
    return out


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def write_drafts(root: Path, plan: dict[str, Any]) -> str:
    batch = f"N-{plan['date']}"
    folder = Path(root) / "state" / "drafts" / batch
    for c in plan["connect"]:
        if c.get("channel") == "x-follow" or not c.get("note"):
            continue                                   # a connection without a note has no draft
        meta = {"channel": "linkedin-connect", "kind": "intro", "to": c["to"], "label": "network-plan",
                "personalization": "; ".join(c["why"]), "checks": []}
        if c["source"] == "prospects":
            meta["prospect"] = c["slug"]
        _write(folder / f"connect-{c['slug']}.md", jc.fm.dump(meta, c["note"] + "\n"))
    for i, c in enumerate(plan["comments"], 1):
        meta = {"channel": c["channel"], "kind": "comment", "to": c["url"], "author": c["author"], "label": "network-plan",
                "evidence_ids": c["evidence_ids"], "personalization": f"subject: {c['family']}", "checks": []}
        # x-reply is not an outbox channel (create files a batch all-or-nothing): X replies get their own folder and go
        # through `x_api.py reply --request`
        dest = folder if c["channel"] != "x-reply" else folder.with_name(f"{batch}-x")
        _write(dest / f"comment-{i}.md", jc.fm.dump(meta, c["draft"] + "\n"))
    return batch


def lint_staged(root: Path, batch: str) -> str:
    import js_comms_util as cu
    import outbox
    roots = cu.Roots(js=Path(root), os=jc.os_root_for(Path(root)))
    drafts = Path(root) / "state" / "drafts"
    staged = sorted(drafts.glob(f"{batch}/*.md")) + sorted(drafts.glob(f"{batch}-x/*.md"))
    out: dict[str, list[str]] = {}
    for p in staged:                                   # lint_batch's checks, per draft (x-reply is not an outbox channel)
        meta, body = cu.read_record(p)
        issues = outbox.lint_draft(meta.get("channel") or "", body, kind=meta.get("kind"), root=Path(root))
        if meta.get("channel") in outbox.CHANNELS:
            issues += outbox.message_issues(roots, meta, body)
        if issues:
            out[p.name] = issues
    return str(out) if out else "{}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today", type=jc.cli_date, default=None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan; write no plan and no draft")
    mode.add_argument("--evening", action="store_true", help="print the evening review's network line; write nothing")
    args = ap.parse_args(argv)
    today = args.today or jc.today()
    if args.evening:
        print(evening(args.root, today)["line"])
        return 0
    plan = build(args.root, today)
    if args.dry_run:
        print(render(plan, None), end="")
        return 0
    batch = write_drafts(args.root, plan)
    text = render(plan, lint_staged(args.root, batch))
    path = Path(args.root) / "state" / "network" / f"{today.isoformat()}.md"
    _write(path, text)
    print(text, end="")
    print(f"wrote {path.relative_to(args.root)} and state/drafts/{batch}/ (+ {batch}-x/ for X replies; staged, not filed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
