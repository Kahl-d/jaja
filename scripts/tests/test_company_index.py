"""company_index.py: one company slug per company, the dossier first ."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import company_index as ci  # noqa: E402


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def root_with(tmp_path: Path) -> Path:
    root = tmp_path / "js"
    w(root / "companies" / "orion.md", """
        ---
        type: company
        slug: orion
        name: "Orion Motor Company"
        last_researched: 2026-09-27
        ---
        # Orion
        """)
    w(root / "companies" / "brightwave.md", """
        ---
        type: company
        slug: brightwave
        name: "Brightwave (legal entity unconfirmed)"
        aliases: [Brightwave, "Brightwave, Inc.", Brightwave-AI, "Quillfield Labs"]
        careers_url: https://brightwave.test/careers
        last_researched: 2026-09-26
        ---
        """)
    w(root / "targets" / "companies.yaml", """
        companies:
          - name: "Northwind"
            slug: northwind
            careers_url: "https://www.northwind.example/careers"
          - name: "Orion Motor Company"
            slug: orion-motor-watch
          - name: "VioletLabs"
            slug: violetlabs
            careers_url: "https://violetlabs.example/careers"
        """)
    return root


def test_every_orion_spelling_resolves_to_the_dossier_slug(tmp_path):
    idx = ci.CompanyIndex.load(root_with(tmp_path))
    for value in ("Orion Motor Company", "orion-motor", "orion-motor-company", "orion", "ORION MOTOR CO"):
        assert idx.resolve(value) == ("orion", "dossier"), value


def test_a_dossier_alias_resolves_and_the_dossier_beats_the_watchlist(tmp_path):
    idx = ci.CompanyIndex.load(root_with(tmp_path))
    assert idx.resolve("Brightwave, Inc.") == ("brightwave", "dossier")
    assert idx.resolve("brightwave-ai") == ("brightwave", "dossier")
    assert idx.resolve("Quillfield Labs") == ("brightwave", "dossier")   # an alias unlike the name
    assert idx.resolve("Northwind") == ("northwind", "watchlist")


def test_an_unknown_company_derives_one_slug_whatever_the_spelling(tmp_path):
    idx = ci.CompanyIndex.load(root_with(tmp_path))
    assert idx.resolve("Harbor Systems, Inc.") == ("harbor-systems", "derived")
    assert idx.resolve("Harbor Systems") == ("harbor-systems", "derived")
    assert idx.resolve(None) == (None, None)


def test_email_domain_resolves_when_the_name_is_unhelpful(tmp_path):
    idx = ci.CompanyIndex.load(root_with(tmp_path))
    assert idx.resolve("AP", ["jane@brightwave.test"]) == ("brightwave", "dossier")


def test_canonical_is_a_cached_one_call_helper(tmp_path):
    root = root_with(tmp_path)
    assert ci.canonical("orion-motor-company", root) == "orion"
    assert ci.canonical("orion-motor", root) == "orion"


def test_a_different_company_with_a_similar_name_is_not_merged(tmp_path):
    # a job board's "Violet" is not VioletLabs, but the tolerant name match says they match; a joined record would inherit VioletLabs' dossier and H-1B facts
    idx = ci.CompanyIndex.load(root_with(tmp_path))
    assert idx.resolve("Violet") == ("violet", "derived")
    assert idx.resolve("violet") == ("violet", "derived")
    assert idx.resolve("VioletLabs") == ("violetlabs", "watchlist")


def test_the_tolerant_tiers_are_opt_in_for_mail_senders(tmp_path):
    # prospects.py resolves people from mail, where "Harborline" (a domain label) names "Harborline Robotics"
    idx = ci.CompanyIndex([], [{"name": "Harborline Robotics", "slug": "harborline-robotics"}])
    assert idx.resolve("Harborline") == ("harborline", "derived")
    assert idx.resolve("Harborline", fuzzy=True) == ("harborline-robotics", "watchlist")
