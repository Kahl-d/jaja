"""render_resume.py lint (ticket 5): with no --density the lint assumes the density the base ships with (one-page, the
README's `--template classic --density one-page`), so a bare lint no longer calls base 76 broken; --density house still
measures the house design on request."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403

import render_resume as rr  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

BASE = JS_ROOT / "resumes" / "bases" / "general" / "resume.yaml"


def test_lint_with_no_flags_passes_on_the_base(capsys):
    rc = rr.main(["lint", "--input", str(BASE)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "OK lint: 0 error(s)" in out and "density one-page" in out


@pytest.mark.parametrize("template", ["classic", "inline"])
def test_lint_with_only_a_template_passes_on_the_base(template, capsys):
    rc = rr.main(["lint", "--input", str(BASE), "--template", template])
    out = capsys.readouterr().out
    assert rc == 0, out and "density one-page" in out


def test_an_explicit_house_density_still_measures_the_house_design(capsys):
    rc = rr.main(["lint", "--input", str(BASE), "--density", "house"])
    out = capsys.readouterr().out
    assert rc == 1 and "density house" in out and "bullet-too-long" in out


def test_a_template_without_the_density_falls_back_to_house(tmp_path, capsys):
    template = rr.load_template(rr.resolve_template(None))
    template.pop("densities", None)
    path = tmp_path / "t.yaml"
    import yaml
    path.write_text(yaml.safe_dump(template))
    rr.main(["lint", "--input", str(BASE), "--template", str(path)])
    assert "density house" in capsys.readouterr().out
