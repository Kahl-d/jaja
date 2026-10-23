"""check_portals.py: the portal setup is done only when the inventory proves it.

Fixtures are synthetic inventories written to a temporary root; nothing reads the live state."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import check_portals as cp

TODAY = date(2026, 10, 6)
DONE = {f: "done" for f in cp.FIELDS}
OWNER = cp.OWNER                 # the owner's token in waits_on (config identity.owner_token)


def _root(tmp_path: Path, body: str, setup: str | None = "# Alex\n- A1 Boardly\n") -> Path:
    inv = tmp_path / cp.INVENTORY
    inv.parent.mkdir(parents=True)
    inv.write_text(body, encoding="utf-8")
    if setup is not None:
        (tmp_path / cp.SETUP_LIST).write_text(setup, encoding="utf-8")
    reg = tmp_path / cp.REGISTRY                  # every row names its registry id (here: no presence block)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("platforms:\n  - id: acme\n", encoding="utf-8")
    pdf = tmp_path / "resumes" / "base.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF base 76")
    return tmp_path


def _row(name: str = "Acme", waits: str = "nobody", checked: str = "2026-10-05", sha: str | None = None,
         extra: str = "", **fields: str) -> str:
    states = {**DONE, **fields}
    lines = [f"- portal: {name}", "  priority: 1", "  kind: portal", "  url: https://acme.example",
             "  registry: acme", "  sign_in: unknown",
             f"  waits_on: {waits}", "  next_step: 'read back'", f"  last_checked: '{checked}'"]
    lines += [f"  {k}: {v}" for k, v in states.items()]
    if states.get("resume") == "done":
        digest = sha or hashlib.sha256(b"%PDF base 76").hexdigest()
        lines += ["  resume_file:", "    path: resumes/base.pdf", f"    sha256: '{digest}'"]
    return "\n".join(lines) + ("\n" + extra if extra else "") + "\n"


def _problems(root: Path) -> list[str]:
    return [f"{p['portal']}|{p['field']}|{p['problem']}" for p in cp.check(root, TODAY)["problems"]]


def test_a_finished_row_is_clean(tmp_path):
    assert _problems(_root(tmp_path, "portals:\n" + _row())) == []
    assert cp.main(["--root", str(tmp_path), "--today", "2026-10-06"]) == 0


def test_a_key_written_twice_fails_instead_of_silently_losing_the_first_value(tmp_path):
    body = "portals:\n" + _row(extra="  updates:\n  - at: '2026-10-05'\n  updates:\n  - at: '2026-10-04'")
    out = _problems(_root(tmp_path, body))
    assert len(out) == 1 and "duplicate key 'updates'" in out[0]


def test_missing_is_not_empty_every_setup_field_is_stated(tmp_path):
    row = _row().replace("  messaging: done\n", "")
    assert _problems(_root(tmp_path, "portals:\n" + row)) == ["Acme|messaging|missing: state it (n/a with a "
                                                              "reason when it does not apply)"]


def test_n_a_needs_a_reason(tmp_path):
    out = _problems(_root(tmp_path, "portals:\n" + _row(alerts="n/a")))
    assert out == ["Acme|alerts|n/a without a reason in `na`"]
    ok = _row(alerts="n/a", extra="  na:\n    alerts: the network has no alerts")
    assert _problems(_root(tmp_path / "b", "portals:\n" + ok)) == []


def test_an_open_field_names_who_it_waits_on_and_a_closed_row_waits_on_nobody(tmp_path):
    assert _problems(_root(tmp_path, "portals:\n" + _row(tools="partial"))) == [
        "Acme|waits_on|nobody, but tools still open"]
    assert _problems(_root(tmp_path / "b", "portals:\n" + _row(tools="partial", waits="time"))) == []
    assert _problems(_root(tmp_path / "c", "portals:\n" + _row(waits="recruiter"))) == [
        "Acme|waits_on|recruiter, but nothing is open"]


def test_resume_done_is_proven_by_the_file_and_its_hash(tmp_path):
    out = _problems(_root(tmp_path, "portals:\n" + _row(sha="0" * 64)))
    assert out == ["Acme|resume_file|resumes/base.pdf no longer matches the recorded sha256 (the file changed)"]
    bare = _row().split("  resume_file:")[0]
    assert _problems(_root(tmp_path / "b", "portals:\n" + bare)) == [
        "Acme|resume_file|resume done needs resume_file {path, sha256}"]


def test_a_row_that_waits_on_the_owner_is_on_the_one_list(tmp_path):
    rows = _row("Boardly", waits=OWNER, account="his-hand") + _row("Careerly", waits=OWNER, account="his-hand")
    assert _problems(_root(tmp_path, "portals:\n" + rows)) == [
        f"Careerly|waits_on|waits on {OWNER} but is not named in state/recruiter/{OWNER}-setup-list.md"]
    out = _problems(_root(tmp_path / "b", "portals:\n" + _row(), setup=None))
    assert out == [f"-|-|state/recruiter/{OWNER}-setup-list.md is missing: the owner's items must be listed in one place"]


def test_a_stale_read_back_fails(tmp_path):
    assert _problems(_root(tmp_path, "portals:\n" + _row(checked="2026-08-01"))) == [
        "Acme|last_checked|2026-08-01 is older than 30 days: read the portal again"]


def test_guard_bad_values_and_a_portal_named_twice(tmp_path):
    out = _problems(_root(tmp_path, "portals:\n" + _row(skills="mostly") + _row(waits="later")))
    assert "Acme|skills|'mostly' is not one of ['done', 'his-hand', 'n/a', 'partial', 'todo', 'unknown']" in out
    assert "Acme|portal|named twice" in out
    assert f"Acme|waits_on|'later' is not one of {sorted([OWNER, 'nobody', 'recruiter', 'time'])}" in out
