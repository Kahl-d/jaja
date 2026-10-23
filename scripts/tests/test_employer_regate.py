"""employer_regate.py: the real-employer gate re-run over existing applications, report only."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import employer_regate as er  # noqa: E402

JD = "<untrusted_jd>\n" + "We build agents for hospitals and need an engineer to ship evals. " * 5 + "{extra}\n</untrusted_jd>\n"


def app(root: Path, slug: str, company: str, stage: str, extra: str = "", jd: bool = True) -> Path:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    (d / "application.md").write_text(f"---\nid: {slug}\ncompany: {company}\nstage: {stage}\njob: '[[j-{slug}]]'\n---\n",
                                      encoding="utf-8")
    if jd:
        (d / "jd.md").write_text("---\njob: x\n---\n" + JD.format(extra=extra), encoding="utf-8")
    return d


def test_report_rows_and_calls(tmp_path):
    app(tmp_path, "acme-ai-202610", "Acme", "applied")
    app(tmp_path, "staffing-ml-202610", "Example Staffing", "fit_checked")
    app(tmp_path, "client-ai-202610", "Brightpath", "applied", "We are hiring on behalf of our client, a fintech.")
    app(tmp_path, "initech-services-ai-202610", "Initech Services", "discovered")
    app(tmp_path, "talent-202610", "Example Talent Network", "rejected")
    rows = {r["application"]: (r["verdict"], r["call"]) for r in er.regate(tmp_path)}
    assert rows == {"staffing-ml-202610": ("fail", "hold"), "client-ai-202610": ("fail", "note"),
                    "initech-services-ai-202610": ("review", "review"), "talent-202610": ("fail", "note")}


def test_it_changes_no_record(tmp_path):
    d = app(tmp_path, "staffing-ml-202610", "Example Staffing", "fit_checked")
    before = {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}
    er.main(["--root", str(tmp_path)])
    assert {p: p.read_bytes() for p in d.rglob("*") if p.is_file()} == before


def test_no_jd_is_named_and_the_job_body_is_the_fallback(tmp_path):
    app(tmp_path, "a-202610", "Acme", "applied", jd=False)
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "j-b-202610.md").write_text("---\nid: j\n---\n" + JD.format(extra="Our client is a bank."),
                                                     encoding="utf-8")
    app(tmp_path, "b-202610", "Brightpath", "discovered", jd=False)
    no_jd: list[str] = []
    rows = er.regate(tmp_path, no_jd)
    assert no_jd == ["a-202610"]
    assert [r["application"] for r in rows] == ["b-202610"]


def test_cli_exit_codes_and_text(tmp_path, capsys):
    app(tmp_path, "acme-ai-202610", "Acme", "applied")
    assert er.main(["--root", str(tmp_path)]) == 0
    assert "1 application(s), 0 listed" in capsys.readouterr().out
    app(tmp_path, "staffing-ml-202610", "Example Staffing", "fit_checked")
    assert er.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "| staffing-ml-202610 | Example Staffing | fit_checked | fail | hold |" in out and chr(0x2014) not in out
    assert er.main(["--root", str(tmp_path / "none")]) == 2
