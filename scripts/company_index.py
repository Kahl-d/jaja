#!/usr/bin/env python3
"""company_index.py - one company slug per company, so every join finds the dossier, the people and the roles.

Without one rule a company ends up with several slugs (say `acme` for the dossier, `acme-motors` for the jobs and
applications, `acme-motors-company` for the prospect) when different scripts make slugs different ways, so the
per-role hubs miss the dossier, the legal check and the referrer. The company's identity has one source:

  1. the dossier, companies/<slug>.md: its `slug`, its `name` and an optional `aliases:` list (and its
     careers_url or website for email domains). company-researcher sets them in research-company step 3.
  2. the watchlist, targets/companies.yaml: `slug`, `name`, `aliases`, `h1b_names`, careers_url, domain.
  3. otherwise the derived slug, slugify(normalize_company(name)): "Helio Systems, Inc." and "Helio Systems"
     are one slug.

Within 1 and then 2, the tiers are the ones prospects.py used for its watchlist (moved here): an exact slug,
normalized name or alias; the sender's email domain against the company's own domains (ATS, job-board and
free-mail hosts ignored); and, only with fuzzy=True, the tolerant company-name match and the company's name
extending the given one ("Harborline" -> "Harborline Robotics"). The first tier with exactly one hit wins;
two or more hits in a tier mean no guess. The tolerant tiers are for people resolved from mail
(prospects.py); record joins never use them, because they merge different employers: a job board's "Violet"
would match a company named "VioletLabs".

    CompanyIndex.load(js_root).resolve(name_or_slug, emails=None, fuzzy=False)
        -> (slug, "dossier"|"watchlist"|"derived")
    canonical(name_or_slug, js_root) -> slug                      (cached per root)
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402


def extends_company(full: str | None, short: str | None) -> bool:
    """True when the leading words of `full` spell `short`: "Harborline Robotics" extends "Harborline"
    (a domain label), so both name one employer and the longer, signed name is the one to keep."""
    tokens = jc.normalize_company(full or "").split()
    keys = cu.company_keys(short)
    if len(tokens) < 2 or not keys:
        return False
    return any(len(prefix) >= 3 and prefix in keys for prefix in ("".join(tokens[:i]) for i in range(1, len(tokens))))


def derive(name: str) -> str:
    return jc.slugify(jc.normalize_company(name)) or jc.slugify(name)


def _host(url: Any) -> str | None:
    from urllib.parse import urlparse
    import mail_classify as mc
    text = str(url or "").strip()
    if not text:
        return None
    host = (urlparse(text if "//" in text else f"//{text}").hostname or "").lower()
    reg = cu.registrable_domain(host)
    if not reg or cu.is_vendor_domain(host) or cu.is_free_mail(host) or reg in mc.ATS_DOMAINS \
            or reg in mc.JOB_BOARD_DOMAINS:
        return None
    return reg


def _entry(c: dict[str, Any], source: str) -> dict[str, Any]:
    names = [str(c.get("name") or "")]
    for key in ("aliases", "h1b_names", "also_known_as"):
        extra = c.get(key) or []
        names += [str(x) for x in (extra if isinstance(extra, list) else [extra])]
    names = [n for n in names if n.strip()]
    domains = set()
    for url in [c.get("careers_url"), c.get("website"), c.get("domain"), *(c.get("domains") or [])]:
        host = _host(url)
        if host:
            domains.add(host)
    slug = str(c.get("slug") or derive(c.get("name") or ""))
    return {"slug": slug, "names": names, "norm": {jc.normalize_company(n) for n in names} | {jc.normalize_company(slug)},
            "slugs": {slug} | {jc.slugify(n) for n in names}, "domains": domains, "source": source}


class CompanyIndex:
    def __init__(self, dossiers: Iterable[dict[str, Any]] = (), watchlist: Iterable[dict[str, Any]] = ()) -> None:
        self.groups = [[_entry(c, "dossier") for c in dossiers], [_entry(c, "watchlist") for c in watchlist]]

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [e for g in self.groups for e in g]

    @classmethod
    def load(cls, js_root: Path | str) -> "CompanyIndex":
        root = Path(js_root)
        dossiers = [meta for _, meta, _ in jc.iter_records(root / "companies")] \
            if (root / "companies").is_dir() else []
        watch: list[dict[str, Any]] = []
        if (root / "targets" / "companies.yaml").is_file():
            from fetch_ats import load_companies
            watch = load_companies(root)
        return cls(dossiers, watch)

    def resolve(self, company: str | None, emails: Iterable[str] | None = None,
                fuzzy: bool = False) -> tuple[str | None, str | None]:
        if not company:
            return None, None
        company = str(company)
        slugged = jc.slugify(company)
        norm = jc.normalize_company(company)
        mail_domains = {cu.registrable_domain(cu.email_domain(e)) for e in emails or [] if e}
        tiers = (
            lambda e: slugged in e["slugs"] or norm in e["norm"],
            lambda e: bool(mail_domains & e["domains"]),
            lambda e: any(cu.companies_match(company, n) for n in e["names"]),
            lambda e: any(extends_company(n, company) for n in e["names"]),
        )[:4 if fuzzy else 2]
        for group in self.groups:
            for test in tiers:
                hits = {e["slug"] for e in group if test(e)}
                if len(hits) == 1:
                    return hits.pop(), group[0]["source"]
                if len(hits) > 1:
                    break
        return derive(company), "derived"


@functools.lru_cache(maxsize=16)
def _index(root: str) -> CompanyIndex:
    return CompanyIndex.load(Path(root))


def canonical(value: str | None, js_root: Path | str | None = None) -> str | None:
    """The one slug for a company name or slug (cached per root: a run sees one index)."""
    return _index(str(Path(js_root or jc.JS_ROOT).resolve())).resolve(value)[0]
