"""Tests for scripts/page_layout_check.py: the rendered page is measured, not only its words.

Every PDF here is written by hand inside the test (Helvetica text placed at exact positions, no renderer), so each
layout defect is built on purpose: a page that stops short of its bottom margin, headers with no more space above them
than an ordinary bullet, a line that runs past the right margin, a second page, a page packed too densely. The text is
fictional. poppler (pdftotext, pdftoppm) must be installed, as for ats_check.py.
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import page_layout_check as plc  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("pdftotext") and shutil.which("pdftoppm")),
                                reason="poppler (pdftotext, pdftoppm) not installed")

W, H, M = 612.0, 792.0, 36.0                  # US Letter, 0.5in margins on every side
BULLET = "\x95"                               # WinAnsi bullet; pdftotext reads it as U+2022
HEADERS = ("Experience", "Education", "Skills", "Projects", "Publications")
BODY = "Built a retrieval service for a fictional library and tested it every day"


# --------------------------------------------------------------------------- a hand-written PDF
def make_pdf(pages: list[list[tuple[float, float, float, str]]]) -> bytes:
    """pages: per page, (x, baseline measured from the top, font size, text) for every line."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    parent = add(b"")
    kids = []
    for lines in pages:
        ops = []
        for x, top, size, text in lines:
            t = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {size} Tf {x:.2f} {H - top:.2f} Td ({t}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {parent} 0 R /MediaBox [0 0 {W:.0f} {H:.0f}] "
                        f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>".encode()))
    objs[parent - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    catalog = add(f"<< /Type /Catalog /Pages {parent} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    return bytes(out)


def resume_page(end_at: float = H - M - 2, header_space: float = 12.0, pitch: float = 12.0, size: float = 10.0,
                per_section: int = 9, long_line: bool = False) -> list[tuple[float, float, float, str]]:
    """A one-page resume: name, contact line, then sections of bullets until the next baseline would pass end_at.
    header_space is the extra space above each section header beyond the body pitch."""
    lines = [(M, 50.0, 18.0, "Rowan Quill Example"),
             (M, 66.0, size, "Oakland, CA | rowan.example@example.com | github.com/rowan-example")]
    y, section, n = 66.0, 0, per_section
    while True:
        if n == per_section:
            nxt = y + pitch + header_space + 2
            if nxt + pitch > end_at:
                break
            y = nxt
            lines.append((M, y, 12.0, HEADERS[section % len(HEADERS)]))
            section, n = section + 1, 0
            continue
        if y + pitch > end_at:
            break
        y += pitch
        n += 1
        lines.append((M, y, size, f"{BULLET} {BODY}"))
    if long_line:
        x, top, s, _ = lines[5]
        lines[5] = (x, top, s, f"{BULLET} " + "an overlong line that keeps going " * 5)
    return lines


def write_pdf(folder: Path, pages: list, name: str = "Rowan_Quill_Example_Resume.pdf") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(make_pdf(pages))
    return path


def run(pdf: Path, *extra: str, capsys=None) -> tuple[int, dict]:
    code = plc.main([str(pdf), "--json", *extra])
    out = capsys.readouterr().out if capsys else ""
    return code, json.loads(out) if out.strip().startswith("{") else {}


def check(report: dict, name: str) -> dict:
    return next(c for c in report["checks"] if c["name"] == name)


# --------------------------------------------------------------------------- the verdicts
def test_a_page_filled_to_its_margin_with_spaced_headers_passes(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page()])
    code, rep = run(pdf, capsys=capsys)
    assert code == 0, rep.get("failures")
    assert rep["verdict"] == "PASS" and rep["passed"] is True
    assert rep["pages"] == 1
    assert abs(rep["metrics"]["bottom_slack_pct"]) <= plc.BOTTOM_SLACK_FAIL_PCT
    assert rep["metrics"]["header_gap_ratio"] >= plc.HEADER_GAP_MIN_RATIO
    assert check(rep, "hierarchy")["status"] == "pass"
    for name in ("page_count", "bottom_whitespace", "header_gaps", "right_margin", "margins", "density"):
        assert check(rep, name)["status"] in ("pass", "warn"), name


def test_a_page_that_stops_short_fails_bottom_whitespace(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page(end_at=H * 0.80)])
    code, rep = run(pdf, capsys=capsys)
    assert code == 1 and rep["verdict"] == "FAIL"
    bottom = check(rep, "bottom_whitespace")
    assert bottom["status"] == "fail"
    assert rep["metrics"]["bottom_slack_pct"] > plc.BOTTOM_SLACK_FAIL_PCT
    assert rep["metrics"]["bottom_whitespace_pct"] > rep["metrics"]["bottom_slack_pct"]   # the raw band includes the margin
    assert any("bottom" in f for f in rep["failures"])


def test_headers_with_no_more_space_than_a_bullet_fail_as_cluttered(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page(header_space=-2.0)])
    code, rep = run(pdf, capsys=capsys)
    assert code == 1
    gaps = check(rep, "header_gaps")
    assert gaps["status"] == "fail"
    assert any(h in gaps["detail"] for h in HEADERS)                     # the tightest header is named
    assert rep["metrics"]["header_gap_ratio"] < plc.HEADER_GAP_MIN_RATIO
    assert check(rep, "bottom_whitespace")["status"] == "pass"          # only the clutter is wrong on this page
    assert check(rep, "hierarchy")["status"] == "warn"                  # bullets now carry more air than headers


def test_text_running_past_the_right_margin_fails(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page(long_line=True)])
    code, rep = run(pdf, capsys=capsys)
    assert code == 1
    right = check(rep, "right_margin")
    assert right["status"] == "fail" and "overlong" in right["detail"]


def test_a_second_page_fails_unless_the_variant_allows_two(tmp_path, capsys):
    full = resume_page()
    pdf = write_pdf(tmp_path / "v", [full, full[:6]])
    code, rep = run(pdf, capsys=capsys)
    assert code == 1 and check(rep, "page_count")["status"] == "fail" and rep["pages"] == 2
    code, rep = run(pdf, "--max-pages", "2", capsys=capsys)
    assert check(rep, "page_count")["status"] == "pass" and rep["max_pages"] == 2


def test_meta_yaml_max_pages_beside_or_above_send_sets_the_page_budget(tmp_path, capsys):
    full = resume_page()
    vdir = tmp_path / "cv-variant"
    vdir.mkdir()
    (vdir / "meta.yaml").write_text("pages: 2\nmax_pages: 2\nstatus: ok\n", encoding="utf-8")
    pdf = write_pdf(vdir / "send", [full, full[:6]], name="Rowan_Quill_Example_CV.pdf")
    _code, rep = run(pdf, capsys=capsys)
    assert rep["max_pages"] == 2 and check(rep, "page_count")["status"] == "pass"


def test_a_packed_page_warns_on_density_but_still_passes(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page(pitch=8.5, size=7.5, header_space=10.0, per_section=14)])
    code, rep = run(pdf, capsys=capsys)
    assert check(rep, "density")["status"] == "warn"
    assert rep["metrics"]["lines_per_inch"] > plc.DENSITY_WARN_LPI
    assert code == 0 and rep["warnings"]


# --------------------------------------------------------------------------- what it writes
def test_the_png_and_the_report_land_beside_the_pdf(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page()])
    _code, rep = run(pdf, capsys=capsys)
    png = tmp_path / "v" / "page-1.png"
    assert Path(rep["png"]) == png and png.is_file()
    data = png.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (round(W / 72 * plc.DPI), round(H / 72 * plc.DPI))
    saved = json.loads((tmp_path / "v" / plc.REPORT_NAME).read_text())
    assert saved["pdf"] == pdf.name and saved["pdf_sha256"] == plc.sha256_file(pdf)
    assert saved["verdict"] == "PASS"


def test_out_puts_the_png_and_report_in_another_folder(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page()])
    out = tmp_path / "scratch"
    _code, rep = run(pdf, "--out", str(out), capsys=capsys)
    assert (out / "page-1.png").is_file() and (out / plc.REPORT_NAME).is_file()
    assert not (tmp_path / "v" / "page-1.png").exists()


def test_the_png_encoder_round_trips_a_grey_image():
    pixels = bytes([0, 128, 255, 64, 32, 16])
    data = plc.png_grey(3, 2, pixels)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert raw == b"\x00" + pixels[:3] + b"\x00" + pixels[3:]


def test_the_human_report_is_short_and_names_the_verdict(tmp_path, capsys):
    pdf = write_pdf(tmp_path / "v", [resume_page(end_at=H * 0.80)])
    assert plc.main([str(pdf)]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("FAIL")
    assert "bottom" in out and "page-1.png" in out
    assert len(out.splitlines()) <= 20


# --------------------------------------------------------------------------- usage
def test_a_missing_pdf_is_a_usage_error(tmp_path, capsys):
    assert plc.main([str(tmp_path / "nope.pdf")]) == 2
    assert "not found" in capsys.readouterr().err


def test_headers_are_found_by_their_section_names():
    assert plc.is_header("Experience") and plc.is_header("EDUCATION") and plc.is_header("Work Experience")
    assert not plc.is_header("Founding Engineer | Feb 2026 - Present")
    assert not plc.is_header("• Built the Experience service")


def test_the_gap_ratio_needs_headers_and_bullets():
    assert plc.gap_ratio([], [2.0, 2.0]) is None
    assert plc.gap_ratio([4.0, 6.0], []) is None
    assert plc.gap_ratio([4.0, 6.0], [2.0, 2.0, 3.0]) == pytest.approx(2.0)        # the tightest header vs the median
