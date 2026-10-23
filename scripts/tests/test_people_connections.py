"""People and their connections. The snapshot carries each prospect's introduced_by and
asked_by (prospects.py INTRO_FIELDS) and who they introduced; the People page shows them in the drawer
and in a Connections view. Browser tests need the installed Chrome and are skipped without it."""
from __future__ import annotations

import pytest

from test_dashboard_api import _w, fm, server, snap, tree  # noqa: F401


@pytest.fixture()
def linked(tree):
    os_root, js = tree
    _w(js / "prospects" / "jordan-lee.md", fm.dump({"id": "jordan-lee", "name": "Jordan Lee", "company": "Beta", "company_slug": "beta",
                                                 "status": "replied", "priority": "P1", "warmth": 3, "linkedin_url": "https://www.linkedin.com/in/jordan-lee"},
                                                "# Jordan Lee\n"))
    _w(js / "prospects" / "priya-nair.md", fm.dump({"id": "priya-nair", "name": "Priya Nair", "company": "Beta", "company_slug": "beta",
                                                 "status": "replied", "priority": "P1", "warmth": 2, "introduced_by": "jordan-lee", "asked_by": "jordan-lee",
                                                 "applications": ["beta-agent-engineer-202609"]}, "# Priya Nair\n"))
    return os_root, js


def test_the_snapshot_carries_who_introduced_whom(linked):
    import dash_api
    from datetime import datetime, timezone
    os_root, js = linked
    s = dash_api.build(js, os_root, datetime(2026, 10, 4, 12, tzinfo=timezone.utc), load=True)
    rows = {p["id"]: p for p in s["people"]["prospects"]}
    assert rows["priya-nair"]["introduced_by"] == "jordan-lee" and rows["priya-nair"]["asked_by"] == "jordan-lee"
    assert rows["jordan-lee"]["introduced"] == ["priya-nair"] and rows["jordan-lee"]["asked_for"] == ["priya-nair"]
    assert rows["jordan-lee"]["linkedin_url"] == "https://www.linkedin.com/in/jordan-lee"
    assert rows["sam-roe"]["introduced_by"] is None and rows["sam-roe"]["introduced"] == []
    assert s["people"]["counts"]["connected"] == 2


@pytest.fixture()
def page(linked):
    pw = pytest.importorskip("playwright.sync_api")
    import threading
    import serve
    from test_dashboard_api import _free_port
    os_root, js = linked
    port = _free_port()
    cache = serve.Cache(js, os_root)
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", port), serve.make_handler(cache, "tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Chrome not available: {exc}")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        yield pg, f"http://127.0.0.1:{port}", errors
        b.close()
    httpd.shutdown()
    httpd.server_close()


def test_the_people_drawer_and_the_connections_view_show_the_introduction(page):
    pg, base, errors = page
    pg.goto(f"{base}/?tab=people&theme=light")
    pg.wait_for_selector("main table", timeout=60000)
    pg.locator("tr", has_text="Priya Nair").first.click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    txt = drawer.inner_text()
    assert "Introduced by" in txt and "Jordan Lee" in txt and "Asked by" in txt
    pg.keyboard.press("Escape")
    pg.locator("main .filters button", has_text="Connections").click()
    pg.wait_for_selector("main .ui-edge", timeout=10000)
    edges = pg.locator("main .ui-edge")
    assert edges.count() >= 1
    card = pg.locator("main .c-card", has_text="Jordan Lee")
    assert "Jordan Lee" in card.inner_text() and "Priya Nair" in edges.first.inner_text() and "introduced" in edges.first.inner_text()
    assert pg.locator("main .ui-person").count() >= 2                     # the same Person component as everywhere
    assert errors == []
