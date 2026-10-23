"""check_portals.py: one presence record .

state/recruiter/portal-inventory.yaml is the source of truth for setup state; targets/platforms.yaml `presence:` (read by
portal_report.py and the job-search MCP) is checked against it, so the two can never again disagree the way the audit
found (base 75 in the registry while the portals held base 76; WTTJ, HiringCafe, YC and Handshake `unknown` in the
registry while the inventory had them done). Every row also names its sign-in method (never a credential) and its
registry id. Fixtures are synthetic; one guard reads the real tree read-only."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

import check_portals as cp

TODAY = date(2026, 10, 6)
from layout import JS  # noqa: E402
PDF = b"%PDF base 76 synthetic"
SHA = hashlib.sha256(PDF).hexdigest()
OLD = "469890f1" + "0" * 56


def tree(tmp_path: Path, presence: str, row_extra: str = "", sign: str = "google-sso", registry: str = "acme",
         account: str = "done", alerts: str = "done", resume: str = "done", base: str = "") -> Path:
    (tmp_path / "resumes").mkdir(parents=True)
    (tmp_path / "resumes" / "base.pdf").write_bytes(PDF)
    states = {f: "done" for f in cp.FIELDS} | {"account": account, "alerts": alerts, "resume": resume}
    lines = ["portals:", "- portal: Acme", "  priority: 1", "  kind: portal", "  url: https://acme.example",
             f"  registry: {registry}", f"  sign_in: {sign}", "  waits_on: nobody", "  next_step: x",
             "  last_checked: '2026-10-05'"] + [f"  {k}: {v}" for k, v in states.items()]
    if resume == "done":
        lines += ["  resume_file:", "    path: resumes/base.pdf", f"    sha256: '{SHA}'"]
    if states["account"] != "done" or alerts != "done" or resume != "done":
        lines[lines.index("  waits_on: nobody")] = "  waits_on: recruiter"
    inv = tmp_path / cp.INVENTORY
    inv.parent.mkdir(parents=True)
    inv.write_text("\n".join(lines) + "\n" + row_extra, encoding="utf-8")
    (tmp_path / cp.SETUP_LIST).write_text("# Alex\n", encoding="utf-8")
    reg = tmp_path / cp.REGISTRY
    reg.parent.mkdir(parents=True)
    reg.write_text("platforms:\n  - id: acme\n    presence:\n" + presence + base, encoding="utf-8")
    return tmp_path


def presence(account="exists", alerts="on", sha=SHA, uploaded="base 76", sign=None):
    out = [f"      account:\n        state: {account}\n"]
    if sign:
        out.append(f"        sign_in: {sign}\n")
    out.append(f"      alerts:\n        state: '{alerts}'\n")
    out.append(f"      resume:\n        uploaded: '{uploaded}'\n" + (f"        sha256: '{sha}'\n" if sha else ""))
    return "".join(out)


def problems(root: Path) -> list[str]:
    return [f"{p['portal']}|{p['field']}|{p['problem']}" for p in cp.check(root, TODAY)["problems"]]


def test_an_agreeing_record_is_clean(tmp_path):
    assert problems(tree(tmp_path, presence(sign="google-sso"))) == []


@pytest.mark.parametrize("pres, field", [
    (presence(account="exists-inferred"), "account"),
    (presence(account="unknown"), "account"),
    (presence(alerts="unknown"), "alerts"),
    (presence(alerts="off"), "alerts"),
    (presence(sha=None), "resume"),
    (presence(sha=OLD), "resume"),
    (presence(uploaded="base 75 only (sha256 469890f1...ed640)"), "resume"),
    (presence(sign="email"), "sign_in"),
], ids=["inferred", "unknown-account", "unknown-alerts", "alerts-off", "no-sha", "old-sha", "old-sha-in-text",
        "sign-in"])
def test_each_kind_of_disagreement_fails(tmp_path, pres, field):
    out = problems(tree(tmp_path, pres))
    assert out and all(p.split("|")[1] == field and "disagrees" in p for p in out), out
    assert cp.main(["--root", str(tmp_path), "--today", "2026-10-06"]) == 1


def test_a_stale_base_number_fails_when_the_registry_names_the_base(tmp_path):
    base = f"presence_base_resume:\n  iteration: 76\n  pdf_sha256: {SHA}\n"
    out = problems(tree(tmp_path, presence(uploaded="base 75 (re-uploaded)"), base=base))
    assert out == ["Acme|resume|disagrees: targets/platforms.yaml acme presence.resume.uploaded says base 75; the file "
                   "is base 76 (presence_base_resume) (fix the stale side; the inventory is the source)"]


def test_a_registry_account_with_no_inventory_row_fails(tmp_path):
    root = tree(tmp_path, presence(sign="google-sso"))
    with (root / cp.REGISTRY).open("a") as fh:
        fh.write("  - id: beta\n    presence:\n      kind: job-board\n      account:\n        state: exists-inferred\n")
    assert "beta|registry|targets/platforms.yaml presence says account exists-inferred but no inventory row names " \
           "registry beta" in problems(root)


def test_sign_in_and_registry_are_required_and_checked(tmp_path):
    out = problems(tree(tmp_path, presence(), sign="password123"))
    assert "Acme|sign_in|'password123' is not one of ['email', 'google-sso', 'indeed-login', 'linkedin', 'none', " \
           "'unknown'] (the method only, never a credential)" in out
    out = problems(tree(tmp_path / "b", presence(), registry="nope"))
    assert "Acme|registry|'nope' is not an id in targets/platforms.yaml" in out
    out = problems(tree(tmp_path / "c", presence(), registry="none"))
    assert "Acme|registry|none without a reason in `na`" in out


def test_an_unknown_setup_value_is_open(tmp_path):
    root = tree(tmp_path, presence(sign="google-sso"))
    inv = root / cp.INVENTORY
    inv.write_text(inv.read_text().replace("  skills: done", "  skills: unknown"), encoding="utf-8")
    assert "Acme|waits_on|nobody, but skills still open" in problems(root)


@pytest.mark.real_records
def test_the_real_inventory_and_registry_agree(monkeypatch):
    """Guard on the department's own files (read only): the inventory and the registry agree, and every row names
    a known sign-in method. Compared by structure and counts, never by name."""
    import yaml
    rows = yaml.safe_load((JS / cp.INVENTORY).read_text())["portals"]
    tokens = {str(r.get("waits_on")) for r in rows} - {"recruiter", "time", "nobody", "None", ""}
    if len(tokens) == 1:                                  # the data folder names its owner's token in waits_on
        token = tokens.pop()
        monkeypatch.setattr(cp, "OWNER", token)
        monkeypatch.setattr(cp, "WAITS", {token, "recruiter", "time", "nobody"})
        monkeypatch.setattr(cp, "SETUP_LIST", Path(f"state/recruiter/{token}-setup-list.md"))
    out = cp.check(JS, TODAY)
    assert out["problems"] == [], out["problems"][:5]
    assert len(rows) >= 10 and len({r["portal"] for r in rows}) == len(rows)
    assert all(r["sign_in"] in cp.SIGN_IN for r in rows)
