"""company_slugs.py: rewrite stored company_slug fields to the one canonical slug, dry run by default."""
from __future__ import annotations

import sys

import pytest
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import company_slugs as cs  # noqa: E402

APP = "acme-motor-ai-engineer-202609"


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def acme(tmp_path: Path) -> Path:
    root = tmp_path / "js"
    w(root / "companies" / "acme.md", '---\ntype: company\nslug: acme\nname: "Acme Motor Company"\n---\n')
    w(root / "jobs" / "acme-motor-man-1.md", """
        ---
        type: job
        id: acme-motor-man-1
        company: Acme Motor Company
        company_slug: acme-motor
        title: AI Engineer
        ---
        body keeps  its   spacing
        """)
    w(root / "applications" / APP / "application.md", """
        ---
        id: acme-motor-ai-engineer-202609
        company: Acme Motor Company
        company_slug: acme-motor
        ---
        # AI Engineer
        """)
    w(root / "prospects" / "sam-sample.md", """
        ---
        id: sam-sample
        company: Acme Motor Company
        company_slug: acme-motor-company
        company_slug_source: derived
        ---
        # Sam
        """)
    w(root / "jobs" / "amc-hn-2.md", "---\nid: amc-hn-2\ncompany: Acme Motor Company\ncompany_slug: amc\n---\n")
    w(root / "jobs-pending" / "globex-1.md", "---\nid: globex-1\ncompany: Globex\ncompany_slug: globex\n---\n")
    return root


def test_dry_run_lists_every_change_and_writes_nothing(tmp_path, capsys):
    root = acme(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*.md")}
    assert cs.main(["migrate", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "jobs/acme-motor-man-1.md: company_slug acme-motor -> acme" in out
    assert f"applications/{APP}/application.md: company_slug acme-motor -> acme" in out
    assert "prospects/sam-sample.md: company_slug acme-motor-company -> acme" in out
    assert "jobs/amc-hn-2.md: company_slug amc -> acme" in out               # the name reaches the dossier
    assert "globex" not in out
    assert "dry run: 4 records would change; nothing written" in out
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == before


def test_apply_rewrites_only_the_slug_lines(tmp_path, capsys):
    root = acme(tmp_path)
    job = root / "jobs" / "acme-motor-man-1.md"
    before = job.read_text()
    assert cs.main(["migrate", "--root", str(root), "--apply"]) == 0
    assert job.read_text() == before.replace("company_slug: acme-motor\n", "company_slug: acme\n")
    prospect = (root / "prospects" / "sam-sample.md").read_text()
    assert "company_slug: acme\n" in prospect and "company_slug_source: dossier\n" in prospect
    assert (root / "applications" / APP).is_dir()                     # ids and folder names never move
    assert "4 records changed" in capsys.readouterr().out
    assert cs.main(["migrate", "--root", str(root)]) == 0              # idempotent
    assert "dry run: 0 records would change" in capsys.readouterr().out


def test_check_fails_on_drift_and_on_a_dossier_the_watchlist_spells_differently(tmp_path, capsys):
    root = acme(tmp_path)
    assert cs.main(["check", "--root", str(root)]) == 1
    assert "drift: 4 records" in capsys.readouterr().out
    cs.main(["migrate", "--root", str(root), "--apply"])
    capsys.readouterr()
    assert cs.main(["check", "--root", str(root)]) == 0
    w(root / "targets" / "companies.yaml", 'companies:\n  - {name: "Acme Motor Company", slug: acme-motor}\n')
    assert cs.main(["check", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "conflict: watchlist slug acme-motor names the dossier companies/acme.md (slug acme)" in out


def test_an_explicit_dry_run_flag_is_accepted_and_refused_with_apply(tmp_path, capsys):
    root = acme(tmp_path)
    assert cs.main(["migrate", "--root", str(root), "--dry-run"]) == 0
    assert "dry run: 4 records would change; nothing written" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cs.main(["migrate", "--root", str(root), "--dry-run", "--apply"])
