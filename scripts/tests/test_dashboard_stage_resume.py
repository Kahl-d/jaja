"""Rejection post-mortem 2026-10-02, change 3, on the CRM: logging "applied" from the dashboard names the page that
went (--resume, or none with a reason), as tracker.py requires. Fixture tree only (test_dashboard_api's)."""
from __future__ import annotations

import pytest

from test_dashboard_api import da, tree  # noqa: F401  (the fixture tree)


def test_applied_from_the_crm_passes_the_resume(tree):  # noqa: F811
    _os_root, js = tree
    argv = da.command_for({"op": "stage", "slug": "beta-agent-engineer-202609", "type": "applied",
                           "evidence": "Alex submitted on the Ashby form", "resume": "none",
                           "reason": "a referral form with no upload"}, js)
    assert argv[argv.index("--resume") + 1] == "none" and argv[argv.index("--reason") + 1] == "a referral form with no upload"


def test_applied_without_a_resume_is_refused_before_running(tree):  # noqa: F811
    _os_root, js = tree
    with pytest.raises(da.WriteRefused, match="resume"):
        da.command_for({"op": "stage", "slug": "beta-agent-engineer-202609", "type": "applied",
                        "evidence": "Alex submitted"}, js)
    argv = da.command_for({"op": "stage", "slug": "beta-agent-engineer-202609", "type": "screen",
                           "evidence": "invite"}, js)
    assert "--resume" not in argv                     # only applied names a page
