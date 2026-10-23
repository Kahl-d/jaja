"""fit_gates.py: deterministic hard gates (sponsorship, location, seniority, employment, E-Verify).
Block/boost sentences are the wording job posts use, kept verbatim."""
from __future__ import annotations

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fit_gates as fg  # noqa: E402


def job(**kw):
    base = {"id": "acme-1", "company": "Acme", "company_slug": "acme", "title": "Machine Learning Engineer",
            "location": "San Francisco, CA", "remote": "onsite", "employment_type": "full-time",
            "sponsorship": None, "sponsorship_evidence": None, "status": "discovered"}
    base.update(kw)
    return base


def gate(jd="", config=None, company=None, **kw):
    return fg.evaluate(job(**kw), jd, config=config or fg.default_config(), company=company)


# ----------------------------------------------------------------------------- sponsorship
@pytest.mark.parametrize("sentence", [
    "U.S. citizenship required, with the ability to obtain and maintain a security clearance.",
    "Must be eligible to obtain a Secret Clearance; Secret clearance eligibility requires US Citizenship.",
    "U.S. Citizenship and active Secret clearance required.",
    "This position requires U.S. citizenship and the ability to obtain/maintain a security clearance.",
    "Clearance required: US Citizens Only & Must have an Active Secret or higher clearance",
    "Green card holders only.",
    "Must be a U.S. person as defined by ITAR.",
    "We do not participate in E-Verify.",
    "The company will not sign the Form I-983 for STEM OPT candidates.",
    "We are not accepting OPT/CPT candidates for this role.",
])
def test_citizenship_clearance_and_opt_refusals_fail_with_the_line_as_evidence(sentence):
    """Hard gates are citizenship or clearance, and an employer refusing E-Verify or the I-983 (an OPT refusal
    is one)."""
    jd = f"About the role\nYou will build agents.\n{sentence}\nWe value curiosity."
    out = gate(jd)
    assert out["gate"] == "fail"
    assert any(r.startswith("sponsorship:") for r in out["reasons"])
    assert any(sentence.split(";")[0].rstrip(".")[:40] in line for line in out["evidence_lines"])
    assert out["sponsorship_signal"] == "block"


@pytest.mark.parametrize("sentence", [
    "Please note that visa sponsorship is not available for this position.",
    "Applicants must have work authorization that does not require sponsorship from the company now or in the future",
    "This role is not eligible for visa sponsorship.",
    "We are not currently able to sponsor visas for fellows.",
    "Candidates must be authorized to work in the United States without sponsorship.",
    "We are unable to sponsor or take over sponsorship of an employment visa at this time.",
    "Visa Sponsorship: Not available",
])
def test_no_sponsorship_is_a_flagged_risk_never_a_skip(sentence):
    """A missing visa is a risk the fit-scorer weighs, never a reason to skip a role. The gate never skips on it."""
    jd = f"About the role\nYou will build agents.\n{sentence}\nWe value curiosity."
    out = gate(jd)
    assert out["gate"] == "pass", out["reasons"]
    assert any(r.startswith("flag: sponsorship:") for r in out["reasons"])
    assert any(sentence.split(";")[0].rstrip(".")[:40] in line for line in out["evidence_lines"])
    assert out["sponsorship_signal"] == "block"


@pytest.mark.parametrize("sentence,signal", [
    ("Visa sponsorship: We do sponsor visas! However, we aren't able to successfully sponsor visas for "
     "every role and every candidate.", "boost"),
    ("Build and maintain trusted relationships with key decision-makers, capability sponsors, programme offices.", "none"),
    ("Experience working with executive sponsors, business process owners, IT leaders.", "none"),
    ("You'll take part in company sponsored events and enjoy exploring a new city.", "none"),
    ("We do not discriminate on the basis of race, religion, national origin, citizenship status, or disability.", "none"),
    ("Candidates must be authorized to work in the United States.", "ambiguous"),
    ("An active security clearance is a plus but not required.", "none"),
    ("We ship firewall rules without sponsorship for an export license.", "none"),
    ("If access to export-controlled technology or source code is required for performance of job duties, it is "
     "within Employer's discretion whether to apply for a U.S. government license for such positions, and Employer "
     "may decline to proceed with an applicant on this basis alone.", "none"),
])
def test_sponsorship_false_positives_do_not_fail(sentence, signal):
    out = gate(f"Intro.\n{sentence}\nOutro.")
    assert out["gate"] == "pass", out
    assert out["sponsorship_signal"] == signal


def test_repeated_block_sentences_give_one_reason():
    out = gate("Must be a U.S. person.\nReminder: must be a U.S. person.")
    assert len([r for r in out["reasons"] if r.startswith("sponsorship:")]) == 1
    assert len(out["evidence_lines"]) == 2


def test_itar_mention_without_a_person_requirement_is_review():
    out = gate("Familiarity with FAR, DFARS, ITAR, export compliance, and government contracting requirements.")
    assert out["gate"] == "review"
    assert any("ITAR" in line for line in out["evidence_lines"])


@pytest.mark.parametrize("sentence", [
    "Visa sponsorship: We sponsor visas.",
    "We provide visa sponsorship support and assess each circumstance on a case-by-case basis.",
])
def test_boost_phrases(sentence):
    out = gate(sentence)
    assert out["gate"] == "pass" and out["sponsorship_signal"] == "boost"
    assert any(r.startswith("note: sponsorship offered") for r in out["reasons"])


def test_record_level_no_from_the_posting_fails_only_on_citizenship_or_clearance():
    out = gate("", sponsorship="no", sponsorship_evidence="U.S. Citizens only")
    assert out["gate"] == "fail" and "U.S. Citizens only" in out["evidence_lines"]
    out = gate("", sponsorship="no", sponsorship_evidence="This role is not eligible for visa sponsorship.")
    assert out["gate"] == "pass" and any(r.startswith("flag: sponsorship:") for r in out["reasons"])


def test_employer_history_alone_never_blocks():
    out = gate("", sponsorship="unknown", sponsorship_evidence=None)
    assert out["gate"] == "pass"


def test_company_stance_does_not_sponsor_is_flagged_and_cap_exempt_notes():
    out = gate("", company={"sponsorship_stance": "does-not-sponsor"})
    assert out["gate"] == "pass" and any("flag: sponsorship: targets/companies.yaml" in r for r in out["reasons"])
    out = gate("", company={"sponsorship_stance": "cap-exempt"})
    assert out["gate"] == "pass" and any("cap-exempt" in r for r in out["reasons"])


def test_everify_mention_is_reported():
    out = gate("Acme participates in E-Verify.")
    assert out["everify"] is True and out["gate"] == "pass"
    assert gate("Nothing here.")["everify"] is False


def test_sentence_split_keeps_us_abbreviation_together():
    parts = fg.split_sentences("Must be a U.S. Citizen. We ship fast.\nRemote OK; U.S. only")
    assert parts == ["Must be a U.S. Citizen.", "We ship fast.", "Remote OK", "U.S. only"]


# ----------------------------------------------------------------------------- location
@pytest.mark.parametrize("location,remote,tier,verdict", [
    ("San Francisco, CA | New York City, NY", "onsite", None, "pass"),
    ("US, CA, Santa Clara | US, CA, Remote", "remote", None, "pass"),
    ("Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA", "remote", None, "pass"),
    ("Remote (US) or Santa Clara or Brooklyn", "remote", None, "pass"),
    ("REMOTE (US)", "remote", None, "pass"),
    ("Remote (U.S. — Eastern/Central Time)", "remote", None, "pass"),
    ("United States", "remote", None, "pass"),
    ("Foster City, CA", "hybrid", None, "pass"),
    ("Dublin, CA", "onsite", None, "pass"),
    ("London, UK", "onsite", None, "fail"),
    ("Dublin, IE", "onsite", None, "fail"),
    ("Amsterdam, Netherlands | ONSITE (hybrid)", "hybrid", None, "fail"),
    ("Remote (EU time zone) + Quarterly Meet-ups", "remote", None, "fail"),
    ("New York City, NY", "onsite", None, "pass"),                       # anywhere in the US
    ("Newark, NJ", "onsite", None, "pass"),
    ("New York City, NY", "onsite", "dream", "pass"),
    ("Remote", "remote", None, "review"),
    ("Remote (worldwide)", "remote", None, "review"),
    (None, "unknown", None, "review"),
    # live 2026-09-25 locations that could not be placed before
    ("Belgrade, Serbia", "onsite", None, "fail"),
    ("Gurugram", "onsite", None, "fail"),
    ("São Paulo", "onsite", None, "fail"),
    ("Doha, Qatar", "onsite", None, "fail"),
    ("Ottawa", "onsite", None, "fail"),
    ("Dublin", "onsite", None, "fail"),
    ("Sydney | Melbourne", "remote", None, "fail"),
    ("NYC", "onsite", None, "pass"),
    ("Bellevue", "onsite", None, "pass"),
    ("McLean, Virginia", "onsite", None, "pass"),
    ("Maryland; Virginia; Washington, D.C.", "onsite", None, "pass"),
    ("Remote - Virginia", "remote", None, "pass"),
    ("Remote - CA", "remote", None, "pass"),
    ("Bay Area, CA, United States of America", "hybrid", None, "pass"),
    ("United States", "onsite", None, "pass"),
    ("Austin, TX", "hybrid", None, "pass"),
    ("Toronto, Canada | New York, NY", "onsite", None, "pass"),
])
def test_location_gate(location, remote, tier, verdict):
    company = {"tier": tier} if tier else None
    out = gate("", location=location, remote=remote, company=company)
    assert out["gate"] == verdict, out["reasons"]


def test_us_outside_the_home_region_passes_with_a_relocation_note():
    """A US city outside the home region is never a reject; the record says a move is involved."""
    out = gate("", location="Seattle, WA", remote="onsite")
    assert out["gate"] == "pass", out["reasons"]
    assert "note: location: US outside the home region (open to relocating)" in out["reasons"]


def test_gate_undoes_its_own_location_skip():
    """A job an old home-region rule skipped returns to discovered when re-gated (apply_gates undo path)."""
    rec = job(title="AI Engineer", location="Chicago, IL", status="skipped", fit_verdict="skip", gate="fail",
              gate_reasons=["location: US onsite outside the SF Bay Area"], _jd_text="Build agents.")
    fg.apply_gates([rec], fg.default_config())
    assert (rec["gate"], rec["status"], rec["fit_verdict"]) == ("pass", "discovered", None)


# ----------------------------------------------------------------------------- seniority + employment
@pytest.mark.parametrize("title,verdict", [
    ("Staff+ Software Engineer, Model Serving Path", "fail"),
    ("Principal Engineer, Inference", "fail"),
    ("Senior Staff Engineer, ML Ops (R1141)", "fail"),                  # a level above staff
    ("Senior Manager, Machine Learning Ops Engineering - Fleet Systems", "fail"),
    ("Contract Student Worker - Machine Learning Engineer - Data Curation & VLM (Full-time)", "fail"),
    ("Applied Machine Learning Engineer, Chip Verification - New College Grad 2026", "fail"),
    ("Example Fellows Program, ML Systems & Reinforcement Learning", "fail"),
    ("Senior / Staff Machine Learning Engineer - Motion Forecasting for Delivery Robots", "review"),
    ("Lead AI Engineer – Enterprise Data & AI Platforms", "review"),
    ("Senior Machine Learning Engineer, Perception", "pass"),
    ("Research Engineer, Simulations", "pass"),
    ("Member of Technical Staff (full-stack + AI evals)", "pass"),
])
def test_seniority_gate(title, verdict):
    assert gate("", title=title)["gate"] == verdict


def test_seniority_allow_flag_lets_a_band_through():
    cfg = fg.default_config(allow=["staff"])
    assert gate("", config=cfg, title="Staff+ Software Engineer, Model Serving Path")["gate"] == "pass"


@pytest.mark.parametrize("jd,verdict", [
    ("You have 5+ years of experience building ML systems.", "pass"),
    ("You have 8+ years of experience in machine learning.", "fail"),
    ("Minimum of 10 years of professional experience.", "fail"),
    ("3+ years with Python; 7+ years of software engineering experience.", "pass"),
    ("We have been building AI products for 10 years.", "pass"),       # not a requirement
])
def test_required_years(jd, verdict):
    assert gate(jd)["gate"] == verdict


def test_employment_type_gate():
    assert gate("", employment_type="contract")["gate"] == "fail"
    assert gate("", config=fg.default_config(allow=["contract"]), employment_type="contract")["gate"] == "pass"
    assert gate("This is a 1099 position paid hourly.")["gate"] == "review"
    tax = ("Contribute to our large-scale tax and regulatory reporting systems, including US IRS information "
           "returns (the 1099 family, issued at the scale of tens of millions of forms annually).")   # Block
    assert gate(tax)["gate"] == "pass"


def test_output_shape_and_precedence():
    out = gate("U.S. citizenship required.", location="London, UK", title="Staff Engineer, ML Platform")
    assert set(out) >= {"gate", "reasons", "evidence_lines", "sponsorship_signal", "everify"}
    assert out["gate"] == "fail"
    kinds = {r.split(":")[0] for r in out["reasons"]}
    assert {"sponsorship", "location", "seniority"} <= kinds
    assert gate("", title="Lead ML Engineer", location="Remote")["gate"] == "review"


# ----------------------------------------------------------------------------- roles.md lists
ROLES_MD = r"""# Target roles

## 8. Sponsorship phrasing lists

**Block (no sponsorship).**
```
\bno relocation visas\b
```

**Block (citizenship, clearance, export control).**
```
\bnational security vetting\b
```

**Boost (sponsorship offered).**
```
\bwe gladly sponsor\b
```
"""


def test_roles_md_lists_replace_the_extended_builtins_but_core_rules_stay(root):
    (root / "targets").mkdir()
    (root / "targets" / "roles.md").write_text(ROLES_MD, encoding="utf-8")
    cfg = fg.load_config(root)
    assert gate("Requires national security vetting.", config=cfg)["gate"] == "fail"    # the hard list
    assert gate("Requires national security vetting.")["gate"] == "pass"            # not a built-in
    flagged = gate("We offer no relocation visas.", config=cfg)                    # the no-sponsorship list: a flag
    assert flagged["gate"] == "pass" and any(r.startswith("flag: sponsorship:") for r in flagged["reasons"])
    assert gate("We cannot sponsor visas.", config=cfg)["sponsorship_signal"] == "block"   # core, flagged
    assert gate("U.S. citizenship required.", config=cfg)["gate"] == "fail"        # core hard rule
    assert gate("We gladly sponsor H-1B.", config=cfg)["sponsorship_signal"] == "boost"


# ----------------------------------------------------------------------------- apply to records + CLI
def test_apply_gates_marks_records():
    fail = job(id="a", _jd_text="This role requires U.S. citizenship and an active Secret clearance.")
    flagged = job(id="d", _jd_text="This role is not eligible for visa sponsorship.")
    ok = job(id="b", _jd_text="Visa sponsorship: We do sponsor visas!", sponsorship="unknown")
    skipped_before = job(id="c", status="applied", location="London, UK", _jd_text="")
    summary = fg.apply_gates([fail, ok, skipped_before, flagged], fg.default_config())
    assert fail["gate"] == "fail" and fail["fit_verdict"] == "skip" and fail["status"] == "skipped"
    assert fail["sponsorship"] == "no"
    assert flagged["gate"] == "pass" and flagged["status"] != "skipped" and flagged["sponsorship"] == "no"
    assert flagged["sponsorship_evidence"] == "This role is not eligible for visa sponsorship."
    assert ok["gate"] == "pass" and ok["sponsorship"] == "yes"
    assert ok["sponsorship_evidence"] == "Visa sponsorship: We do sponsor visas!"
    assert "fit_verdict" not in ok or ok["fit_verdict"] is None
    assert skipped_before["status"] == "applied"          # never un-apply
    assert summary == {"evaluated": 4, "pass": 2, "review": 0, "fail": 2}


def test_a_jd_sponsorship_line_replaces_employer_level_evidence():
    rec = job(_jd_text="Visa sponsorship: We do sponsor visas!", sponsorship="yes",
              sponsorship_evidence="targets/companies.yaml: h1b_approvals_recent 143")
    fg.apply_gates([rec], fg.default_config())
    assert rec["sponsorship"] == "yes" and rec["sponsorship_evidence"] == "Visa sponsorship: We do sponsor visas!"
    hub = job(_jd_text="This role is not eligible for visa sponsorship.", sponsorship="yes",
              sponsorship_evidence="USCIS H-1B hub: 254 approvals FY2024-FY2026 (ACME PBC)")
    fg.apply_gates([hub], fg.default_config())
    assert hub["sponsorship"] == "no" and hub["sponsorship_evidence"] == "This role is not eligible for visa sponsorship."


def test_cli_dry_run_writes_nothing(root, capsys):
    import fetch_ats as fa
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title="Machine Learning Engineer", source="greenhouse",
                         source_url="https://job-boards.greenhouse.io/acme/jobs/1", apply_url=None,
                         ats_vendor="greenhouse", posted_at="2026-09-23", location="London, UK", remote="onsite",
                         comp=(None, None, None), employment_type="full-time", jd_text="Great role.", now=NOW)
    fa.upsert_jobs([rec], root, NOW)
    before = (root / "jobs" / f"{rec['id']}.md").read_text()
    assert fg.main(["--root", str(root), "--dry-run"]) == 0
    assert (root / "jobs" / f"{rec['id']}.md").read_text() == before
    assert "fail 1" in capsys.readouterr().out
    assert fg.main(["--root", str(root)]) == 0
    assert fa.load_jobs(root)[rec["id"]]["gate"] == "fail"


def test_a_job_without_a_captured_jd_is_held_at_review():
    held = gate(tags=["jd-missing"])
    assert held["gate"] == "review" and any(r.startswith("jd: ") for r in held["reasons"])
    assert gate()["gate"] == "pass"                                   # same record, JD tag absent
    failed = gate(tags=["jd-missing"], location="London, UK")
    assert failed["gate"] == "fail"                                   # a hard fail still wins
    assert gate("Build agents in Python.", tags=["jd-missing"])["gate"] == "pass"   # a JD arrived: stale tag ignored



# Early-career seats that ask 1 to 3 years count; new-grad programs and internships do not;
# the years note itself is tested in test_years_over_the_count.
@pytest.mark.parametrize("title,jd,verdict,note", [
    ("Deployed Engineer (Early Career, SF)", "You have 1-3 years of experience shipping software.", "pass",
     "early-career seat asking 1 years or more"),
    ("Software Engineer, Early Career", "2+ years of experience with Python.", "pass", "early-career seat asking 2 years or more"),
    ("Software Engineer, Early Career", "No experience listed.", "fail", None),             # no years: a program
    ("Applied Machine Learning Engineer, Chip Verification - New College Grad 2026", "1+ years of experience.", "fail",
     None),                                                                                 # a new-grad program
    ("ML Engineer, Automated Evaluation", "4+ years of experience in evaluation.", "pass", "STRETCH"),
])
def test_early_career_seats_count(title, jd, verdict, note):
    out = gate(jd, title=title)
    assert out["gate"] == verdict, out["reasons"]
    if note:
        assert any(note in str(r) for r in out["reasons"] + out.get("notes", [])), out


# Up to 3 years asked goes cold; 4+ is no cold application, a STRETCH only through a person; 8+ stays the fail.
# The 3-year rows: test_k380_peoplewatch.py.
@pytest.mark.parametrize("title,jd,verdict,note,absent", [
    ("AI Engineer", "2+ years of experience with Python.", "pass", None, "no cold application"),
    ("AI Engineer", "4+ years of experience with Python.", "pass", "asks 4+ years, over the 3 years a cold application "
     "allows: no cold application; STRETCH only through a person", None),
    ("Software Engineer, Early Career", "3+ years of experience with Python.", "pass", "a cold application", "no cold application"),
    ("Software Engineer, Early Career", "2+ years of experience with Python.", "pass", "within your ~29 engineer months",
     "no cold application"),
    ("AI Engineer", "8+ years of experience.", "fail", None, "no cold application"),
])
def test_years_over_the_count(title, jd, verdict, note, absent):
    out = gate(jd, title=title)
    text = " ".join(str(r) for r in out["reasons"] + out.get("notes", []))
    assert out["gate"] == verdict, out["reasons"]
    assert note is None or note in text, text
    assert absent is None or absent not in text, text
    assert fg.ENGINEER_MONTHS == 29 and fg.COLD_MAX_YEARS == 3 and fg.REJECT_YEARS == 8   # the persona's policy


# A spelled-out figure ("Two years") is an ask: spelled numbers one to ten are read.
@pytest.mark.parametrize("jd,years", [
    ("Two years of post-graduate engineering experience.", 2),
    ("three+ years of experience building ML systems", 3),
    ("At least five years of experience in production.", 5),
    ("ten years of professional experience", 10),
    ("You have 2+ years of experience with Python.", 2),
    ("We value two teams working together on experience design.", None),       # no years asked
])
def test_required_years_reads_spelled_numbers(jd, years):
    assert fg.required_years(jd) == years


# The years asks the parser once missed or misread, in the words real JDs use.
LONG_JD = ("Basic Qualifications:\xa0 \n\n- Bachelor's Degree in Computer Science, AI, or related fields plus at least 3 "
               "years of experience developing AI and ML algorithms or technologies, or a Master's degree in Computer "
               "Science, AI, or related fields plus at least 1 year of experience developing AI and ML algorithms or "
               "technologies \n\n- At least 3 years of experience programming with Python, Go, Scala, CUDA, or Java \n\n"
               " Preferred Qualifications:\xa0 \n\n- 4+ years of experience deploying scalable and responsible AI "
               "solutions on cloud platforms \n\n- Experience in building agentic AI systems")


@pytest.mark.parametrize("jd,years", [
    # an ask with no word "experience": a "+" marks it (plain bullet lists)
    ("certified as a Great Place to Work for the past 6 years in a row\n- 3+ years in a technical role with a strong "
     "foundation in AI\n- 1+ years shipping production AI systems to end users", 3),
    ("- 3+ years across ML, data engineering, or AI systems in production", 3),
    ("- 3+ years of professional software engineering, machine learning engineering, or research", 3),
    # company history is not an ask
    ("We have served hospitals for over 10+ years.", None),
    ("For the past 6 years in a row we were named a best place to work.", None),
    # preferred qualifications are not the ask: the section, or the word in the same line
    (LONG_JD, 3),
    ("Requirements:\n- 2+ years of experience with Python\nNice to have:\n- 6+ years of experience in ML", 2),
    ("- 5+ years of experience preferred\n- 1+ years of experience with LLMs", 1),
    ("- 5+ years of experience in software engineering with at least one backend language (Go preferred)", 5),
    ("- 4+ years of experience with Python, ideally 6+ years in ML", 4),
    # degree paths: the persona holds a Master's (and a Bachelor's): the easiest path it meets is the ask
    ("Bachelor's degree plus 5 years of experience, or a Master's degree plus 3 years of experience", 3),
    ("PhD with 0 years, Master's with 2+ years of experience, or Bachelor's with 4+ years of experience", 2),
    # misreads found on a before/after run
    ("Qualifications/Nice to have:\n- 5+ years of professional experience, with 2 year experience operating ML", 5),
    ("Ideally you'd have:\n- 4+ years of full-time engineering experience, post-graduation", 4),
    ("Nice to Have:\n- A Master\u2019s degree or Bachelor\u2019s with at least 2 years of work experience", None),
    ("We\u2019re nine engineers. We\u2019re looking for 5+ years of professional experience, strong TypeScript.", 5),
    ("Bachelor's or Master's degree in CS. You have 5+ years of Python experience.", 5),
    ("A Master\u2019s degree and 1 year of experience, or a Bachelor\u2019s and 3 years of experience.", 1),
    # headings with no colon, either-or asks, a PhD path the persona lacks, company history
    ("BASIC QUALIFICATIONS\n- 3+ years of experience in ML\n\nPREFERRED QUALIFICATIONS\n- PhD, or Master's degree "
     "and 4+ years of science experience", 3),
    ("- 2+ years of relevant work experience with a Bachelor's degree, OR 5+ years of relevant work experience.", 2),
    ("- M.S. or Ph.D. in Computer Science\n- 5+ years (or Ph.D. with 3+ years) of experience in ML modelling", 5),
    ("Example Partners has a stellar 30+ year track record and a reputation for excellence.", None),
    ("- 3+ years of experience in ML or data engineering, and 5+ years of software engineering", 5),
    # the degree after the figure; a "desired" heading that holds the requirements
    ("- 9 or more years of relevant work experience with a Bachelor Degree or 7 or more relevant years of experience "
     "with an Advanced degree (e.g. Masters, MBA, JD, MD) or 5 or more years of experience with a PhD", 7),
    ("Desired capabilities\n- 6+ years of professional software engineering experience\nExtra credit\n- 8+ years "
     "of experience leading teams", 6),
    # "e.g." is not a sentence end; a duration in a description is not an ask; "3-10+" is a range
    ("\u2022 8+ years of relevant work experience with a Bachelor\u2019s Degree or at least 5 years of experience with "
     "an Advanced Degree (e.g. Masters, MBA, JD, MD) or 2 years of work experience with a PhD, OR 11+ years of "
     "relevant work experience.", 5),
    ("- Work with experienced founders who\u2019ve built and scaled financial products for 9+ years", None),
    ("- At least 3-10+ years working with a variety of programming languages such as Go, Python", 3),
    # a sentence can start with a figure ("experience. 6+ Years of ...")
    ("Bachelor's Degree in CS AND 4+ years technical engineering experience OR Master's AND 2+ years OR equivalent "
     "experience. 6+ Years of previous experience in developing and troubleshooting networking.", 6),
    # a list of fields joined by "or" is not a choice between asks (B1)
    ("- 5+ years of experience in Python or Go, with 2+ years in ML", 5),
    ("- 6+ years of software engineering or ML experience, 3+ years of which in a tech lead role", 6),
    ("- 5+ years in data engineering, backend development, or database design, including 3+ years with Neo4j", 5),
    ("- 8+ years in data engineering, data architecture, or related roles, with at least 5 years at the Principal level", 8),
    # B2: within one degree path the asks add up (the largest); across paths the easiest one the persona holds
    ("- BS/MS in CS with 4+ years of experience in ML and 2+ years of experience with LLMs", 4),
    ("- Bachelor's or Master's degree in CS, 5+ years of experience in backend, and 2+ years with LLMs", 5),
    ("- BS or MS in Computer Science and 5+ years of software engineering experience, including 3+ years in ML.", 5),
    ("PhD in CS with 3+ years of engineering experience and 5+ years of ML experience; or MS in CS with 6+ years of "
     "engineering experience and 5+ years of ML experience; or BS in CS with 7+ years of engineering experience and 5+ "
     "years of ML experience", 6),
    ("- Bachelor's degree and 5+ years of experience, including 2+ years in ML", 5),
    # B3: "preferred", "ideally", "a plus" only when they qualify the years
    ("You have 3 to 6 years of experience deploying software in production settings\u2014ideally with enterprise", 3),
    ("At least 5 years industry experience ideally in quality assurance", 5),
    ("- 5+ years of software engineering experience and startup experience is a plus", 5),
    ("- 5+ years of experience in your preferred programming language", 5),
    # B4: a heading with no colon ends the preferred section before it
    ("Preferred Qualifications\n- Experience with Spark\nEducation & Experience\n- MS in CS with 6+ years of "
     "engineering experience and 5+ years of ML experience; or BS with 7+ years of engineering experience", 6),
    # B5: a range is an ask; "experience" may sit further out; "BS or MS ..., with 10+ years"
    ("Have 4\u20136 years of professional software engineering or AI engineering experience", 4),
    ("- 3-5 years of professional software engineering or machine learning engineering experience", 3),
    ("- BS or MS in Computer Science or a related engineering field, with 10+ years of software development experience", 10),
    ("Master's degree in Engineering, Information Systems, Computer Science, or related field and 5+ years of Systems "
     "Engineering, Software Engineering, or SIT experience.", 5),
    # N1, N2: a degree path with no "+" still counts; a shared "MS or PhD" path is the persona's
    ("- 8 years of experience with a Bachelor's degree, or 6 years with a Master's degree, or 3 years with a PhD", 6),
    ("Bachelor's degree and 7 years of experience, or Master's degree and 5 years, or PhD and 2 years", 5),
    ("- MS or PhD with 2+ years of experience, or BS with 4+ years of experience", 2),
    # N3: "more than", "over", "expertise"
    ("Proven expertise of more than 3 years in at least one of the pre-silicon ASIC verification methodologies", 3),
    ("Over 5 years of validated expertise in GPU systems", 5),
    # N4: a responsibilities heading is not a required heading
    ("What You'll Do\n- Build things\nDesired Capabilities\n- 5+ years of software engineering experience\nBonus "
     "Points\n- 8+ years leading teams", 5),
    # N6: an intro line inside a preferred section does not end it
    ("Requirements:\n- 2+ years of experience\nPreferred Qualifications:\nIt would be great if you also have:\n- 6+ "
     "years of experience", 2),
    ("Requirements:\n- 2+ years of experience\nYou'll stand out if you have:\n- 6+ years of experience", 2),
    # company history is not an ask, even with the word experience (pins the history rule)
    ("We have 15+ years of experience in fintech.", None),
    ("About Us\n- 10+ years serving Fortune 500 clients", None),
    # an "About ..." heading opens no skipped section; only benefits and pay do
    ("About Acme\nWe build AI.\nAbout the Role\nYou will build agents.\nYou might thrive if you:\n- Have 5+ years of "
     "software engineering experience", 5),
    ("About You\n- 3+ years of experience in ML", 3),
    ("Requirements\n- 2+ years of experience\nBenefits\n- 401k match after 1+ years of service", 2),
    # a figure deep in a long sentence reads its own clause (the 300-character window keeps its offset)
    ("We build agents for hospitals " + "and clinics " * 40 + "and you bring 4+ years of experience preferred, "
     "with 2+ years of experience in Python", 2),
    # an "or" inside the degree's field list is not the no-degree alternative
    ("- BS/MS in CS or related field, 6+ years of backend experience, 3+ years with Kubernetes", 6),
    ("- BS or MS in Computer Science or a related field, 5+ years of software experience, and 2+ years with LLMs", 5),
    ("- BS in CS or equivalent; 5+ years of experience building ML systems; 2+ years with LLMs", 5),
    ("- MS or PhD in CS or a related field, 3+ years of industry experience, 1+ year with LLMs", 3),
    ("- MS in EE/CS, or related field + 2 years of experience, OR BS + 4 years of experience", 2),
    # the "(preferred)" label; a capitalised word after "preferred"; inline preferred heading
    ("- 5+ years of experience (preferred)", None),
    ("- 2+ years of experience; 4+ years preferred for senior level", 2),
    # headings that start with "What" or "Who"
    ("What You'll Bring\n- 2+ years of experience\nNice to Have\n- 5+ years in ML\nBonus Points\n- Rust", 2),
    # "Master of Engineering" is a Master's path
    ("- Master of Engineering in CS and 3+ years of experience, or Bachelor of Engineering and 5+ years of experience", 3),
    # company history with "over"; vesting is not an ask
    ("Our team has over 10 years of experience in fintech.", None),
    ("We have over 15 years of experience in payments.", None),
    ("What we offer\n- Equity vested over 4 years", None),
    # pins for rules the round-2 mutants showed unpinned
    ("- 25+ years of experience in software", None),                                  # n > 20 is not an ask
    ("- Python and 3+ years of experience preferred, with 2+ years of experience in ML", 2),   # start-side "and" cut
    ("- 4-6 years building ML systems in production", 4),                              # a range is an ask
    ("- 5+ years of experience in ML and a Master's degree with 2+ years in research", 5),    # an ask outside the path
    ("Requirements\n- 2+ years of experience\nPreferred:\n- Rust\nAbout the Role\n- 6+ years of experience", 2),
    ("Requirements\n- 2+ years of experience\nBenefits\n- 5+ years of experience matching our 401k", 2),
    ("Proven expertise of 3 years in ASIC verification", 3),                           # expertise
    ("Nice to have\n- Rust\nAbout You\n- 4+ years of experience", 4),                    # "about you" is required
    # "OR equivalent (minimum N years)" is the no-degree alternative; a label in parentheses closes at once;
    # "is a bonus" is a label; and the three pins that did not catch their mutants
    ("- Bachelor's degree in Computer Science OR equivalent (minimum 12 years) work experience, and 6+ years of "
     "experience in data engineering", 6),
    ("- 3+ years of experience in Python (preferred) or Go", 3),
    ("- 3+ years of experience (preferred: fintech)", 3),
    ("- 5+ years of Go experience is a bonus", None),
    ("- 3+ years of Go experience preferred and 5+ years of experience in ML", 5),
    ("Requirements\n- 2+ years of experience\nPreferred:\n- Rust\nAbout the role:\n- 6+ years of experience", 2),
    ("- 3 years of deep expertise in compilers", 3),
    # unchanged
    ("You have 1-3 years of experience shipping software.", 1),
    ("Minimum 2 years in a similar role.", 2),
])
def test_required_years_reads_the_ask_s32(jd, years):
    assert fg.required_years(jd) == years


# ----------------------------------------------------------------------------- employer
@pytest.mark.parametrize("company", [
    "Example Search", "Acme Recruitment", "Acme Talent", "Globex Hiring Partners", "Example Search Group",
    "Talent Example (Hiring For Top And Emerging Startups In The US)",
    "Stealth AI fintech (via Example Recruitment)", "EXAMPLE HUMAN RESOURCES CONSULTANCIES LLC", "Acme Staffing"])
def test_staffing_names_fail(company):
    """Generic staffing words fail on their own; the named firms come from policy.employer.staffing_names."""
    out = fg.evaluate(job(company=company), "", config=fg.default_config())
    assert out["gate"] == "fail" and any(r.startswith("employer: staffing") for r in out["reasons"])


@pytest.mark.parametrize("company", ["Acme Health", "Northwind", "Example Corp", "Globex Partners",
                                     "Initech Group", "Writer", "Capital Example"])
def test_real_employers_pass_the_employer_gate(company):
    out = fg.evaluate(job(company=company), "", config=fg.default_config())
    assert not any(r.startswith("employer") for r in out["reasons"])


def test_it_services_go_to_review():
    out = fg.evaluate(job(company="Example Consulting"), "", config=fg.default_config())
    assert out["gate"] == "review" and any("IT services" in r for r in out["reasons"])


@pytest.mark.parametrize("jd", [
    "Our client is a fast-growing AI startup in San Francisco.",
    "We are recruiting on behalf of our client, a Series B fintech.",
    "Direct client role, full-time W2.",
    "We're a boutique technical recruiting firm placing engineers.",
    "My client, a leading healthcare AI company, seeks an engineer."])
def test_unnamed_client_jds_fail(jd):
    out = fg.evaluate(job(), jd, config=fg.default_config())
    assert out["gate"] == "fail" and "employer: posted for an unnamed client" in out["reasons"]
    assert out["evidence_lines"]


@pytest.mark.parametrize("jd", [
    "You will work closely with our clients to deploy agents.",
    "Build client-facing tools for our enterprise customers.",
    "Help our customers and client teams succeed."])
def test_client_words_in_real_jds_do_not_fail(jd):
    out = fg.evaluate(job(), jd, config=fg.default_config())
    assert not any(r.startswith("employer") for r in out["reasons"])
