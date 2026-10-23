"""h1b_lookup.py: sponsorship enrichment from cached USCIS H-1B hub + DOL LCA data.

fixtures/disc/h1b_synth/h1b_cache/uscis_h1b_fy20{24,25,26}.csv hold SYNTHETIC employer-level approvals in the
USCIS hub layout, including look-alike employers (STELLAR IT SYSTEMS, ORCHARD AMERICAN GROUP, OPENLABINTERFACE).
lca_h1b_sample.csv is SYNTHETIC in the DOL disclosure column layout. uscis_hub_long_fy2026_sample.csv and
h1b_datahubexport-2023_sample.csv are synthetic rows in the layout of the real exports."""
from __future__ import annotations

import csv
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FIXTURES, NOW, FakeHttp

import h1b_lookup as hb  # noqa: E402
from fetch_ats import FetchError  # noqa: E402

SYNTH = FIXTURES / "h1b_synth"
CACHE = SYNTH / "h1b_cache"
FYS = [2024, 2025, 2026]


@pytest.fixture(scope="module")
def index():
    return hb.load_index(CACHE)


def test_fiscal_years_follow_the_october_start():
    assert hb.fiscal_years(date(2026, 9, 24)) == [2024, 2025, 2026]
    assert hb.fiscal_years(date(2026, 10, 5)) == [2025, 2026, 2027]


@pytest.mark.parametrize("name,keys", [
    ("ACMEAI PBC D B A ACMEAI INC", ["acmeai"]),
    ("OPENLAB L L C", ["openlab"]),
    ("Stellar Platforms, Inc.", ["stellar platforms"]),
    ("ACMEAI, PBC", ["acmeai"]),
])
def test_employer_keys(name, keys):
    assert hb.employer_keys(name) == keys


@pytest.mark.parametrize("company,total", [
    ("Acmeai", 254), ("OpenLab", 504), ("Stellar", 13700), ("Orchard", 13134),
    ("NORTHWIND", 4740), ("Vandelay", 667), ("Skyline AI", 125), ("Umbrella AI", 6),
])
def test_lookup_sums_three_fiscal_years_without_look_alikes(index, company, total):
    res = hb.lookup(index, company, fys=FYS)
    assert res["h1b_approvals_3y"] == total
    assert res["sponsorship"] == "yes"


def test_lookup_reports_per_year_and_matched_names(index):
    res = hb.lookup(index, "OpenLab", fys=FYS)
    assert res["h1b_by_fy"] == {2024: 75, 2025: 125, 2026: 304}
    assert set(res["employer_matches"]) == {"OPENLAB L L C", "OPENLAB OPCO LLC", "OPENLAB LLC"}
    assert res["sponsorship_evidence"].startswith("USCIS H-1B hub: 504 approvals FY2024-FY2026")


def test_unknown_employer_is_unknown_not_no(index):
    res = hb.lookup(index, "Tiny Stealth Startup", fys=FYS)
    assert res["sponsorship"] == "unknown" and res["h1b_approvals_3y"] == 0
    assert res["lca_count"] == 0 and res["median_lca_wage"] is None and res["wage_level_est"] == "unknown"


def test_aliases_extend_the_match(index):
    res = hb.lookup(index, "Orchard", fys=FYS, aliases=["ORCHARD PAYMENTS SERVICES LLC"])
    assert res["h1b_approvals_3y"] == 13140


def test_archive_format_is_read(tmp_path):
    shutil.copy(SYNTH / "h1b_datahubexport-2023_sample.csv", tmp_path / "h1b_datahubexport-2023.csv")
    idx = hb.load_index(tmp_path)
    assert hb.lookup(idx, "Orchard", fys=[2023])["h1b_approvals_3y"] == 1825
    assert hb.lookup(idx, "Stellar", fys=[2023])["h1b_approvals_3y"] == 1537
    assert hb.lookup(idx, "Vandelay", fys=[2023])["h1b_approvals_3y"] == 81


def test_lca_stats_count_certified_h1b_only(index):
    res = hb.lookup(index, "Acmeai", fys=FYS)
    assert res["lca_count"] == 5
    assert res["median_lca_wage"] == 300000
    assert res["wage_level_est"] == "III"
    assert res["lca_basis"] == "employer"


def test_lca_stats_prefer_rows_with_the_same_title(index):
    res = hb.lookup(index, "Acmeai", fys=FYS, title="Research Engineer, Interpretability")
    assert res["lca_basis"] == "title"
    assert res["median_lca_wage"] == 312000 and res["wage_level_est"] == "III"
    assert res["lca_count"] == 5


def test_wage_level_tie_resolves_to_the_lower_level(tmp_path):
    header = (CACHE / "lca_h1b_sample.csv").read_text().splitlines()[0]
    rows = [f"I-{i},Certified,2026-01-05,2026-01-12,H-1B,ML Engineer,15-1252.00,Software Developers,Y,"
            f"TIE CO INC,Austin,TX,Austin,TX,200000,,Year,150000,Year,{lv}" for i, lv in
            enumerate(["III", "III", "II", "II"])]
    (tmp_path / "lca_tie.csv").write_text("\n".join([header, *rows]) + "\n")
    assert hb.lookup(hb.load_index(tmp_path), "Tie Co", fys=FYS)["wage_level_est"] == "II"


def test_enrich_sets_fields_and_keeps_jd_level_evidence(index):
    anth = {"id": "acmeai-1", "company": "Acmeai", "company_slug": "acmeai", "title": "Research Engineer",
            "sponsorship": None, "sponsorship_evidence": None}
    hn = {"id": "skyhn-1", "company": "Skyline AI", "company_slug": "skyline-ai", "title": "AI Engineer",
          "sponsorship": "no", "sponsorship_evidence": "This position requires U.S. citizenship."}
    uni = {"id": "ucsf-1", "company": "UCSF", "company_slug": "ucsf", "title": "ML Engineer",
           "sponsorship": None, "sponsorship_evidence": None}
    companies = [{"slug": "ucsf", "name": "UCSF", "sponsorship_stance": "cap-exempt"}]
    hb.enrich([anth, hn, uni], index, companies=companies, today=date(2026, 9, 24))
    assert anth["sponsorship"] == "yes" and anth["h1b_approvals_3y"] == 254
    assert anth["sponsorship_evidence"].startswith("USCIS H-1B hub")
    assert anth["lca_count"] == 5 and anth["median_lca_wage"] == 312000 and anth["wage_level_est"] == "III"
    assert hn["sponsorship"] == "no" and hn["sponsorship_evidence"] == "This position requires U.S. citizenship."
    assert hn["h1b_approvals_3y"] == 125
    assert uni["sponsorship"] == "cap_exempt"


# ----------------------------------------------------------------------------- download
def test_download_pivots_the_hub_export_into_the_cache(root):
    url = hb.hub_url(2026)
    assert url == ("https://bigdataanalyticspub-sb.uscis.dhs.gov/views/H1BEmployerDataHub-Final/"
                   "H1BPublic.csv?Fiscal%20Year%20%20%20=2026")
    http = FakeHttp({("DOWNLOAD", url): SYNTH / "uscis_hub_long_fy2026_sample.csv"})
    report = hb.download(root, http, fys=[2026], now=NOW)
    cache = root / "legal" / "evidence" / "h1b"
    assert (cache / "uscis_h1b_fy2026.csv").is_file()
    assert not list(cache.glob("*.part")) and not list(cache.glob("_raw*"))
    idx = hb.load_index(cache)
    assert hb.lookup(idx, "Acmeai", fys=[2026])["h1b_approvals_3y"] == 143
    assert hb.lookup(idx, "OpenLab", fys=[2026])["h1b_approvals_3y"] == 304
    manifest = json.loads((cache / "_manifest.json").read_text())
    assert manifest["files"]["uscis_h1b_fy2026.csv"]["source"] == url
    assert report["written"] == ["uscis_h1b_fy2026.csv"]


def _xlsx(path: Path, rows: list[list]) -> None:
    """Minimal real-structure workbook: shared strings, one inline string, numeric cells."""
    strings: list[str] = []

    def col(i: int) -> str:
        return chr(ord("A") + i)

    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{col(c)}{r}"
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            elif r == 2 and c == 0:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData>{"".join(body)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>' +
                   "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")


def test_lca_xlsx_is_converted_to_an_h1b_computer_occupation_subset(tmp_path, root):
    serial = (date(2026, 5, 1) - date(1899, 12, 30)).days
    header = ["CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE",
              "EMPLOYER_NAME", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY", "PW_WAGE_LEVEL", "UNUSED_COLUMN"]
    rows = [header,
            ["I-200-1", "Certified", serial, "H-1B", "Machine Learning Engineer", "15-1252.00", "VANDELAY INC", 210000, "Year", "II", "x"],
            ["I-200-2", "Certified", serial, "H-1B", "Financial Analyst", "13-2051.00", "VANDELAY INC", 150000, "Year", "II", "x"],
            ["I-200-3", "Certified", serial, "E-3 Australian", "ML Engineer", "15-1252.00", "VANDELAY INC", 200000, "Year", "II", "x"]]
    src = tmp_path / "LCA_Disclosure_Data_FY2026_Q3.xlsx"
    _xlsx(src, rows)
    report = hb.download(root, FakeHttp({}), fys=[], lca_xlsx=[src], now=NOW)
    out = root / "legal" / "evidence" / "h1b" / "lca_LCA_Disclosure_Data_FY2026_Q3.csv"
    assert report["written"] == [out.name]
    got = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(got) == 1
    assert got[0]["CASE_NUMBER"] == "I-200-1" and got[0]["RECEIVED_DATE"] == "2026-05-01"
    assert got[0]["WAGE_RATE_OF_PAY_FROM"] == "210000" and "UNUSED_COLUMN" not in got[0]


def test_blocked_lca_download_explains_the_manual_path(root, capsys):
    url = hb.lca_url(2026, 3)
    http = FakeHttp({("DOWNLOAD", url): FetchError(f"GET {url}: HTTP 403")})
    report = hb.download(root, http, fys=[], lca_quarters=[(2026, 3)], now=NOW)
    assert report["written"] == [] and report["errors"]
    out = capsys.readouterr().out
    assert "https://www.dol.gov/agencies/eta/foreign-labor/performance" in out and "--lca-xlsx" in out


def test_cli_lookup_prints_json(root, capsys):
    cache = root / "legal" / "evidence" / "h1b"
    shutil.copytree(CACHE, cache)
    assert hb.main(["--root", str(root), "lookup", "OpenLab", "--today", "2026-09-24"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["h1b_approvals_3y"] == 504 and out["sponsorship"] == "yes"


def test_cli_enrich_updates_job_files(root):
    import fetch_ats as fa
    shutil.copytree(CACHE, root / "legal" / "evidence" / "h1b")
    rec = fa.make_record({"name": "OpenLab", "slug": "openlab"}, title="Research Engineer", source="ashby",
                         source_url="https://jobs.ashbyhq.com/openlab/x", apply_url=None, ats_vendor="ashby",
                         posted_at="2026-09-20", location="San Francisco", remote="unknown",
                         comp=(None, None, None), employment_type=None, jd_text="JD", now=NOW)
    fa.upsert_jobs([rec], root, NOW)
    assert hb.main(["--root", str(root), "enrich", "--today", "2026-09-24"]) == 0
    job = fa.load_jobs(root)[rec["id"]]
    assert job["sponsorship"] == "yes" and job["h1b_approvals_3y"] == 504


# ----------------------------------------------------------------------------- entities
# Fictional employers modelled on the company-researcher quillstone-entity-refresh-02 case: a San Jose
# product company and a Houston staffing firm whose names normalize to the same key.
QUILL_USCIS = [
    "fiscal_year,employer,tax_id,city,state,zip,naics,approvals,denials,initial_approvals,continuing_approvals",
    "2024,QUILLSTONE SYSTEMS INC,2109,SAN JOSE,CA,95131,511210,2,0,2,0",
    "2024,QUILLSTONE SYSTEMS LLC,7743,HOUSTON,TX,77056,541612,14,2,11,3",
    "2025,\"QUILLSTONE SYSTEMS, INC.\",2109,SAN JOSE,CA,95131,511210,3,0,3,0",
    "2025,QUILLSTONE SYSTEMS LLC,7743,HOUSTON,TX,77056,541612,15,1,12,3",
    "2026,QUILLSTONE SYSTEMS INC,2109,SAN JOSE,CA,95131,511210,3,0,3,0",
    "2026,QUILLSTONE SYSTEMS LLC,7743,HOUSTON,TX,77056,541612,11,0,9,2",
]


def _quill_cache(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uscis_h1b_quill.csv").write_text("\n".join(QUILL_USCIS) + "\n")
    header = (CACHE / "lca_h1b_sample.csv").read_text().splitlines()[0]
    lca = [("QUILLSTONE SYSTEMS INC", "San Jose", "CA", w, "II") for w in (152000, 168000, 149000, 174000, 160000)]
    lca += [("QUILLSTONE SYSTEMS LLC", c, "TX", w, "I") for c, w in (("Houston", 98000), ("Houston", 102000),
                                                                     ("Dallas", 99000))]
    rows = [f"I-{i},Certified,2026-01-05,2026-01-12,H-1B,Software Engineer,15-1252.00,Software Developers,Y,"
            f"{n},{c},{s},{c},{s},{w},,Year,100000,Year,{lv}" for i, (n, c, s, w, lv) in enumerate(lca)]
    (folder / "lca_quill.csv").write_text("\n".join([header, *rows]) + "\n")
    return folder


def _by_match(res):
    return {e["match"]: e for e in res["entities"]}


def test_lookup_returns_per_entity_rows_and_counts_only_the_named_legal_entity(tmp_path):
    res = hb.lookup(hb.load_index(_quill_cache(tmp_path)), "Quillstone Systems, Inc.", fys=FYS)
    assert res["h1b_approvals_3y"] == 8 and res["h1b_by_fy"] == {2024: 2, 2025: 3, 2026: 3}
    assert res["lca_count"] == 5 and res["median_lca_wage"] == 160000 and res["sponsorship"] == "yes"
    assert res["ambiguous"] is False and len(res["entities"]) == 2
    mine, other = _by_match(res)["primary"], _by_match(res)["possible_other_entity"]
    assert (mine["legal_name"], mine["city"], mine["state"], mine["naics"], mine["tax_id"]) == \
        ("QUILLSTONE SYSTEMS INC", "SAN JOSE", "CA", "511210", "2109")
    assert (mine["h1b_approvals_3y"], mine["lca_count"]) == (8, 5)
    assert (other["legal_name"], other["city"], other["state"], other["naics"]) == \
        ("QUILLSTONE SYSTEMS LLC", "HOUSTON", "TX", "541612")
    assert (other["h1b_approvals_3y"], other["lca_count"]) == (40, 3)
    assert res["employer_matches"] == ["QUILLSTONE SYSTEMS INC", "QUILLSTONE SYSTEMS, INC."]
    assert "QUILLSTONE SYSTEMS LLC" not in res["sponsorship_evidence"]
    assert "1 look-alike entity not counted" in res["sponsorship_evidence"]


def test_a_bare_name_shared_by_two_entities_is_ambiguous_not_summed(tmp_path):
    idx = hb.load_index(_quill_cache(tmp_path))
    res = hb.lookup(idx, "Quillstone Systems", fys=FYS)
    assert res["ambiguous"] is True and res["h1b_approvals_3y"] is None and res["sponsorship"] == "unknown"
    assert {e["match"] for e in res["entities"]} == {"possible_other_entity"}
    assert "--state" in res["note"] and "--merge" in res["note"]
    by_state = hb.lookup(idx, "Quillstone Systems", fys=FYS, state="CA")
    assert by_state["h1b_approvals_3y"] == 8 and by_state["ambiguous"] is False
    by_city = hb.lookup(idx, "Quillstone Systems", fys=FYS, city="Houston")
    assert by_city["h1b_approvals_3y"] == 40
    by_alias = hb.lookup(idx, "Quillstone Systems", fys=FYS, aliases=["QUILLSTONE SYSTEMS INC"])
    assert by_alias["h1b_approvals_3y"] == 8


def test_merge_keeps_the_old_name_key_sum(tmp_path, capsys):
    cache = _quill_cache(tmp_path / "legal" / "evidence" / "h1b")
    assert hb.lookup(hb.load_index(cache), "Quillstone Systems", fys=FYS, merge=True)["h1b_approvals_3y"] == 48
    assert hb.main(["--root", str(tmp_path), "lookup", "Quillstone Systems, Inc.", "--today", "2026-09-28"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["h1b_approvals_3y"] == 8 and len(out["entities"]) == 2
    assert hb.main(["--root", str(tmp_path), "lookup", "Quillstone Systems", "--merge", "--today", "2026-09-28"]) == 0
    assert json.loads(capsys.readouterr().out)["h1b_approvals_3y"] == 48
    assert hb.main(["--root", str(tmp_path), "lookup", "Quillstone Systems", "--state", "TX",
                    "--today", "2026-09-28"]) == 0
    assert json.loads(capsys.readouterr().out)["h1b_approvals_3y"] == 40


def test_enrich_does_not_credit_a_look_alike_entity(tmp_path):
    idx = hb.load_index(_quill_cache(tmp_path))
    bare = {"id": "q-1", "company": "Quillstone Systems", "company_slug": "quillstone-systems", "title": "ML Engineer",
            "sponsorship": None, "sponsorship_evidence": None}
    named = dict(bare, id="q-2")
    hb.enrich([bare], idx, companies=[], today=date(2026, 9, 28))
    assert bare["sponsorship"] == "unknown" and bare["h1b_approvals_3y"] is None
    assert "2 entities" in bare["sponsorship_evidence"] and "h1b_names" in bare["sponsorship_evidence"]
    hb.enrich([named], idx, companies=[{"slug": "quillstone-systems", "h1b_names": ["QUILLSTONE SYSTEMS INC"]}],
              today=date(2026, 9, 28))
    assert named["sponsorship"] == "yes" and named["h1b_approvals_3y"] == 8 and named["lca_count"] == 5


def test_help_documents_entities_and_merge(capsys):
    with pytest.raises(SystemExit):
        hb.main(["lookup", "--help"])
    text = capsys.readouterr().out
    assert "possible_other_entity" in text and "--merge" in text and "--state" in text
    assert "possible_other_entity" in hb.__doc__


# ----------------------------------------------------------------------------- titles, denials, NAICS (eval 0.1.3)
# A dossier counted "Machine Learning Engineer x2" by eye where the LCA rows had 3, cited a denial count to
# output that carried none, and named NAICS industries from no source. The lookup now prints the counts.
COMPACT = "fiscal_year,employer,tax_id,city,state,zip,naics,approvals,denials,initial_approvals,continuing_approvals"


def test_lca_titles_count_the_same_rows_as_lca_count(index):
    res = hb.lookup(index, "Acmeai", fys=FYS)
    assert res["lca_titles"] == [{"title": "Research Engineer", "count": 3}, {"title": "Software Engineer", "count": 2}]
    assert sum(t["count"] for t in res["lca_titles"]) == res["lca_count"]      # withdrawn, denied, E-3 left out


def test_lca_titles_sort_by_count_then_title(tmp_path):
    header = (CACHE / "lca_h1b_sample.csv").read_text().splitlines()[0]
    titles = ["ML Engineer", "Applied Scientist", "ML Engineer", "Data Engineer", "Applied Scientist",
              "ML Engineer", "Zeta Engineer"]
    rows = [f"I-{i},Certified,2026-01-05,2026-01-12,H-1B,{t},15-1252.00,Software Developers,Y,"
            f"TITLE CO INC,Austin,TX,Austin,TX,200000,,Year,150000,Year,II" for i, t in enumerate(titles)]
    (tmp_path / "lca_t.csv").write_text("\n".join([header, *rows]) + "\n")
    res = hb.lookup(hb.load_index(tmp_path), "Title Co", fys=FYS)
    assert [(t["title"], t["count"]) for t in res["lca_titles"]] == [
        ("ML Engineer", 3), ("Applied Scientist", 2), ("Data Engineer", 1), ("Zeta Engineer", 1)]


def test_denials_per_fiscal_year_come_from_the_cache(index):
    assert hb.lookup(index, "Acmeai", fys=FYS)["denials"] == {2024: 1, 2025: 2, 2026: 2}
    assert hb.lookup(index, "OpenLab", fys=FYS)["denials"] == {2024: 0, 2025: 4, 2026: 5}   # 0 is a real zero here


def test_an_absent_denials_column_omits_the_key_never_zero(tmp_path):
    (tmp_path / "uscis_nodenials.csv").write_text(
        "fiscal_year,employer,tax_id,city,state,zip,naics,approvals,initial_approvals,continuing_approvals\n"
        "2025,NODENY CO INC,11,AUSTIN,TX,78701,541511,7,3,4\n")
    res = hb.lookup(hb.load_index(tmp_path), "Nodeny Co", fys=[2025])          # the one cached FY
    assert res["h1b_approvals_3y"] == 7 and "denials" not in res


def test_a_blank_denials_cell_or_a_year_without_rows_is_omitted(tmp_path):
    (tmp_path / "uscis_mixed.csv").write_text(COMPACT + "\n"
                                              "2024,MIXED CO INC,12,AUSTIN,TX,78701,541511,5,,2,3\n"
                                              "2025,MIXED CO INC,12,AUSTIN,TX,78701,541511,6,1,2,4\n")
    res = hb.lookup(hb.load_index(tmp_path), "Mixed Co", fys=FYS)
    assert res["denials"] == {2025: 1}                   # 2024 blank, 2026 no row: neither becomes 0


def test_a_year_with_one_row_lacking_denials_is_omitted(tmp_path):
    (tmp_path / "uscis_a.csv").write_text(COMPACT + "\n2025,SPLIT CO INC,13,AUSTIN,TX,78701,541511,5,2,2,3\n")
    (tmp_path / "uscis_b.csv").write_text(
        "fiscal_year,employer,tax_id,city,state,zip,naics,approvals\n2025,SPLIT CO INC,13,AUSTIN,TX,78701,541511,4\n")
    res = hb.lookup(hb.load_index(tmp_path), "Split Co", fys=[2025])          # the one cached FY
    assert res["h1b_approvals_3y"] == 9 and "denials" not in res          # a partial sum would undercount


def test_archive_denials_are_read_and_absent_denial_columns_omit_them(tmp_path):
    shutil.copy(SYNTH / "h1b_datahubexport-2023_sample.csv", tmp_path / "h1b_datahubexport-2023.csv")
    res = hb.lookup(hb.load_index(tmp_path), "Vandelay", fys=[2023])
    assert "denials" in res and set(res["denials"]) == {2023}
    other = tmp_path / "other"
    other.mkdir()
    (other / "h1b_datahubexport-2023.csv").write_text(
        "Fiscal Year,Employer,Initial Approval,Continuing Approval,NAICS,Tax ID,State,City,ZIP\n"
        "2023,VANDELAY INC,50,31,33,1234,CA,FOSTER CITY,94404\n")
    res = hb.lookup(hb.load_index(other), "Vandelay", fys=[2023])
    assert res["h1b_approvals_3y"] == 81 and "denials" not in res


def test_the_hub_long_export_carries_denials_through_the_pivot(tmp_path):
    src = SYNTH / "uscis_hub_long_fy2026_sample.csv"
    with src.open(newline="", encoding="utf-8") as fh:
        want = sum(int(r["Measure Values"].replace(",", "")) for r in csv.DictReader(fh)
                   if r["Employer (Petitioner) Name"] == "ACMEAI PBC" and r["Measure Names"].endswith("Denial"))
    hb.pivot_hub_long(src, tmp_path / "uscis_h1b_fy2026.csv")
    assert hb.lookup(hb.load_index(tmp_path), "Acmeai", fys=[2026])["denials"] == {2026: want}
    only = tmp_path / "only"                                                 # the long file read directly
    only.mkdir()
    shutil.copy(src, only / "raw_long.csv")
    assert hb.lookup(hb.load_index(only), "Acmeai", fys=[2026])["denials"] == {2026: want}


def test_a_long_export_without_denial_measures_writes_no_zero_denials(tmp_path):
    src = SYNTH / "uscis_hub_long_fy2026_sample.csv"
    lines = src.read_text(encoding="utf-8").splitlines()
    kept = [lines[0]] + [ln for ln in lines[1:] if "Denial," not in ln]
    (tmp_path / "long.csv").write_text("\n".join(kept) + "\n", encoding="utf-8")
    hb.pivot_hub_long(tmp_path / "long.csv", tmp_path / "cache" / "uscis_h1b_fy2026.csv")
    res = hb.lookup(hb.load_index(tmp_path / "cache"), "Acmeai", fys=[2026])
    assert res["h1b_approvals_3y"] == 143 and "denials" not in res
    direct = tmp_path / "direct"
    direct.mkdir()
    (direct / "long.csv").write_text("\n".join(kept) + "\n", encoding="utf-8")
    assert "denials" not in hb.lookup(hb.load_index(direct), "Acmeai", fys=[2026])


def test_unknown_and_ambiguous_employers_carry_no_titles_or_denials(tmp_path, index):
    res = hb.lookup(index, "Tiny Stealth Startup", fys=FYS)
    assert res["lca_titles"] == [] and "denials" not in res
    amb = hb.lookup(hb.load_index(_quill_cache(tmp_path)), "Quillstone Systems", fys=FYS)
    assert amb["ambiguous"] is True and amb["lca_titles"] is None and "denials" not in amb


def test_the_primary_entity_only_counts_titles_and_denials(tmp_path):
    res = hb.lookup(hb.load_index(_quill_cache(tmp_path)), "Quillstone Systems, Inc.", fys=FYS)
    assert res["lca_titles"] == [{"title": "Software Engineer", "count": 5}]
    assert res["denials"] == {2024: 0, 2025: 0, 2026: 0}                   # the Houston LLC's 3 are not counted


def test_merge_reports_titles_and_denials_over_every_match(tmp_path):
    res = hb.lookup(hb.load_index(_quill_cache(tmp_path)), "Quillstone Systems", fys=FYS, merge=True)
    assert res["lca_titles"] == [{"title": "Software Engineer", "count": 8}]
    assert res["denials"] == {2024: 2, 2025: 1, 2026: 0}


def test_naics_is_a_code_unless_a_local_table_names_it(tmp_path):
    cache = _quill_cache(tmp_path)
    res = hb.lookup(hb.load_index(cache), "Quillstone Systems, Inc.", fys=FYS)
    mine = _by_match(res)["primary"]
    assert mine["naics"] == "511210" and "naics_name" not in mine and "naics_source" not in mine
    (cache / hb.NAICS_TABLE).write_text("code,title\n511210,Software Publishers (fixture)\n", encoding="utf-8")
    res = hb.lookup(hb.load_index(cache), "Quillstone Systems, Inc.", fys=FYS)
    rows = _by_match(res)
    assert rows["primary"]["naics_name"] == "Software Publishers (fixture)"
    assert rows["primary"]["naics_source"] == hb.NAICS_TABLE
    assert "naics_name" not in rows["possible_other_entity"]              # 541612 is not in the table
    assert res["h1b_approvals_3y"] == 8                                    # the table is not read as filings


def test_cli_prints_titles_and_denials(tmp_path, capsys):
    _quill_cache(tmp_path / "legal" / "evidence" / "h1b")
    assert hb.main(["--root", str(tmp_path), "lookup", "Quillstone Systems, Inc.", "--today", "2026-09-28"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lca_titles"] == [{"title": "Software Engineer", "count": 5}]
    assert out["denials"] == {"2024": 0, "2025": 0, "2026": 0}


# ----------------------------------------------------------------------------- missing is not zero
# An absent cache printed h1b_approvals_3y 0 / lca_count 0 and two dossiers repeated it as a finding.
def test_an_absent_cache_is_unknown_with_reasons_never_zero(tmp_path):
    res = hb.lookup(hb.load_index(tmp_path / "nowhere"), "Hooli", fys=FYS)
    assert res["h1b_approvals_3y"] == "unknown" and res["lca_count"] == "unknown"
    assert res["h1b_by_fy"] == {2024: "unknown", 2025: "unknown", 2026: "unknown"}
    assert res["sponsorship"] == "unknown" and res["median_lca_wage"] is None and res["lca_titles"] is None
    reasons = {m["source"]: m["reason"] for m in res["missing"]}
    assert "no USCIS Data Hub cache" in reasons["uscis_h1b_hub"] and "h1b_lookup.py fetch" in reasons["uscis_h1b_hub"]
    assert "no DOL LCA disclosure cache" in reasons["dol_lca"] and "--lca-xlsx" in reasons["dol_lca"]


def test_a_full_cache_reports_nothing_missing_and_real_zeros_stay_zero(index):
    res = hb.lookup(index, "Tiny Stealth Startup", fys=FYS)
    assert res["missing"] == [] and res["h1b_approvals_3y"] == 0 and res["lca_count"] == 0


def test_uscis_cached_but_no_lca_file_leaves_only_the_lca_fields_unknown(tmp_path):
    for fy in FYS:
        shutil.copy(CACHE / f"uscis_h1b_fy{fy}.csv", tmp_path)
    res = hb.lookup(hb.load_index(tmp_path), "OpenLab", fys=FYS)
    assert res["h1b_approvals_3y"] == 504 and res["sponsorship"] == "yes"
    assert res["lca_count"] == "unknown" and res["wage_level_est"] == "unknown" and res["lca_titles"] is None
    assert [m["source"] for m in res["missing"]] == ["dol_lca"]


def test_an_uncached_fiscal_year_is_unknown_and_the_found_approvals_still_count(tmp_path):
    for fy in (2024, 2025):
        shutil.copy(CACHE / f"uscis_h1b_fy{fy}.csv", tmp_path)
    shutil.copy(CACHE / "lca_h1b_sample.csv", tmp_path)
    res = hb.lookup(hb.load_index(tmp_path), "OpenLab", fys=FYS)
    assert res["h1b_by_fy"] == {2024: 75, 2025: 125, 2026: "unknown"}
    assert res["h1b_approvals_3y"] == "unknown" and res["sponsorship"] == "yes"
    assert res["sponsorship_evidence"].endswith("not cached: FY2026")
    assert res["missing"][0]["fiscal_years"] == [2026] and isinstance(res["lca_count"], int)
    assert all(e["h1b_approvals_3y"] == "unknown" for e in res["entities"])


def test_cli_lookup_on_an_absent_cache_exits_3_unless_allow_missing(root, capsys):
    assert hb.main(["--root", str(root), "lookup", "Hooli", "--today", "2026-09-26"]) == 3
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["h1b_approvals_3y"] == "unknown" and out["lca_count"] == "unknown"
    assert "run h1b_lookup.py fetch" in captured.err and "--allow-missing" in captured.err
    assert hb.main(["--root", str(root), "lookup", "Hooli", "--today", "2026-09-26", "--allow-missing"]) == 0


def test_enrich_on_an_absent_cache_writes_empty_counts_not_zero(tmp_path):
    rec = {"id": "x-1", "company": "Hooli", "company_slug": "hooli", "title": "Founding Engineer",
           "sponsorship": None, "sponsorship_evidence": None}
    hb.enrich([rec], hb.load_index(tmp_path / "nowhere"), today=date(2026, 9, 26))
    assert rec["h1b_approvals_3y"] is None and rec["lca_count"] is None and rec["sponsorship"] == "unknown"


def test_cli_fetch_downloads_one_request_per_file_and_writes_the_readme(root, capsys):
    routes = {("DOWNLOAD", hb.hub_url(fy)): SYNTH / "uscis_hub_long_fy2026_sample.csv" for fy in FYS}
    http = FakeHttp(routes)
    assert hb.main(["--root", str(root), "fetch", "--fy", *map(str, FYS)], http=http, now=NOW) == 0
    assert sorted(url for _, url, _ in http.calls) == sorted(hb.hub_url(fy) for fy in FYS)     # once each
    cache = root / "legal" / "evidence" / "h1b"
    readme = (cache / "README.md").read_text()
    assert hb.HUB_PAGE in readme and hb.hub_url(2026) in readme and hb.DOL_PAGE in readme
    assert NOW.date().isoformat() in readme and "`uscis_h1b_fy2025.csv`" in readme and "--lca-xlsx" in readme
    assert "h1b_lookup.py" in hb.FETCH_USER_AGENT and hb.FETCH_USER_AGENT in readme
    assert "h1b fetch: wrote" in capsys.readouterr().out


def test_download_is_kept_as_an_alias_of_fetch(root):
    http = FakeHttp({("DOWNLOAD", hb.hub_url(2026)): SYNTH / "uscis_hub_long_fy2026_sample.csv"})
    assert hb.main(["--root", str(root), "download", "--fy", "2026"], http=http, now=NOW) == 0
    assert (root / "legal" / "evidence" / "h1b" / "uscis_h1b_fy2026.csv").is_file()



def test_a_new_fiscal_year_with_nothing_published_reads_the_last_three_published(tmp_path):
    """The Scout's lesson L-04757013: on 2026-10-01 FY2027 began with no USCIS file, and every lookup went unknown."""
    for fy in (2024, 2025, 2026):
        shutil.copy(CACHE / f"uscis_h1b_fy{fy}.csv", tmp_path)
    idx = hb.load_index(tmp_path)
    assert hb.published_years(idx, date(2026, 9, 30)) == [2024, 2025, 2026]
    assert hb.published_years(idx, date(2026, 10, 1)) == [2024, 2025, 2026]       # FY2027 unpublished: shift back
    assert hb.published_years(idx, date(2027, 10, 1)) == [2026, 2027, 2028]       # two years behind: no silent shift
    res = hb.lookup(idx, "OpenLab", today=date(2026, 10, 1))
    assert res["fiscal_years"] == [2024, 2025, 2026]
    assert [m["source"] for m in res["missing"]] == ["dol_lca"]                     # no USCIS gap reported
