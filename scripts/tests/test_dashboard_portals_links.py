"""Quick links on the Portals page (the DASHBOARD page): the inventory's optional `links:` list per portal
(each {label, url}; https or a repo path) passes through portals_inv.py and renders in the drawer under "Open the
portal". A portal without links is unchanged. Browser test needs the installed Chrome and is skipped without it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from test_dashboard_api import _w, tree  # noqa: F401
from test_dashboard_portals import CLICKS, FINDINGS, INVENTORY

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import portals_inv  # noqa: E402

LINKS = """  links:
  - {label: Profile, url: https://www.linkedin.com/in/alex-example/}
  - {label: Job alerts (11), url: https://www.linkedin.com/jobs/jam/}
  - {label: Messaging, url: https://www.linkedin.com/messaging/}
  - {label: Sent invitations, url: https://www.linkedin.com/mynetwork/invitation-manager/sent/}
  - {label: Who viewed, url: https://www.linkedin.com/analytics/profile-views/}
  - {label: Search appearances, url: https://www.linkedin.com/analytics/search-appearances/}
  - {label: Application settings, url: https://www.linkedin.com/jobs/application-settings/}
  - {label: Saved and applied jobs, url: https://www.linkedin.com/my-items/saved-jobs/}
  - {label: AI engineer search, url: 'https://www.linkedin.com/jobs/search/?keywords=AI&f_TPR=r86400'}
  - {label: Data export (his click), url: https://www.linkedin.com/mypreferences/d/download-my-data}
  - {label: Portal note, url: state/recruiter/portals/linkedin.md}
  - {label: Integration, url: state/recruiter/portals/linkedin-integration.md}
  - {label: '', url: https://example.com/no-label}
  - {label: No url}
"""


@pytest.fixture()
def links_tree(tree):
    os_root, js = tree
    inv = INVENTORY.replace("  resume_file: null\n", "  resume_file: null\n" + LINKS, 1)
    assert "links:" in inv
    _w(js / "state" / "recruiter" / "portal-inventory.yaml", inv)
    _w(js / "state" / "recruiter" / "owner-clicks-2026-10-05.md", CLICKS)
    _w(js / "state" / "recruiter" / "portal-content" / "readback-findings-2026-10-05.md", FINDINGS)
    return os_root, js


def test_links_pass_through_with_label_and_url_and_bad_rows_are_dropped(links_tree):
    _os_root, js = links_tree
    d = {p["portal"]: p for p in portals_inv.load(js)["portals"]}
    li = d["LinkedIn"]["links"]
    assert len(li) == 12                                                          # 14 rows: one without a label, one without a url
    assert li[0] == {"label": "Profile", "url": "https://www.linkedin.com/in/alex-example/", "kind": "web"}
    assert li[10] == {"label": "Portal note", "url": "state/recruiter/portals/linkedin.md", "kind": "file"}
    assert d["Wellfound"]["links"] == [] and d["MyGreenhouse"]["links"] == []


@pytest.fixture()
def page(links_tree):
    pw = pytest.importorskip("playwright.sync_api")
    import threading
    import serve
    from test_dashboard_api import _free_port
    os_root, js = links_tree
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


def test_the_drawer_lists_the_links_under_open_the_portal(page):
    pg, base, errors = page
    pg.goto(f"{base}/?tab=portals&theme=light")
    pg.wait_for_selector("main .portals table tbody tr.portal", timeout=60000)
    pg.locator("main .portals tr.portal", has_text="LinkedIn").click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    links = drawer.locator(".quick-links a")
    assert links.count() == 12
    assert links.first.inner_text() == "Profile" and links.first.get_attribute("href") == "https://www.linkedin.com/in/alex-example/"
    assert links.first.get_attribute("target") == "_blank"
    note = drawer.locator(".quick-links a", has_text="Portal note")
    assert note.get_attribute("href").startswith("obsidian://open?vault=workspace&file=")                 # a repo path opens in Obsidian
    assert "state%2Frecruiter%2Fportals%2Flinkedin.md" in note.get_attribute("href")
    txt = drawer.inner_text()
    assert txt.index("Open the portal") < txt.index("Profile") < txt.index("Setup parts")
    pg.keyboard.press("Escape")
    pg.locator("main .portals tr.portal", has_text="Wellfound").click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    assert drawer.locator(".quick-links").count() == 0                                            # no links, nothing extra
    assert errors == []
