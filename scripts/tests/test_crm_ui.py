"""The CRM pages in a real browser (Playwright on the installed Chrome) against serve.py on the fixture tree: a deal card opens its drawer, a card says "lane: not claimed" until a lane writes claims:, People's drawer shows
the timeline, "/" focuses the page's search, j/k move the selected row and Enter opens it. Skipped without Chrome."""
from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright.sync_api")

from test_dashboard_api import _w, fm, server, tree  # noqa: E402,F401  (the fixture tree and a live server)


@pytest.fixture()
def page(server):
    base, js = server
    _w(js / "prospects" / "pat-lee.md", fm.dump({"id": "pat-lee", "name": "Pat Lee", "company": "Beta", "company_slug": "beta",
                                                 "status": "replied", "priority": "P1", "warmth": 2,
                                                 "applications": ["beta-agent-engineer-202609"]},
                                                "# Pat Lee\n\n## Timeline\n- 2026-09-30T17:00:00Z · in · email · Thanks, let's talk.\n"))
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                        # no installed Chrome on this machine
            pytest.skip(f"Chrome not available: {exc}")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        yield pg, base
        b.close()


def _go(pg, base, tab):
    pg.goto(f"{base}/?tab={tab}&theme=light")
    pg.wait_for_selector("main .c-grid, main .c-board, main table, main .c-head", timeout=60000)


def test_a_deal_card_opens_its_drawer_and_says_lane_not_claimed(page):
    pg, base = page
    _go(pg, base, "pipeline")
    card = pg.locator(".c-deal").first
    assert "lane: not claimed" in card.inner_text()
    card.click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    assert "Lane: not claimed by a lane" in drawer.inner_text()


def test_people_drawer_shows_the_timeline(page):
    pg, base = page
    _go(pg, base, "people")
    pg.locator("tr", has_text="Pat Lee").first.click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    assert "Timeline" in drawer.inner_text() and "09-30 10:00" in drawer.inner_text() and "Thanks, let's talk." in drawer.inner_text()


def test_slash_focuses_search_and_j_k_enter_walk_the_rows(page):
    pg, base = page
    _go(pg, base, "companies")
    pg.keyboard.press("/")
    assert pg.evaluate("document.activeElement && document.activeElement.type") == "search"
    pg.keyboard.press("Escape")
    pg.locator("body").click(position={"x": 5, "y": 5})
    pg.keyboard.press("j")
    pg.keyboard.press("j")
    assert pg.locator("main tbody tr.sel").count() == 1
    first_two = pg.locator("main tbody tr")
    assert "sel" in (first_two.nth(1).get_attribute("class") or "")
    pg.keyboard.press("k")
    assert "sel" in (first_two.nth(0).get_attribute("class") or "")
    pg.keyboard.press("Enter")
    pg.locator("aside.drawer").wait_for(timeout=5000)


def _selected(pg):
    return pg.locator('nav.tabs button[aria-selected="true"]').inner_text().split("\n")[0].strip()


def test_it_always_opens_on_the_cockpit_even_after_another_page_was_last(page):
    pg, base = page
    _go(pg, base, "pipeline")
    pg.locator("nav.tabs button", has_text="Jobs").click()             # the app used to remember this as the start page
    pg.wait_for_selector("main table", timeout=60000)
    pg.goto(base + "/")
    pg.wait_for_selector("main .cockpit .pane", timeout=60000)         # the cockpit is the first page
    assert pg.inner_text("#title") == "Cockpit" and _selected(pg) == "Cockpit"


def test_hash_deep_links_and_the_title_and_nav_stay_in_sync(page):
    pg, base = page
    pg.goto(base + "/#pipeline")
    assert pg.inner_text("#title") == "Pipeline"                       # set from the hash before the data arrives
    pg.wait_for_selector("main .c-board, main .c-head", timeout=60000)
    assert _selected(pg) == "Pipeline"
    pg.locator("nav.tabs button", has_text="Companies").click()
    pg.wait_for_selector("main .c-table", timeout=60000)
    assert pg.evaluate("location.hash") == "#companies" and pg.inner_text("#title") == "Companies" and _selected(pg) == "Companies"
    pg.go_back()
    pg.wait_for_selector("main .c-board, main .c-head", timeout=60000)
    assert pg.inner_text("#title") == "Pipeline" and _selected(pg) == "Pipeline"


@pytest.mark.real_records
def test_the_feed_toggles_between_since_last_visit_and_the_last_7_days(server):
    base, js = server
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Chrome not available: {exc}")
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script("localStorage.setItem('jad.lastVisit', JSON.stringify('2026-10-02T10:30:00+00:00'))")
        pg = ctx.new_page()
        pg.goto(base + "/#cockpit")
        pg.wait_for_selector("main .cockpit .foot", timeout=60000)
        assert "Since your last visit" in pg.locator("#feed").inner_text()
        pg.locator("#feed").click()                                        # the feed lives in the cockpit's drawer
        feed = pg.locator("#drawer")
        feed.wait_for(state="visible")
        assert "Since your last visit" in feed.inner_text() and "Pat Lee" not in feed.inner_text()   # sent 10:00Z, before the visit
        feed.locator("button", has_text="Last 7 days").click()
        feed = pg.locator("#drawer")
        assert "Last 7 days" in feed.inner_text() and "Pat Lee" in feed.inner_text()
        b.close()
