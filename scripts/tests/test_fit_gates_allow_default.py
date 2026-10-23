"""fit_gates allow_default: an experienced candidate may still want to apply to new-grad roles.
targets/fit-gates.yaml `allow_default: [new-grad]` lets new-grad roles through by default; --allow still adds more; --no-default-allow restores the old gate. A built-in default_config()
is unchanged (no file, no default allowance)."""
from __future__ import annotations

from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403

import fit_gates as fg  # noqa: E402
from test_fit_gates import gate  # noqa: E402

from layout import JS  # noqa: E402
TITLE = "New Grad Software Engineer"
WINDOW_IN = "Class of 2026 graduates are encouraged to apply."
WINDOW_OUT = "This is a new grad program for 2027 graduates."


def root_with(tmp_path, text):
    (tmp_path / "targets").mkdir(parents=True)
    if text is not None:
        (tmp_path / "targets" / "fit-gates.yaml").write_text(text)
    return tmp_path


@pytest.mark.real_records
def test_the_real_config_allows_new_grad_by_default():
    assert fg.load_config(JS).allow >= {"new-grad"}


def test_a_new_grad_role_passes_with_the_default_and_notes_its_window(tmp_path):
    cfg = fg.load_config(root_with(tmp_path, "allow_default: [new-grad]\n"))
    out = gate("", config=cfg, title=TITLE)
    assert out["gate"] == "pass"
    for jd, words in ((WINDOW_IN, "includes 2026-05"), (WINDOW_OUT, "does not include 2026-05"), ("", "no graduation window")):
        out = gate(jd, config=cfg, title=TITLE)
        assert out["gate"] == "pass" and any(words in r for r in out["reasons"]), (jd, out["reasons"])


def test_without_the_file_new_grad_still_fails_as_before(tmp_path):
    cfg = fg.load_config(root_with(tmp_path, None))
    assert cfg.allow == set()
    assert gate("", config=cfg, title=TITLE)["gate"] == "fail"
    assert gate(WINDOW_IN, config=cfg, title=TITLE)["gate"] == "review"      # the window review where not allowed


def test_the_cli_allow_adds_to_the_default_and_no_default_allow_removes_it(tmp_path):
    root = root_with(tmp_path, "allow_default: [new-grad]\n")
    assert fg.load_config(root, allow=["staff"]).allow == {"new-grad", "staff"}
    assert fg.load_config(root, allow=["staff"], use_defaults=False).allow == {"staff"}


def test_an_unknown_or_malformed_allowance_is_refused(tmp_path):
    for text in ("allow_default: [new-grad, nonsense]\n", "allow_default: new-grad\n", "allow_default: [1]\n"):
        try:
            fg.load_config(root_with(tmp_path / str(abs(hash(text))), text))
        except ValueError as exc:
            assert "allow_default" in str(exc)
        else:
            raise AssertionError(text)


def test_other_fail_levels_are_unaffected_by_the_new_grad_default(tmp_path):
    cfg = fg.load_config(root_with(tmp_path, "allow_default: [new-grad]\n"))
    assert gate("", config=cfg, title="Principal Engineer")["gate"] == "fail"
    assert gate("", config=cfg, title="Machine Learning Engineering Intern")["gate"] == "fail"


def test_an_early_seat_asking_years_keeps_its_note(tmp_path):
    cfg = fg.load_config(root_with(tmp_path, "allow_default: [new-grad]\n"))
    out = gate("You have 2+ years of experience.", config=cfg, title="Early Career Software Engineer")
    assert out["gate"] == "pass" and any("early-career seat asking 2 years" in r for r in out["reasons"])


def test_the_cli_flag_exists(tmp_path, capsys):
    root = root_with(tmp_path, "allow_default: [new-grad]\n")
    (root / "jobs").mkdir()
    assert fg.main(["--root", str(root), "--dry-run", "--no-default-allow"]) == 0
    assert "evaluated 0" in capsys.readouterr().out
