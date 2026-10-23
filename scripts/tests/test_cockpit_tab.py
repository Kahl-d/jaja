"""The cockpit as a tab inside the CRM shell (a section the owner clicks rather than reaching by changing the URL). The server test runs everywhere; the browser test
needs the installed Chrome (Playwright) and is skipped without it. The standalone /cockpit route stays as it was
(test_cockpit_server.py, test_cockpit_ui.py)."""
from __future__ import annotations

import pytest

from test_dashboard_api import _req, server, tree  # noqa: F401  (the dashboard's own fixtures)


def test_the_crm_page_loads_the_cockpit_script_and_styles(server):
    base, _js = server
    code, body = _req(base + "/")
    assert code == 200
    assert b'src="/cockpit.js"' in body and b'href="/cockpit.css"' in body


def test_the_cockpit_styles_do_not_redefine_the_crm_tokens(server):
    """cockpit.css once set --surface on :root; loaded beside style.css that recolored every CRM card."""
    _base, _js = server
    from pathlib import Path
    import serve
    css = (Path(serve.__file__).parent / "cockpit.css").read_text(encoding="utf-8")
    assert ":root {" not in css and ":root:not(" not in css


@pytest.fixture()
def page(server):
    pw = pytest.importorskip("playwright.sync_api")
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


def test_the_cockpit_tab_renders_every_section_inside_the_shell(page):
    pg, base, errors = page
    pg.goto(f"{base}/?tab=cockpit&theme=light")
    pg.wait_for_selector("main .cockpit #machine", timeout=60000)
    assert pg.locator('nav.tabs button[aria-selected="true"]').inner_text().startswith("Cockpit")
    foot = pg.locator("main .cockpit .foot").inner_text()
    assert "null" not in foot and "built" in foot                   # an empty slot once printed "null"
    for sid in ("next", "coming", "pipeline"):
        assert pg.locator(f"main .cockpit #{sid} h2").count() == 1, sid
    for sid in ("week", "works", "machine"):
        assert pg.locator(f"main .cockpit #{sid}").count() == 1, sid
    pg.keyboard.press("j")
    pg.keyboard.press("Enter")
    drawer = pg.locator("#drawer")
    drawer.wait_for(state="visible")
    assert "null" not in drawer.inner_text() and "undefined" not in drawer.inner_text()
    pg.keyboard.press("Escape")
    assert not drawer.is_visible()
    # the tab is reached by a click, not a URL: Pipeline, then the Cockpit tab
    pg.locator("nav.tabs button", has_text="Pipeline").click()
    pg.wait_for_selector("main .c-board, main .c-head", timeout=60000)
    pg.locator("nav.tabs button", has_text="Cockpit").click()
    pg.wait_for_selector("main .cockpit #next", timeout=60000)
    assert errors == []
