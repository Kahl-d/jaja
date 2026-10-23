"""The cockpit in a real browser (Playwright on the installed Chrome) against serve.py on the fixture tree: every section renders, a drawer never prints "null" (a native replaceChildren turned null into text and
two unit-test rounds missed it), the keys move and open, and no console error. Skipped without Chrome."""
from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright.sync_api")

from test_dashboard_api import server, tree  # noqa: E402,F401  (the fixture tree and a live server)


@pytest.fixture()
def page(server):
    base, _js = server
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                        # no installed Chrome on this machine
            pytest.skip(f"Chrome not available: {exc}")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        yield pg, base, errors
        b.close()


def test_every_section_renders_and_a_drawer_never_says_null(page):
    pg, base, errors = page
    pg.goto(f"{base}/?tab=cockpit")
    pg.wait_for_selector("#machine", timeout=60000)
    for sid in ("next", "coming", "pipeline"):
        assert pg.locator(f"#{sid} h2").count() == 1, sid
    for sid in ("week", "works", "machine"):
        assert pg.locator(f"#{sid}").count() == 1, sid
    rows = pg.locator("#body .row[data-item]")
    assert rows.count() > 0
    pg.keyboard.press("j")
    pg.keyboard.press("Enter")
    drawer = pg.locator("#drawer")
    drawer.wait_for(state="visible")
    assert "null" not in drawer.inner_text() and "undefined" not in drawer.inner_text()
    pg.keyboard.press("Escape")
    assert not drawer.is_visible()
    assert errors == []
