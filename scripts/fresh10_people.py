#!/usr/bin/env python3
"""fresh10_people.py: one named person per Fresh 10 role, every day.

Fresh 10 ranks a role with no warm path as cold; naming a person first moves it up. For each Fresh 10 row
with no warm path this lists the people the deterministic sources name, for the prospector to verify:
  jd            the JD's own words: "report(s) to", "reporting to", "hiring manager is", "led by" (any case, a colon
                allowed), then a capitalized full name, Dr. or Prof. kept (a title such as "the VP of Engineering" or a
                company or product name is not a name; "work with" is not read: it names products, not people).
  prospect      the department's prospects/ at the company, any warmth, never do_not_contact.
  people-watch  targets/people-watch.yaml rows at the company.
  hn-poster     for a role from Hacker News (a job id ending -hn-<item>), the comment's author and the links in their
                public HN profile (HN's API). Only with --net.
  author-page   with --net --traces, the company's own author pages (people_traces.py: robots.txt first), the name
                read from the page's URL.
A name is "two sources" only when two independent groups agree on it (first and last name, titles and suffixes
dropped, no conflicting middle initial): the JD, the department's records (prospects and people-watch together count
once: both are internal and may come from the same source), HN, and the company's site. Everything else is "to
verify" (a trace is a lead, not an identity). A report, never a prospect record; nothing
outward; LinkedIn is never read (its terms: the prospector's step, in the owner's browser).
Usage: python3 scripts/fresh10_people.py [--today YYYY-MM-DD] [--net] [--traces] [--write]
--write saves state/daily/<date>-fresh10-people.md. Read only otherwise; no network without --net.
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common  # noqa: E402
import people_traces  # noqa: E402
from fresh10 import LA  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ID = re.compile(r"-hn-(\d+)$")
WORD = r"[^\W\d_][\w'.-]*"
JD_PHRASE = re.compile(r"(?i:\breports? (?:directly )?to|\breporting (?:directly )?to|\bhiring manager is|\bled by)"
                       r"[:\s]+(?=((?:(?:Dr|Prof)\.\s)?" + WORD + r"(?:\s" + WORD + r"){0,3}))")   # a lookahead: one name never eats the next phrase
TITLES = {"dr", "prof", "mr", "ms", "mrs"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd"}
GROUP = {"jd": "jd", "prospect": "records", "people-watch": "records", "hn-poster": "hn", "author-page": "site"}
NOT_NAME = {"Our", "The", "Your", "This", "That", "Team", "Teams", "Engineering", "Research", "Head", "Founder",
            "Founders", "Vice", "President", "Director", "Manager", "Chief", "Officer", "Lead", "Senior", "Staff",
            "Principal", "Product", "Applied", "Machine", "Learning", "Data", "Science", "Platform", "Infrastructure",
            "Group", "Leadership", "Executive", "Partners", "Customers", "Engineers", "Scientists", "AI", "ML", "VP",
            "CTO", "CEO", "Cross", "Functional", "Global", "Sales", "Marketing", "Design", "Operations", "Language",
            "Languages", "Models", "Model", "Cloud", "Large", "Labs", "Inc", "Company", "PhDs", "Former", "Industry",
            "Veterans", "San", "Francisco", "HQ", "Experts", "Alumni", "Leaders", "Researchers", "Founding"}


def not_name_words() -> set[str]:
    """Words that never form a person's name: the structural list above plus the configured company, product and
    school words (policy.people.not_name_words; none by default)."""
    return NOT_NAME | {str(w) for w in cfg.get("policy.people.not_name_words", []) or []}
STOP_AFTER = {"Who", "Which", "That", "And", "With", "In", "At", "On", "For", "To", "From", "Is", "Was", "Has"}
KIND_ORDER = ("jd", "prospect", "people-watch", "hn-poster", "author-page")


def jd_names(jd: str) -> list[tuple[str, str]]:
    """(name, the matched phrase) for each full name the JD puts after a reporting or hiring phrase, in order, once."""
    out: dict[str, str] = {}
    not_name = not_name_words()
    for m in JD_PHRASE.finditer(jd or ""):
        words = []
        for w in m.group(1).split():
            if not w[0].isupper() or w.strip(".,") in STOP_AFTER or len(words) >= 3:
                break
            words.append(w)
            if w.endswith(".") and w.strip(".").lower() not in TITLES and len(w.strip(".")) > 1:
                break                                  # a sentence end, not an initial or a title
        words[-1:] = [words[-1].rstrip(".,")] if words and words[-1].strip(".").lower() not in TITLES else words[-1:]
        core = [w for w in words if w.strip(".").lower() not in TITLES]
        if len(core) < 2 or any(w.strip(".,") in not_name for w in core):
            continue
        out.setdefault(" ".join(words), (m.group(0) + " ".join(words)).rstrip(".,"))
    return list(out.items())


def _parts(name: str) -> list[str]:
    words = re.sub(r"[^\w\s'-]", " ", name.lower()).split()
    return [w for w in words if w not in TITLES and w not in SUFFIXES]


def _key(name: str) -> str:
    words = [w for w in _parts(name) if len(w) > 1]
    return f"{words[0]} {words[-1]}" if len(words) >= 2 else " ".join(words)


def _middle(name: str) -> str:
    words = _parts(name)
    return " ".join(w[0] for w in words[1:-1]) if len(words) > 2 else ""


CLEAN_LINK = re.compile(r"https?://[^\s|<>\"'`\\]+")


def hn_poster(http: Any, job_id: str) -> dict[str, Any] | None:
    """The HN comment's author for a job id ending -hn-<item>; None for any other job."""
    m = HN_ID.search(job_id)
    if not m:
        return None
    item = http.get_json(f"{HN_API}/item/{m.group(1)}.json") or {}
    by = str(item.get("by") or "")
    if not by:
        return None
    about = str((http.get_json(f"{HN_API}/user/{by}.json") or {}).get("about") or "")
    # the profile text is untrusted; only its http(s) links are kept, each unescaped and checked on its own
    links = list(dict.fromkeys(u for u in (_html.unescape(h) for h in re.findall(r'href="([^"]+)"', about))
                               if CLEAN_LINK.fullmatch(u)))
    tie = "posted this role in HN Who is hiring" + (f"; their HN profile links {', '.join(links[:3])}" if links else "")
    return {"name": by, "kind": "hn-poster", "tie": tie,
            "url": f"https://news.ycombinator.com/item?id={m.group(1)}",
            "note": "an HN handle, not a confirmed real name"}


def record_people(root: Path, company_slug: str) -> list[dict[str, Any]]:
    """Prospects at the company (any warmth, never do_not_contact) and people-watch rows at the company."""
    out: list[dict[str, Any]] = []
    folder = Path(root) / "prospects"
    if folder.is_dir():
        for path, rec, _ in js_common.iter_records(folder):
            if str(rec.get("company_slug") or "") != company_slug or _blocked(rec):
                continue
            out.append({"name": str(rec.get("name") or path.stem), "kind": "prospect",
                        "tie": f"prospect record (status {rec.get('status') or 'unset'}, warmth {rec.get('warmth') or 0}, "
                               f"title {rec.get('title') or 'unknown'})",
                        "url": f"prospects/{path.name}"})
    blocked = blocked_keys(root)
    watch = Path(root) / "targets" / "people-watch.yaml"
    if watch.is_file():
        for p in (yaml.safe_load(watch.read_text(encoding="utf-8")) or {}).get("people") or []:
            if (isinstance(p, dict) and str(p.get("company") or "") == company_slug and p.get("name")
                    and _key(str(p["name"])) not in blocked):
                handle = f" (x: {p['x_handle']})" if p.get("x_handle") else ""
                out.append({"name": str(p["name"]), "kind": "people-watch",
                            "tie": f"people-watch: {p.get('role') or 'watched'}{handle}", "url": "targets/people-watch.yaml"})
    return out


def _blocked(rec: dict[str, Any]) -> bool:
    return str(rec.get("status") or "") == "do_not_contact" or bool(rec.get("do_not_contact_reason"))


def blocked_keys(root: Path) -> set[str]:
    """The names of every do_not_contact prospect, at any company (a name match is enough to hold a candidate back)."""
    folder = Path(root) / "prospects"
    return {_key(str(rec.get("name") or path.stem)) for path, rec, _ in
            (js_common.iter_records(folder) if folder.is_dir() else []) if _blocked(rec)}


def _author_pages(root: Path, http: Any, slug: str, notes: list[str]) -> list[dict[str, Any]]:
    co = people_traces._company(root, slug)
    out = people_traces.traces(http, co, max_members=0, pause=people_traces.PAUSE)
    notes.extend(out.get("warnings") or [])
    rows = []
    for t in out.get("traces") or []:
        if t.get("kind") != "author-page":
            continue
        name = str(t["url"]).rstrip("/").split("/")[-1].replace("-", " ").title()
        rows.append({"name": name, "kind": "author-page", "tie": "author of a post on the company's own site",
                     "url": t["url"], "note": "an author, not shown to be on the hiring team"})
    return rows


def candidates(root: Path, row: dict[str, Any], rec: dict[str, Any], http: Any = None,
               use_traces: bool = False) -> dict[str, Any]:
    """The named people for one Fresh 10 row, merged by name, with the kinds that named each and a confidence."""
    found: list[dict[str, Any]] = []
    notes: list[str] = []
    ok = {"hn": False, "site": False}
    jd = rec.get("_jd_text") or ""
    for name, phrase in jd_names(jd):
        found.append({"name": name, "kind": "jd", "tie": f'the JD: "{phrase}"', "url": f"jobs/{row['id']}.md"})
    slug = str(row.get("company_slug") or "")
    if slug:
        found += record_people(root, slug)
    if http is None:
        if HN_ID.search(row["id"]):
            notes.append("HN poster not read (pass --net)")
    else:
        try:
            hn = hn_poster(http, row["id"])
            ok["hn"] = True
            if hn:
                found.append(hn)
        except Exception as exc:                       # one source failing never hides the others
            notes.append(f"HN poster skipped: {exc}")
        if use_traces and slug:
            try:
                found += _author_pages(root, http, slug, notes)
                ok["site"] = True
            except Exception as exc:
                notes.append(f"author pages skipped: {exc}")
    merged: dict[str, dict[str, Any]] = {}
    for c in found:
        k = _key(c["name"])
        m = merged.setdefault(k, {"name": c["name"], "kinds": [], "ties": [], "urls": [], "notes": [], "middles": set()})
        if c["kind"] != "hn-poster" and _middle(c["name"]):
            m["middles"].add(_middle(c["name"]))
        if c["kind"] not in m["kinds"]:
            m["kinds"].append(c["kind"])
        m["ties"].append(c["tie"])
        m["urls"].append(c["url"])
        if c.get("note"):
            m["notes"].append(c["note"])
        if len(c["name"]) > len(m["name"]) and c["kind"] != "hn-poster":
            m["name"] = c["name"]
    blocked = blocked_keys(root)                       # do_not_contact holds after the merge, whatever named them
    held = sum(1 for k in merged if k in blocked)
    merged = {k: m for k, m in merged.items() if k not in blocked}
    out = sorted(merged.values(), key=lambda m: (-len(m["kinds"]), KIND_ORDER.index(m["kinds"][0]), m["name"]))
    for m in out:
        groups = {GROUP[k] for k in m["kinds"]}
        conflict = len(m.pop("middles")) > 1
        if conflict:
            m["notes"].append("the sources give different middle initials: maybe two people")
        m["confidence"] = "two sources" if len(groups) >= 2 and not conflict else "to verify"
        m["next"] = _next(m)
    read = ["the JD", "prospects", "people-watch"]
    unread = []
    if HN_ID.search(row["id"]):
        (read if ok["hn"] else unread).append("HN poster")
    (read if ok["site"] else unread).append("author pages")
    found_what = (f"{held} name{'s' if held > 1 else ''} held back (do_not_contact) from the sources read" if held
                  else "no name from the sources read")
    why = "" if out else (f"{found_what} ({', '.join(read)})" + (f"; not read: {', '.join(unread)}" if unread else ""))
    return {"id": row["id"], "company": row.get("company"), "title": row.get("title"), "candidates": out,
            "notes": notes, "why_none": why}


def _next(m: dict[str, Any]) -> str:
    kinds = m["kinds"]
    if m["confidence"] == "two sources":
        return "prospector: confirm the person's team in the owner's browser, then record the warm path"
    if kinds == ["hn-poster"]:
        return "read the HN profile's links, then confirm the real name and role"
    if "prospect" in kinds:
        return "prospector: confirm title and team in the owner's browser (is this the hiring team?)"
    if "people-watch" in kinds:
        return "read their hiring posts for this team; confirm before any note"
    if "jd" in kinds:
        return "the JD names them: prospector confirms the person in the owner's browser"
    return "confirm the author's team before any note"


NONE_NEXT = "no source names anyone: watch the company's hiring posts, or a LinkedIn look with the owner present"


def _fresh_rows(root: Path, now: datetime) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    import fetch_ats
    import fresh10
    rows = fresh10.pick(root, now)["rows"]
    records = {}
    for r in rows:
        path = Path(root) / "jobs" / f"{r['id']}.md"
        rec, body = js_common.read_record(path) if path.is_file() else ({}, "")
        rec = dict(rec)
        rec["_jd_text"] = fetch_ats.jd_text_from_body(body)
        r["company_slug"] = str(rec.get("company_slug") or "")
        records[r["id"]] = rec
    return rows, records


def report(root: Path, now: datetime, http: Any = None, use_traces: bool = False,
           rows: list[dict[str, Any]] | None = None, records: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if rows is None:
        rows, records = _fresh_rows(root, now)
    records = records or {}
    cold = [r for r in rows if not r.get("warm")]
    out = [candidates(root, r, records.get(r["id"]) or {}, http=http, use_traces=use_traces) for r in cold]
    return {"date": now.astimezone(LA).date().isoformat(), "rows": out, "warm_skipped": len(rows) - len(cold),
            "net": http is not None, "traces": use_traces, "generated": now.astimezone(LA).strftime("%Y-%m-%d %H:%M %Z")}


def _cell(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def render(res: dict[str, Any]) -> str:
    handle = lambda r: r["candidates"] and all(c["kinds"] == ["hn-poster"] for c in r["candidates"])  # noqa: E731
    named = sum(1 for r in res["rows"] if r["candidates"] and not handle(r))
    handles = sum(1 for r in res["rows"] if handle(r))
    two = sum(1 for r in res["rows"] if any(c["confidence"] == "two sources" for c in r["candidates"]))
    sources = "JD, prospects, people-watch" + (", HN poster" if res["net"] else "") + (", author pages" if res["traces"] else "")
    out = ["---", "type: report", f"title: Fresh 10 people candidates, {res['date']} (for the prospector to verify)",
           "generated_by: scripts/fresh10_people.py", f"date: {res['date']}",
           "status: candidates only; never a prospect record; nothing outward", "---", "",
           f"# Fresh 10 · people candidates · {res['date']}", "",
           f"The Fresh 10 rows with no warm path (scripts/fresh10.py, live; state/daily/{res['date']}-fresh10.md when written): {len(res['rows'])} "
           f"({res['warm_skipped']} with a warm path left out). A name for {named}; an HN handle only for {handles}; two "
           f"agreeing sources for {two}. "
           f"Sources read: {sources}. LinkedIn is never read here (its terms); a LinkedIn look is the prospector's "
           "step, in the owner's browser. A trace is a lead, not an identity: \"two sources\" means two independent groups "
           "(the JD; the department's records, prospects and people-watch counted once; HN; the company's site) "
           f"agree on the name, everything else is \"to verify\". Generated {res['generated']}. Regenerate: "
           "`python3 scripts/fresh10_people.py --net --write`.", "",
           "| role (job id) | candidate | sources | tie | where | confidence | next |", "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        role = f"{_cell(r['company'])} · {_cell(r['title'])} (`{r['id']}`)"
        if not r["candidates"]:
            out.append(f"| {role} | none found | | {_cell(r['why_none'])} | | none | {NONE_NEXT} |")
        for c in r["candidates"]:
            note = f" ({'; '.join(dict.fromkeys(c['notes']))})" if c["notes"] else ""
            out.append(f"| {role} | **{_cell(c['name'])}**{_cell(note)} | {', '.join(c['kinds'])} "
                       f"| {_cell('; '.join(c['ties']))} | {_cell(' , '.join(dict.fromkeys(c['urls'])))} | {c['confidence']} "
                       f"| {c['next']} |")
    notes = [f"- `{r['id']}`: {n}" for r in res["rows"] for n in r["notes"]]
    if notes:
        out += ["", "**Sources not read or skipped:**"] + notes
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", default=None, help="run as of the end of this day (PT); default now")
    ap.add_argument("--net", action="store_true", help="read the HN API for HN roles")
    ap.add_argument("--traces", action="store_true", help="with --net, also the company's author pages (slow)")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    now = (datetime.combine(date.fromisoformat(a.today), datetime.max.time(), LA) if a.today else datetime.now(LA))
    http = None
    if a.net:
        from fetch_ats import Http
        http = Http(min_interval=1.0, max_retry_after=people_traces.RETRY_AFTER_CAP)
    rows, records = _fresh_rows(a.root, now)
    res = report(a.root, now, http=http, use_traces=a.traces and a.net, rows=rows, records=records)
    text = render(res)
    if a.write:
        p = Path(a.root) / "state" / "daily" / f"{res['date']}-fresh10-people.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name("." + p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        print(f"wrote {p.relative_to(a.root)}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
