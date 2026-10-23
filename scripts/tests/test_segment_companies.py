"""Tests for scripts/segment_companies.py: targets/companies.yaml `segment` from evidence, in order: the segment files'
own company lists, the dream list's group, the company dossiers (stage, size), then a short known-names table."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import segment_companies as sc  # noqa: E402

HEAD = """# targets/companies.yaml - watchlist
companies:
"""


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def entry(name, slug, tier="good"):
    return f"""  # verified 2026-10-01
  - name: {name}
    slug: {slug}
    tier: {tier}
    ats_vendor: ashby
    board_token: "{slug}"
"""


@pytest.fixture
def js(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "companies.yaml").write_text(HEAD + "".join(entry(*e) for e in [
        ("Quanta Labs", "quanta-labs"), ("Globex Software", "globex"), ("Initech Capital", "initech"),
        ("Zephyr AI", "zephyr"), ("Roadrunner", "roadrunner"), ("Sim", "sim"), ("Autoco", "autoco"), ("Mystery Co", "mystery"),
        ("Bigsearch", "bigsearch"), ("Bigcorp Inc", "bigcorp"), ("Bigsearch DeepLab", "bigsearch-deeplab")]))
    w(tmp_path / "targets" / "segments" / "ai-lab.md", """
        # Segment: AI labs (Northwind AI, Orbit AI, Quanta Labs, Reflex, Xenon ...)
        ## Referral mechanics
        | Company | Mechanics | Order |
        |---|---|---|
        | Zephyr | form | 1 |
        """)
    w(tmp_path / "targets" / "segments" / "big-tech.md", "# Segment: big tech (Bigsearch, Metaverse, Globex ...)\n")
    w(tmp_path / "targets" / "segments" / "enterprise.md",
      "# Segment: enterprise (non-tech or mixed companies with AI teams: Autoco, Initech, Capital Example, consulting firms ...)\n")
    w(tmp_path / "targets" / "segments" / "startup.md", "# Segment: startups (seed to Series C, roughly under 300 people)\n")
    w(tmp_path / "research" / "dream" / "DREAM-COMPANIES.md", """
        | company | group | why | roles | sponsorship | channel |
        |---|---|---|---|---|---|
        | Roadrunner | autonomy | self-driving | 3 | sponsors | greenhouse |
        | Bigsearch | big tech | search | 9 | sponsors | site |
        | Bigsearch DeepLab | frontier lab | models | 0 | sponsors | site |
        """)
    w(tmp_path / "companies" / "sim.md", """
        ---
        type: company
        slug: sim
        name: Sim
        stage: seed
        size: about 10 people
        ---
        """)
    w(tmp_path / "companies" / "bigcorp.md", """
        ---
        type: company
        slug: bigcorp
        name: Bigcorp Inc
        stage: public
        size: large public company
        ---
        """)
    return tmp_path


def test_evidence_order_and_unknown_stays_unknown(js):
    found = {r["slug"]: r for r in sc.classify(js)}
    assert (found["quanta-labs"]["segment"], found["quanta-labs"]["source"]) == ("ai-lab", "targets/segments/ai-lab.md")
    assert found["zephyr"]["segment"] == "ai-lab"                         # the referral table names it
    assert found["globex"]["segment"] == "big-tech" and found["initech"]["segment"] == "enterprise"
    assert found["autoco"]["segment"] == "enterprise"
    assert found["bigsearch"]["source"] == "targets/segments/big-tech.md"     # the segment file comes before the table
    assert (found["sim"]["segment"], found["sim"]["source"]) == ("startup", "companies/sim.md (stage seed)")
    assert found["roadrunner"]["source"].startswith("known-names table")  # dream group "autonomy" says nothing of size;
    assert found["mystery"]["segment"] is None                             # Roadrunner is in the known-names table
    assert found["bigcorp"]["segment"] is None                            # public alone is not a segment
    assert found["mystery"]["segment"] is None
    assert found["bigsearch-deeplab"]["segment"] == "ai-lab"           # an exact name beats "Bigsearch" as a prefix


def test_dry_run_writes_nothing_and_write_adds_segment_lines_keeping_comments(js, capsys):
    before = (js / "targets" / "companies.yaml").read_text()
    assert sc.main(["--root", str(js), "--dry-run"]) == 0
    assert (js / "targets" / "companies.yaml").read_text() == before
    assert "would set 9" in capsys.readouterr().out                        # Roadrunner joins (known names)
    assert sc.main(["--root", str(js)]) == 0
    text = (js / "targets" / "companies.yaml").read_text()
    assert text.count("  # verified 2026-10-01") == 11
    rows = {c["slug"]: c for c in yaml.safe_load(text)["companies"]}
    assert rows["sim"]["segment"] == "startup" and rows["sim"]["segment_source"] == "companies/sim.md (stage seed)"
    assert "segment" not in rows["mystery"] and rows["roadrunner"]["segment"] == "big-tech"
    assert sc.main(["--root", str(js)]) == 0                                 # idempotent: no duplicate lines
    assert (js / "targets" / "companies.yaml").read_text() == text


def test_the_known_names_table_is_last_and_short(js):
    p = js / "targets" / "segments" / "big-tech.md"
    p.write_text("# Segment: big tech\n")
    d = js / "research" / "dream" / "DREAM-COMPANIES.md"
    d.write_text("\n".join(ln for ln in d.read_text().splitlines() if "| Bigsearch |" not in ln) + "\n")
    found = {r["slug"]: r for r in sc.classify(js)}
    assert found["bigsearch"]["segment"] == "big-tech"
    assert found["bigsearch"]["source"].startswith("known-names table (segment_companies.py): public company")
    assert set(sc.KNOWN) == {"bigsearch", "bigsearch cloud", "northwind ai", "roadrunner"}     # the persona's table


# More recorded evidence, each kept in segment_source.
def _yaml_entry(js, text):
    p = js / "targets" / "companies.yaml"
    p.write_text(p.read_text() + text)


def test_yc_and_vc_board_entries_are_startups(js):
    _yaml_entry(js, '''  # unverified: found by scripts/yc_boards.py
  - name: "Ledgerly"
    slug: "ledgerly"
    tier: good
    notes: "YC Summer 2025: books. Board found by scripts/yc_boards.py (website)"
  - name: Portco
    slug: portco
    tier: good
    careers_url: "https://jobs.example.getro.com/companies/portco"
''')
    found = {r["slug"]: r for r in sc.classify(js)}
    assert (found["ledgerly"]["segment"], found["ledgerly"]["source"]) == ("startup", "companies.yaml notes (YC)")
    assert (found["portco"]["segment"], found["portco"]["source"]) == ("startup", "companies.yaml careers_url (VC board)")


def test_dossier_headcount_under_500_and_public_by_sector(js):
    w(js / "companies" / "mid.md", "---\ntype: company\nslug: mid\nname: Midco\nstage: unknown\nsize: about 450 people\n---\n")
    w(js / "companies" / "bigcorp.md", "---\ntype: company\nslug: bigcorp\nname: Bigcorp Inc\nstage: \"public (NYSE: BIG)\"\n"
      "size: about 16,800 employees\n---\nBigcorp makes test and measurement instruments.\n")
    w(js / "companies" / "payco.md", "---\ntype: company\nslug: payco\nname: Payco\nstage: \"public (NYSE: PAY)\"\n"
      "size: thousands of employees\n---\nPayco runs a payments app and a software platform.\n")
    _yaml_entry(js, "  - name: Midco\n    slug: mid\n    tier: good\n  - name: Payco\n    slug: payco\n    tier: good\n")
    found = {r["slug"]: r for r in sc.classify(js)}
    assert found["mid"]["segment"] == "startup" and "size about 450 people" in found["mid"]["source"]
    assert found["bigcorp"]["segment"] == "enterprise" and "instrument" in found["bigcorp"]["source"]
    assert found["payco"]["segment"] == "big-tech" and "software" in found["payco"]["source"]


def test_job_records_and_companies_off_the_watchlist(js):
    w(js / "jobs" / "tradeco-man-1.md", "---\nid: tradeco-man-1\ncompany: TradeCo\ntitle: Founding Engineer\n---\n"
      "## JD\nTradeCo (incorporated Feb 2026) by three co-founders.\n")
    w(js / "companies" / "tinyco.md", "---\ntype: company\nslug: tinyco\nname: TinyCo\nstage: seed\nsize: 4\n---\n")
    seg, src = sc.segment_for(js, "TradeCo", "tradeco")
    assert seg == "startup" and src.startswith("jobs/tradeco-man-1.md")
    assert sc.segment_for(js, "TinyCo", "tinyco") == ("startup", "companies/tinyco.md (stage seed)")
    assert sc.segment_for(js, "Nobody Inc", "nobody") == (None, None)
    w(js / "jobs" / "bigco-1.md", "---\nid: bigco-1\ncompany: Bigco\ntitle: Founding Forward Deployed Engineer\n---\n")
    assert sc.segment_for(js, "Bigco", "bigco") == (None, None)                # a founding team inside a big company


# companies.yaml is the one source for a segment; the dashboard's pick goes through `set`.
def test_set_records_the_owners_pick_and_inference_never_overwrites_it(js):
    assert sc.main(["--root", str(js), "set", "bigsearch", "enterprise", "--source", sc.OWNER_SOURCE]) == 0
    rows = {c["slug"]: c for c in yaml.safe_load((js / "targets" / "companies.yaml").read_text())["companies"]}
    assert (rows["bigsearch"]["segment"], rows["bigsearch"]["segment_source"]) == ("enterprise", sc.OWNER_SOURCE)
    assert sc.main(["--root", str(js)]) == 0                                   # a later inference run
    rows = {c["slug"]: c for c in yaml.safe_load((js / "targets" / "companies.yaml").read_text())["companies"]}
    assert (rows["bigsearch"]["segment"], rows["bigsearch"]["segment_source"]) == ("enterprise", sc.OWNER_SOURCE)
    assert sc.main(["--root", str(js), "set", "Bigsearch", "none", "--source", sc.OWNER_SOURCE]) == 0   # by name; clears it
    assert "segment" not in {c["slug"]: c for c in yaml.safe_load(
        (js / "targets" / "companies.yaml").read_text())["companies"]}["bigsearch"]


def test_set_refuses_bad_input_and_writes_nothing(js, capsys):
    before = (js / "targets" / "companies.yaml").read_text()
    assert sc.main(["--root", str(js), "set", "bigsearch", "giant", "--source", sc.OWNER_SOURCE]) == 2
    assert sc.main(["--root", str(js), "set", "no-such-co", "startup", "--source", sc.OWNER_SOURCE]) == 2
    assert sc.main(["--root", str(js), "set", "bigsearch", "startup", "--source", "guess"]) == 2
    assert (js / "targets" / "companies.yaml").read_text() == before
    assert "not on targets/companies.yaml" in capsys.readouterr().err


def test_set_works_on_flow_style_entries(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Delta Works, slug: gamma, tier: strong}\n  - {name: Beta, slug: beta, tier: good}\n")
    assert sc.main(["--root", str(tmp_path), "set", "gamma", "enterprise", "--source", sc.OWNER_SOURCE]) == 0
    rows = {c["slug"]: c for c in yaml.safe_load((tmp_path / "targets" / "companies.yaml").read_text())["companies"]}
    assert rows["gamma"]["segment"] == "enterprise" and rows["gamma"]["segment_source"] == sc.OWNER_SOURCE
    assert rows["beta"] == {"name": "Beta", "slug": "beta", "tier": "good"}


def test_growth_is_a_private_company_past_series_c_or_500_to_10000_people(js):
    """'growth' is a segment: a private company at Series D or later, or 500 to 10,000
    people. A public company of that size is never growth; it stays big-tech, enterprise or unknown by sector."""
    assert "growth" in sc.SEGMENTS
    w(js / "companies" / "lateco.md", "---\ntype: company\nslug: lateco\nname: Lateco\nstage: Series E\nsize: unknown\n---\n")
    w(js / "companies" / "midsize.md", "---\ntype: company\nslug: midsize\nname: Midsize\nstage: unknown\n"
      "size: about 1,200 employees\n---\n")
    w(js / "companies" / "pubmid.md", "---\ntype: company\nslug: pubmid\nname: Pubmid\nstage: \"public (NASDAQ: PM)\"\n"
      "size: about 2,000 employees\n---\n")
    _yaml_entry(js, "  - name: Lateco\n    slug: lateco\n    tier: good\n  - name: Midsize\n    slug: midsize\n    tier: good\n"
                    "  - name: Pubmid\n    slug: pubmid\n    tier: good\n")
    found = {r["slug"]: r for r in sc.classify(js)}
    assert found["lateco"]["segment"] == "growth" and "stage Series E" in found["lateco"]["source"]
    assert found["midsize"]["segment"] == "growth" and "size about 1,200 employees" in found["midsize"]["source"]
    assert "pubmid" not in found or found["pubmid"]["segment"] != "growth"


def test_set_accepts_growth(js):
    assert sc.main(["--root", str(js), "set", "bigsearch", "growth", "--source", sc.OWNER_SOURCE]) == 0
    assert "segment: growth" in (js / "targets" / "companies.yaml").read_text()


@pytest.mark.parametrize("stage,size,expected", [
    ("Series D (2025), after a Series C", "unknown", "growth"),         # the latest round decides
    ("pre-IPO, plans to go public in 2027", "unknown", "growth"),      # 'go public' is not public
    ("unknown", "201-500 employees", "startup"),                       # a range takes its lower bound
])
def test_growth_edge_cases_from_the_review(js, stage, size, expected):
    w(js / "companies" / "edgeco.md", f"---\ntype: company\nslug: edgeco\nname: Edgeco\nstage: \"{stage}\"\nsize: \"{size}\"\n---\n")
    _yaml_entry(js, "  - name: Edgeco\n    slug: edgeco\n    tier: good\n")
    found = {r["slug"]: r for r in sc.classify(js)}
    assert found["edgeco"]["segment"] == expected
