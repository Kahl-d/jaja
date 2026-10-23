"""One package-only folder whose jd.md frontmatter does not parse
(applications/acme-founding-ml-engineer-202610/jd.md, an unquoted ': ' in copied_from) made every
`tracker.py new` crash, --dry-run included, because _prepared_for and _package_for load every folder. Now that
folder is skipped with a warning naming it (stderr and the result) and the scan goes on."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import tracker as tr  # noqa: E402
from test_tracker_package_only import _job  # noqa: E402

BAD = "applications/acme-founding-ml-engineer-202610/jd.md"


def _bad(root):
    p = root / BAD
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\njob: '[[acme-yc-cwnavto]]'\ncopied_from: fetched from the source_url (no JD: tag jd-missing)\n"
                 "---\n# JD\n", encoding="utf-8")


def test_new_skips_the_broken_folder_and_names_it(root, capsys):
    _bad(root)
    skipped: list[str] = []
    slug, created = tr.new_application(root, _job(root), NOW, skipped=skipped)
    assert created and slug.startswith("acme-")
    assert len(skipped) == 1 and BAD in skipped[0]
    assert BAD in capsys.readouterr().err


def test_the_cli_dry_run_survives_and_reports_it(root, capsys):
    _bad(root)
    assert tr.main(["--root", str(root), "new", "--job", _job(root), "--dry-run"], now=NOW) == 0
    out = capsys.readouterr()
    assert "application would create:" in out.out and f"skipped {BAD}" in out.out and BAD in out.err


def test_a_good_package_beside_the_broken_one_is_still_adopted(root):
    _bad(root)
    jid = _job(root)
    good = root / "applications" / "acme-applied-ai-engineer-202608"
    good.mkdir(parents=True)
    (good / "jd.md").write_text(f"---\njob: '[[{jid}]]'\ncompany: Acme\ntitle: Applied AI Engineer\n---\n# JD\n")
    slug, _ = tr.new_application(root, jid, NOW, skipped=[])
    assert slug == good.name


def test_new_refuses_to_mint_a_sibling_beside_its_own_unreadable_package(root):
    import pytest
    jid = _job(root)
    slug, _ = tr.new_application(root, jid, NOW, dry_run=True, skipped=[])        # the slug the job would get
    p = root / "applications" / slug / "jd.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\njob: '[[{jid}]]'\ncopied_from: fetched (no JD: tag jd-missing)\n---\n# JD\n")
    with pytest.raises(tr.TrackerError, match="does not parse"):
        tr.new_application(root, jid, NOW, skipped=[])
    assert sorted(f.name for f in (root / "applications").iterdir()) == [slug]
