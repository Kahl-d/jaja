"""The cockpit is the first page, built from the shared component library (ui.js) as a
workbench of panes; the old Home folds into it; /cockpit redirects into the shell. Browser tests need the installed
Chrome (Playwright) and are skipped without it."""
from __future__ import annotations

import pytest

from test_dashboard_api import _req, server, tree  # noqa: F401


def test_the_page_loads_the_component_library(server):
    base, _js = server
    code, body = _req(base + "/")
    assert code == 200 and b'src="/ui.js"' in body and b'href="/ui.css"' in body
    assert _req(base + "/ui.js")[0] == 200 and _req(base + "/ui.css")[0] == 200


def test_cockpit_path_redirects_into_the_shell(server):
    base, _js = server
    import urllib.request
    import urllib.error
    req = urllib.request.Request(base + "/cockpit")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req)
        code, loc = resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        code, loc = e.code, e.headers.get("Location")
    assert code in (301, 302, 303, 307, 308) and loc == "/#cockpit"


class _NoRedirect(__import__("urllib.request").request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401
        return None


@pytest.fixture()
def page(server):
    pw = pytest.importorskip("playwright.sync_api")
    base, _js = server
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Chrome not available: {exc}")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg, base, errors
        b.close()


def test_the_cockpit_opens_first_as_a_workbench_of_panes(page):
    pg, base, errors = page
    pg.goto(f"{base}/?theme=light")
    pg.wait_for_selector("main .cockpit .pane", timeout=60000)
    assert pg.inner_text("#title") == "Cockpit"
    assert pg.locator('nav.tabs button[aria-selected="true"]').inner_text().startswith("Cockpit")
    assert pg.locator("nav.tabs button", has_text="Home").count() == 0           # Home folded into the cockpit
    panes = pg.locator("main .cockpit .pane")
    assert panes.count() == 3                                                  # Next, Coming up, Pipeline; the strip and the foot are not panes
    for name in ("Next", "Coming up", "Pipeline"):
        assert pg.locator("main .cockpit .pane h2", has_text=name).count() == 1, name
    # the strip and the footer carry this week, what works and the machine line
    assert pg.locator("main .cockpit .strip").count() == 1
    assert "of" in pg.locator("main .cockpit .strip").inner_text()                # "k of n", never a bare percent
    assert "%" not in pg.locator("main .cockpit .pane", has_text="Next").locator(".row").first.inner_text()
    assert pg.locator("main .cockpit .foot").count() == 1
    # the whole workbench fits the viewport at 1440x900: the page itself does not scroll
    assert pg.evaluate("document.documentElement.scrollHeight") <= 900 + 4
    # keys: j selects, Enter opens, Esc closes; the drawer never says null
    pg.keyboard.press("j"); pg.keyboard.press("Enter")
    drawer = pg.locator("#drawer"); drawer.wait_for(state="visible")
    assert "null" not in drawer.inner_text() and "undefined" not in drawer.inner_text()
    pg.keyboard.press("Escape"); assert not drawer.is_visible()
    assert errors == []


def test_the_components_render_a_person_a_deal_and_a_rate_the_same_way_everywhere(page):
    pg, base, errors = page
    pg.goto(f"{base}/?theme=light")
    pg.wait_for_selector("main .cockpit .pane", timeout=60000)
    assert pg.locator("main .cockpit .ui-deal").count() >= 1
    assert pg.locator("main .cockpit .ui-stage").count() >= 1
    assert pg.locator("main .cockpit .ui-rate").count() >= 2                   # warm and cold
    pg.locator("nav.tabs button", has_text="Pipeline").click()
    pg.wait_for_selector("main .c-board, main .c-head", timeout=60000)
    assert errors == []
