"""The house renderer carries the general base.

The owner keeps one general base resume (resumes/bases/general/, frozen and hash-pinned). These tests hold
scripts/render_resume.py to it:
  - `--template classic|inline --density one-page` renders the base text-identical (pdftotext -layout) to the frozen
    <Name>_Resume.pdf and _inline-dates.pdf, lints with 0 errors, titles the PDF "<Name>, Resume" and passes
    ats_check.py (skipped when RenderCV/typst or poppler's pdftotext is absent);
  - every template keeps the ATS invariants (template_problems) and a broken copy is refused;
  - the line estimate uses the measured width of each layout (resumes/templates/line-widths.yaml);
  - hidden-link and bullet-too-long bend only for a recorded ruling (resumes/templates/rulings.yaml or facts.yaml);
  - the sidecar contract: base_of null or `general`, never a per-lens base.
Unit tests use the FICTIONAL Riley Moreno fixtures; only the acceptance renders read the real base (read-only).
"""
from __future__ import annotations

import copy
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import render_resume as rr  # noqa: E402

from layout import JS  # noqa: E402
BASE = JS / "resumes" / "bases" / "general"
FIX = Path(__file__).resolve().parent / "fixtures" / "resume"
GOOD, EVIDENCE, FACTS = FIX / "good.yaml", FIX / "evidence.yaml", FIX / "facts.yaml"
CALIBRATION = FIX / "calibration.yaml"
AWARD_URL = "https://example.com/hack/safety-net"

needs_rendercv = pytest.mark.skipif(not rr.rendercv_available(), reason="RenderCV (with its typst) not installed")
needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler pdftotext not installed")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def base_name() -> str:
    return load(BASE / "resume.yaml")["cv"]["name"]


def base_email() -> str:
    return load(BASE / "resume.yaml")["cv"]["email"]


def frozen_pdf(suffix: str) -> Path:
    found = sorted(BASE.glob(f"*{suffix}"))
    if not found:
        pytest.skip(f"no frozen *{suffix} in the base folder")
    return found[0]


def layout_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True,
                          text=True, check=True).stdout


def ruled(**extra) -> dict:
    return {"rule": "fictional test ruling", "ruled": {"by": rr.OWNER_TOKEN, "on": "2030-01-15", "basis": "test"},
            "source": "a fictional record, row X-01", **extra}


# --------------------------------------------------------------------------- the frozen base itself
@pytest.mark.real_records
def test_the_frozen_base_still_matches_its_hashes():
    for line in (BASE / "SHA256.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == digest, name


# --------------------------------------------------------------------------- acceptance: identical renders
@needs_rendercv
@needs_pdftotext
@pytest.mark.real_records
@pytest.mark.parametrize("template, suffix", [("classic", "_Resume.pdf"), ("inline", "_Resume_inline-dates.pdf")])
def test_the_house_render_of_the_general_base_equals_the_frozen_pdf(tmp_path, template, suffix):
    import ats_check
    out = tmp_path / template
    frozen = frozen_pdf(suffix).name
    rc = rr.main(["render", "--input", str(BASE / "resume.yaml"), "--out", str(out), "--template", template,
                  "--density", "one-page", "--basename", Path(frozen).stem])
    assert rc == 0
    meta = load(out / "meta.yaml")
    assert meta["status"] == "ok" and meta["pages"] == 1 and meta["template"] == template
    assert meta["pdf_title"] == f"{base_name()}, Resume" == rr.pdf_metadata_title(out / frozen)
    assert meta["template_sha256"] == "sha256:" + hashlib.sha256(
        (BASE / f"template-{template}.yaml").read_bytes()).hexdigest()
    assert layout_text(out / frozen) == layout_text(BASE / frozen)
    assert ats_check.main(["check", "--pdf", str(out / frozen), "--contact-email", base_email(),
                           "--source", str(BASE / "resume.yaml"), "--out", str(out), "--no-pdfkit",
                           "--date", "2030-01-15"]) == 0


@pytest.mark.real_records
@pytest.mark.parametrize("template", ["classic", "inline"])
def test_the_general_base_lints_clean_on_its_own_design(template, capsys):
    rc = rr.main(["lint", "--input", str(BASE / "resume.yaml"), "--template", template, "--density", "one-page"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "OK lint: 0 error(s)" in out
    assert "NOTE labeled-link-allowed cv.sections.Projects[0].summary" in out
    # the Publications line is short enough that its long-bullet allowance does not fire; pinned so a regrowth is seen
    assert "NOTE long-bullet-allowed cv.sections.Publications[0]" not in out
    assert "~126 chars per line (measured" in out


@needs_rendercv
@needs_pdftotext
@pytest.mark.real_records
def test_a_variant_of_the_general_base_renders_with_the_house_renderer_and_passes_ats(tmp_path):
    """A variant copies the base and its templates (base README step 1) and renders with its own copy."""
    import ats_check
    vdir = tmp_path / "variant"
    vdir.mkdir()
    data = load(BASE / "resume.yaml")
    data["sidecar"] = {"lens": "agentic-systems", "base_of": "general", "job": "jobs/acme-12345.md"}
    (vdir / "resume.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    shutil.copy(BASE / "template-classic.yaml", vdir / "template-classic.yaml")
    rc = rr.main(["render", "--input", str(vdir / "resume.yaml"), "--out", str(vdir), "--template",
                  str(vdir / "template-classic.yaml"), "--density", "one-page"])
    meta = load(vdir / "meta.yaml")
    assert rc == 0 and meta["base_of"] == "general" and meta["lens"] == "agentic-systems"
    assert meta["pdf_title"] == f"{base_name()}, Resume"                      # the lens stays internal
    assert meta["template"].endswith("template-classic.yaml")
    assert ats_check.main(["check", "--pdf", str(vdir / meta["pdf"]), "--contact-email", base_email(),
                           "--source", str(vdir / "resume.yaml"), "--no-pdfkit", "--date", "2030-01-15"]) == 0


# --------------------------------------------------------------------------- templates
@pytest.mark.real_records
def test_named_templates_are_the_house_file_and_the_bases_own_files():
    assert rr.resolve_template(None) == rr.RENDERCV_TEMPLATE == rr.resolve_template("house")
    assert rr.resolve_template("classic") == BASE / "template-classic.yaml"
    assert rr.resolve_template("inline") == BASE / "template-inline.yaml"
    assert rr.template_label(BASE / "template-inline.yaml") == "inline"
    with pytest.raises(rr.UsageError, match="unknown template"):
        rr.resolve_template("fancy")


@pytest.mark.real_records
@pytest.mark.parametrize("name", ["house", "classic", "inline"])
@pytest.mark.parametrize("density", ["house", "one-page"])
def test_every_named_template_keeps_the_ats_invariants_and_validates_in_rendercv(name, density):
    _, template = rr.load_checked_template(name, density)
    assert rr.template_problems(template) == []
    if rr.rendercv_available():
        from rendercv.schema.models.design.built_in_design import built_in_design_adapter
        built_in_design_adapter.validate_python(template["design"])


@pytest.mark.parametrize("path, value, words", [
    (("design", "typography", "alignment"), "justified", "alignment"),
    (("design", "entries", "date_and_location_width"), "4cm", "one column"),
    (("design", "colors", "links"), "rgb(0, 79, 144)", "not black"),
    (("design", "header", "connections", "show_icons"), True, "show_icons"),
    (("design", "typography", "font_family"), "Times New Roman", "sans"),
    (("design", "templates", "date_range"), "START_DATE -- END_DATE", "dash"),
    (("locale", "month_abbreviations"), ["January"] * 12, "three-letter"),
])
@pytest.mark.real_records
def test_a_template_copy_that_breaks_an_ats_rule_is_refused(tmp_path, path, value, words):
    template = copy.deepcopy(rr.load_template(BASE / "template-classic.yaml"))
    node = template
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert any(words in p for p in rr.template_problems(template))
    bad = tmp_path / "template.yaml"
    bad.write_text(yaml.safe_dump(template, sort_keys=False), encoding="utf-8")
    with pytest.raises(rr.UsageError, match="breaks the ATS rules"):
        rr.load_checked_template(bad, None)


def test_a_bad_template_is_a_usage_error_on_the_cli(tmp_path, capsys):
    assert rr.main(["lint", "--input", str(GOOD), "--template", str(tmp_path / "missing.yaml")]) == 2
    assert "unknown template" in capsys.readouterr().err


# --------------------------------------------------------------------------- measured line widths
@pytest.mark.real_records
@pytest.mark.parametrize("name, density, cpl", [("house", "house", 116), ("classic", "house", 116),
                                                ("house", "one-page", 128), ("classic", "one-page", 126),
                                                ("inline", "one-page", 126)])
def test_each_template_and_density_uses_its_measured_width(name, density, cpl):
    _, template = rr.load_checked_template(name, density)
    assert rr.measured_chars_per_line(template) == cpl == rr.estimate_chars_per_line(template)
    assert rr.width_note(template) is None


def test_an_unmeasured_layout_falls_back_to_the_formula_and_says_so():
    template = copy.deepcopy(rr.load_template())
    template["design"]["page"]["left_margin"] = template["design"]["page"]["right_margin"] = "0.75in"
    assert rr.measured_chars_per_line(template) is None
    assert rr.estimate_chars_per_line(template) == rr.formula_chars_per_line(template)
    assert "no measured line width" in rr.width_note(template)


@pytest.mark.real_records
def test_every_recorded_width_names_its_measurement():
    widths = rr.load_line_widths()
    assert len(widths) == 3
    for entry in widths:
        assert entry["fit"]["low"] <= entry["chars_per_line"] <= entry["fit"]["high"]
        assert entry["fit"]["agree"] == entry["fit"]["samples"] > 40
        assert entry["measured"]["input"] == "scripts/tests/fixtures/resume/calibration.yaml"


def test_the_fit_picks_the_middle_of_the_agreeing_widths():
    samples = [("word " * 30, 2), ("word " * 12, 1)]           # 149 and 59 chars once stripped
    fit = rr.fit_chars_per_line(samples, low=40, high=200)
    assert fit["agree"] == 2 and fit["low"] == 74 and fit["high"] == 148
    assert fit["low"] <= fit["chars_per_line"] <= fit["high"]


def test_claim_line_counts_follows_wrapped_bullets_in_layout_text():
    cv = {"sections": {"Experience": [{"highlights": ["Built the thing for the team <!-- ev:a -->",
                                                      "Shipped it on time and measured it well <!-- ev:b -->"]}]}}
    text = "Experience\n• Built the thing for\n  the team\n• Shipped it on time and measured it well\n"
    got = [(c.path, n) for c, _, n in rr.claim_line_counts(text, cv)]
    assert got == [("cv.sections.Experience[0].highlights[0]", 2), ("cv.sections.Experience[0].highlights[1]", 1)]


@needs_rendercv
@needs_pdftotext
@pytest.mark.real_records
def test_measure_records_the_width_of_a_layout(tmp_path, capsys):
    widths = tmp_path / "line-widths.yaml"
    widths.write_text("# header kept\n", encoding="utf-8")
    assert rr.main(["measure", "--input", str(CALIBRATION), "--template", "classic", "--density", "one-page",
                    "--widths", str(widths), "--record", "--date", "2030-01-15"]) == 0
    assert "measured 126 chars per line" in capsys.readouterr().out
    assert widths.read_text(encoding="utf-8").startswith("# header kept\n")
    [entry] = rr.load_line_widths(widths)
    assert entry["chars_per_line"] == 126 and entry["measured"]["on"] == "2030-01-15"


# --------------------------------------------------------------------------- rulings
def _linked(url: str = AWARD_URL) -> dict:
    data = load(GOOD)
    data["cv"]["sections"]["Projects"][0]["summary"] = f"[Best Hack, Example Hacks 2030]({url}): a tool <!-- ev:tern-repo -->"
    return data


def _lint(data, facts=None, rulings=None, cpl=None):
    return rr.lint_resume(data, known_evidence=rr.load_evidence_ids(EVIDENCE), held=[], facts=facts,
                          rulings=rulings, chars_per_line=cpl)


def test_a_labeled_link_needs_a_ruling_naming_its_url():
    assert "hidden-link" in [e.code for e in _lint(_linked()).errors]
    rulings = {"rules": [ruled(labeled_links=[{"url": AWARD_URL, "label": "award"}])]}
    report = _lint(_linked(), rulings=rulings)
    assert report.errors == []
    [note] = [n for n in report.notes if n.code == "labeled-link-allowed"]
    assert "rulings.yaml rules[0].labeled_links[0]" in note.message and "2030-01-15" in note.message
    other = _lint(_linked("https://example.com/elsewhere"), rulings=rulings)
    assert [e.code for e in other.errors] == ["hidden-link"]


def test_a_labeled_link_ruling_in_facts_yaml_counts_too():
    entry = ruled(labeled_links=[{"url": AWARD_URL}])
    del entry["source"]                                         # facts.yaml entries carry their basis instead
    assert _lint(_linked(), facts={"rules": [entry]}).errors == []


def test_a_link_in_facts_and_rulings_is_allowed_by_the_record_and_the_twin_is_a_warning():
    # facts.yaml `rules:` is the record; a rulings.yaml copy of the same URL is flagged for deletion
    facts_entry = ruled(labeled_links=[{"url": AWARD_URL, "label": "award"}])
    del facts_entry["source"]
    rulings = {"rules": [ruled(labeled_links=[{"url": AWARD_URL + "/", "label": "award"}])]}
    report = _lint(_linked(), facts={"rules": [facts_entry]}, rulings=rulings)
    assert report.errors == []
    [warn] = [w for w in report.warnings if w.code == "link-rule-duplicate"]
    assert warn.path == "rulings.yaml rules[0].labeled_links[0]" and "delete this rulings.yaml entry" in warn.message
    [note] = [n for n in report.notes if n.code == "labeled-link-allowed"]
    assert "facts.yaml rules[0].labeled_links[0]" in note.message


def test_a_rulings_link_alone_still_counts_until_the_record_has_it():
    report = _lint(_linked(), facts={"rules": [{**ruled(), "evidence": ["ev-x"]}]},
                   rulings={"rules": [ruled(labeled_links=[{"url": AWARD_URL}])]})
    assert report.errors == [] and not [w for w in report.warnings if w.code == "link-rule-duplicate"]


@pytest.mark.real_records
def test_the_recorded_link_rulings_have_no_errors():
    facts, doc = rr.load_facts(rr.DEFAULT_FACTS), load(rr.DEFAULT_RULINGS)
    allowed, problems = rr.link_rules(facts, doc)
    assert [p for p in problems if p.severity == "error"] == []


@pytest.mark.parametrize("entry, words", [
    (ruled(labeled_links=[{"url": "devpost.com/x"}]), "http(s)"),
    (ruled(labeled_links=[{"url": AWARD_URL, "why": "x"}]), "unknown key"),
    (ruled(labeled_links=[]), "non-empty"),
    ({**ruled(labeled_links=[{"url": AWARD_URL}]), "ruled": {"by": "agent", "on": "2030-01-15", "basis": "x"}},
     "only the owner"),
    ({**ruled(labeled_links=[{"url": AWARD_URL}]), "source": ""}, "source is empty"),
])
def test_a_malformed_link_ruling_is_an_error_and_allows_nothing(entry, words):
    report = _lint(_linked(), rulings={"rules": [entry]})
    codes = [e.code for e in report.errors]
    assert "link-rule" in codes and "hidden-link" in codes
    assert any(words in e.message for e in report.errors if e.code == "link-rule")


def test_a_rulings_long_bullet_covers_a_paragraph_but_never_a_summary():
    data = load(GOOD)
    long_text = " ".join(["measured"] * 12)                     # 3 lines at 40 chars
    data["cv"]["sections"]["Publications"] = [f"{long_text} <!-- ev:pub-triage -->"]
    data["cv"]["sections"]["Experience"][0]["summary"] = f"{long_text} <!-- ev:pub-triage -->"
    rulings = {"rules": [ruled(long_bullets=[{"evidence": ["pub-triage"], "max_lines": 3}])]}
    report = _lint(data, rulings=rulings, cpl=40)
    too_long = [e.path for e in report.errors if e.code == "bullet-too-long"]
    assert "cv.sections.Publications[0]" not in too_long
    assert "cv.sections.Experience[0].summary" in too_long
    assert any(n.path == "cv.sections.Publications[0]" and "rulings.yaml rules[0]" in n.message for n in report.notes)


def test_the_same_evidence_set_in_facts_and_rulings_is_ambiguous():
    item = {"evidence": ["pub-triage"], "max_lines": 3}
    facts_entry = ruled(long_bullets=[item])
    report = _lint(load(GOOD), facts={"rules": [facts_entry]}, rulings={"rules": [ruled(long_bullets=[item])]})
    assert any(e.code == "long-bullet-rule" and "listed twice" in e.message for e in report.errors)


@pytest.mark.real_records
def test_the_recorded_rulings_file_is_well_formed_and_names_its_sources():
    doc = load(rr.DEFAULT_RULINGS)
    for entry in doc["rules"]:
        assert entry["source"] and entry["ruled"]["by"] == rr.OWNER_TOKEN
    assert rr.link_rules(None, doc)[1] == [] and rr.long_bullet_rules(None, doc)[1] == []
    allowed, problems = rr.link_rules(load(rr.DEFAULT_FACTS), doc)
    assert problems == []


# --------------------------------------------------------------------------- sidecar and header
@pytest.mark.parametrize("sidecar", [
    None,
    {"lens": None, "base_of": None, "job": None},
    {"lens": "agentic-systems", "base_of": "general", "job": "jobs/acme-12345.md"},
    {"lens": "applied-ml", "base_of": "general", "job": None},
])
def test_base_of_general_and_a_base_are_valid_sidecars(sidecar):
    report = rr.LintReport()
    rr.check_sidecar(sidecar, report)
    assert report.errors == []
    assert "general" in rr.base_names()


@pytest.mark.parametrize("sidecar, code, words", [
    ({"lens": "applied-ml", "base_of": "applied-ml", "job": None}, "unknown-base", "base_of: general"),
    ({"lens": "applied-ml", "base_of": "general", "job": None, "template": "x"}, "bad-sidecar", "unknown key"),
    ({"lens": "Applied ML", "base_of": None, "job": None}, "bad-sidecar", "kebab"),
    ({"lens": None, "base_of": "general", "job": "acme-12345"}, "bad-sidecar", "jobs/<id>.md"),
    (["lens"], "bad-sidecar", "mapping"),
])
def test_a_per_lens_base_or_a_malformed_sidecar_is_an_error(sidecar, code, words):
    data = load(GOOD)
    data["sidecar"] = sidecar
    report = _lint(data)
    assert any(e.code == code and words in e.message for e in report.errors), [str(e) for e in report.errors]


def test_custom_connections_show_their_placeholder_in_the_docx_header():
    cv = {"name": "Riley Moreno", "location": "Seattle, WA",
          "custom_connections": [{"placeholder": "[+1 (000) 555-0100](tel:+10005550100)", "url": None,
                                  "fontawesome_icon": "phone"}],
          "email": "riley.moreno@example.com"}
    assert rr.connections(cv) == [("Seattle, WA", None), ("+1 (000) 555-0100", "tel:+10005550100"),
                                  ("riley.moreno@example.com", "mailto:riley.moreno@example.com")]
    assert "Seattle, WA | +1 (000) 555-0100 | riley.moreno@example.com" in rr.resume_markdown({"cv": cv})


# --------------------------------------------------------------------------- the estimate against the frozen page
@needs_pdftotext
@pytest.mark.real_records
def test_every_base_claim_estimates_the_lines_it_renders_in_the_frozen_pdf():
    """At classic one-page (126 chars a line) each bullet of the general base estimates exactly the lines it takes in
    the frozen PDF; pinned so a change either way is seen. An over-estimate is the safe direction (the fit check can
    only flag it early), but none is expected on the current base."""
    _, template = rr.load_checked_template("classic", "one-page")
    cpl = rr.estimate_chars_per_line(template)
    assert cpl == 126
    cv = load(BASE / "resume.yaml")["cv"]
    counts = {c.path: (c, n) for c, _, n in rr.claim_line_counts(layout_text(frozen_pdf("_Resume.pdf")), cv)}
    checked = {path: n for path, (c, n) in counts.items() if c.kind in rr.FIT_KINDS}
    assert None not in checked.values() and checked
    for path, n in checked.items():
        estimate = rr.estimate_lines(rr.parse_evidence(counts[path][0].text)[0], cpl)
        assert n == estimate, path
