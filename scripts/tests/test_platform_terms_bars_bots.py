"""platform_yield.py --check: the terms words and the ignored-sender kinds.

1. agent_terms `bars-bots`: some job boards bar bots and automating applies on the platform itself. A registry that
   said `none` would read as permission. bars-bots says it, and it may stay active or probation because the channel
   is the owner's own alert mail and the apply is the owner's click or the employer's site (charter section 7).
2. Every ignored sender carries a kind (job-alert, marketing or noise) besides its why and date.
3. The committed registry: every platform that bars bots records the clause it was read from and the date, and
   every ignored sender has a kind that the alert parser honours."""
from __future__ import annotations

from datetime import date

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import job_alerts as ja  # noqa: E402
import platform_yield as py  # noqa: E402
from js_common import JS_ROOT, fm  # noqa: E402
from test_platform_yield import NEVER_PROBATION, _linkedin, _linkedin_errors  # noqa: E402


@pytest.mark.parametrize("status", ["probation", "active", "manual", "blocked"])
def test_bars_bots_is_a_word_and_never_forces_a_retirement(root, status):
    _linkedin(root, status, "bars-bots")
    assert _linkedin_errors(root) == []


def test_names_agents_still_forces_manual(root):
    _linkedin(root, "active", "names-agents")
    assert any(NEVER_PROBATION in e for e in _linkedin_errors(root))


def _ignored(root, rows: str) -> list[str]:
    _linkedin(root, "manual", "none")
    path = root / "targets" / "platforms.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "ignored_senders:\n" + rows, encoding="utf-8")
    return [e for e in py.check(root, date(2026, 10, 13))[0] if e.startswith("ignored_senders")]


def test_an_ignored_sender_needs_a_kind(root):
    assert _ignored(root, "  - {sender: spam.example, why: pings, decided: 2026-10-01}\n") == [
        "ignored_senders spam.example: kind None is not one of job-alert, marketing, noise"]
    assert _ignored(root, "  - {sender: spam.example, why: pings, decided: 2026-10-01, kind: noise}\n") == []
    assert _ignored(root, "  - {sender: spam.example, kind: noise}\n") == [
        "ignored_senders spam.example: needs sender, why and decided"]


def _registry() -> dict:
    return fm.parse_yaml((JS_ROOT / "targets" / "platforms.yaml").read_text(encoding="utf-8"))


@pytest.mark.real_records
def test_the_committed_terms_records():
    reg = {p["id"]: p for p in _registry()["platforms"]}
    barring = [p for p in reg.values() if p.get("agent_terms") == "bars-bots"]
    assert barring, "no platform records a bars-bots clause"
    for p in barring:
        assert str(p["tos"]["quote"]).strip() and str(p["tos"]["read_on"])[:4].isdigit(), p["id"]
    for p in reg.values():
        assert p.get("agent_terms"), p["id"]
        assert str(p["tos"].get("read_on") or "").strip(), p["id"]
    assert [e for e in py.check(JS_ROOT, date.today())[0] if "adapter" not in e] == []   # adapters live in the code root


@pytest.mark.real_records
def test_every_ignored_sender_has_a_kind_the_parser_honours():
    platforms = ja.load_platforms(JS_ROOT)
    ignored = ja.load_ignored(JS_ROOT)
    rows = _registry().get("ignored_senders") or []
    assert rows, "no ignored senders recorded"
    for r in rows:
        assert r["kind"] in ("job-alert", "marketing", "noise"), r["sender"]
        row = {"from_email": f"someone@{r['sender']}"}
        assert ja.platform_for(row, platforms) is None, r["sender"]
        assert ja.ignored_reason(row, ignored), r["sender"]
