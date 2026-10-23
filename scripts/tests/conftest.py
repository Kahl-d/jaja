"""Suite-wide backstop (I-025): no private hold from profile/private-holds.txt reaches a test report.

Every report (failure text, skip reason, captured stdout, stderr and log sections) is scrubbed with
held_safe.scrub before pytest prints it. Tests that read the real holds still compare without values
(held_safe.py); this catches what they miss.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import held_safe as hs  # noqa: E402


def scrub_report(report) -> None:
    """Mask private holds in a TestReport in place; a changed failure text becomes plain text."""
    lr = report.longrepr
    if isinstance(lr, tuple) and len(lr) == 3:                         # a skip: (path, line, reason)
        report.longrepr = (lr[0], lr[1], hs.scrub(lr[2]))
    elif lr is not None:
        text = str(lr)
        clean = hs.scrub(text)
        if clean != text:
            report.longrepr = clean
    report.sections = [(title, hs.scrub(content)) for title, content in report.sections]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if hs.private_held():
        scrub_report(outcome.get_result())


def pytest_configure(config):
    # SETUP-H3 item 3 (2026-10-01): tests that pin the department's real records (the real facts.yaml, evidence.yaml,
    # PROFILE.md). The full suite runs them; an eval sandbox with fixture records deselects them:
    #   python3 -m pytest scripts/tests -m "not real_records"
    config.addinivalue_line("markers", "workspace: reads the owner's workspace (an Obsidian vault with brain/ and .gitignore) as the runtime root; skipped elsewhere")
    config.addinivalue_line("markers", "real_records: reads the real profile records; deselect in a fixture sandbox")
    for name, mods in OPTIONAL_STACKS.items():
        config.addinivalue_line("markers", f"{name}: needs the optional package(s) {', '.join(mods)} (see requirements-optional.txt); skipped when absent")


# optional extras: a test that exercises one of these marks itself; without the package it is skipped, never failed
OPTIONAL_STACKS = {"needs_google": ("google.oauth2", "googleapiclient"), "needs_rendercv": ("rendercv",), "needs_jinja2": ("jinja2",)}


def _optional_skips(items):
    import importlib.util

    def present(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            return False
    for name, mods in OPTIONAL_STACKS.items():
        if all(present(m) for m in mods):
            continue
        skip = pytest.mark.skip(reason=f"{name}: optional package(s) {', '.join(mods)} not installed")
        for item in items:
            if name in item.keywords:
                item.add_marker(skip)


def pytest_collection_modifyitems(config, items):
    _optional_skips(items)
    """A `workspace` test needs the owner's vault as the runtime root (JAJA_RUNTIME); anywhere else it is skipped."""
    import agency_paths
    if not (agency_paths.RUNTIME_ROOT / ".obsidian").is_dir():
        skip_ws = pytest.mark.skip(reason="workspace: the runtime root is not the owner's vault")
        for item in items:
            if "workspace" in item.keywords:
                item.add_marker(skip_ws)
    _eval_sandbox_skips(config, items)


def _eval_sandbox_skips(config, items):
    """Inside an eval sandbox (a `.eval-sandbox` marker next to this scripts/ copy, the signal gmail_auth.config_dir
    uses) a real_records test is skipped, so the corpus-keeper card's plain pytest command works in both places."""
    if not (Path(__file__).resolve().parents[2] / ".eval-sandbox").is_file():
        return
    skip = pytest.mark.skip(reason="real_records: an eval sandbox holds fixture records, not the real profile")
    for item in items:
        if "real_records" in item.keywords:
            item.add_marker(skip)
