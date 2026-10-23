"""The Portals page: the inventory, the owner's clicks and the read-back findings read from the
RECRUITER's files, one row per portal with a status per setup part; missing reads "not recorded", never "done". Browser
tests need the installed Chrome (Playwright) and are skipped without it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from test_dashboard_api import _req, _w, server, tree  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import portals_inv  # noqa: E402

INVENTORY = """# Portal inventory
updated: '2026-10-05'
portals:
- portal: LinkedIn
  priority: 1
  kind: portal + network
  url: https://www.linkedin.com/jobs/
  account: done
  profile: partial
  resume: todo
  skills: partial
  alerts: todo
  settings: partial
  waits_on: owner
  next_step: 'sitting steps 1 to 22, then alerts'
  last_checked: 2026-10-05
  messaging: todo
  applications: todo
  tools: todo
  resume_file: null
  updates:
  - at: '2026-10-04'
    what: 'profile audit read'
    by: RECRUITER
  - at: '2026-10-05'
    what: 'sitting step 1 given to Alex'
    by: RECRUITER
- portal: Wellfound
  priority: 4
  kind: portal (startups)
  url: https://wellfound.com/jobs
  account: done
  profile: done
  resume: done
  skills: done
  alerts: done
  settings: done
  waits_on: owner
  next_step: 'W16 GPA decision'
  last_checked: '2026-10-05'
- portal: MyGreenhouse
  priority: 2
  kind: ats profile
  url: https://my.greenhouse.io
  account: done
  profile: done
  resume: done
  skills: n/a
  alerts: n/a
  settings: done
  messaging: n/a
  applications: done
  tools: n/a
  waits_on: nobody
  next_step: none
  last_checked: '2026-10-05'
"""

CLICKS = """---
title: What only Alex clicks
---

# Portals

| # | where | your clicks |
|---|---|---|
| 11 | LinkedIn sitting | rounds 1 to 5 |
| 12 | Indeed | Activate on the 6 alert links |

# Decisions only you can make

| # | item | recommendation |
|---|---|---|
| 21 | Wellfound: Example Institute GPA 7.10 (W16) | remove |
"""

FINDINGS = """---
title: Portal read-back findings
---

# How to read this

Counts: Wellfound 2.

# Wellfound (2)

| # | Type | Live text (exact) | Fix | Needs Alex |
|---|---|---|---|---|
| W1 | wrong date | Education 2026 | Graduation 2025 | apply |
| W16 | off-record | Example Institute GPA 7.10 | remove | decide |

# MyGreenhouse (1)

| # | Type | Live text (exact) | Fix | Needs Alex |
|---|---|---|---|---|
| G1 | loose | C13 | fix it | apply |

# Outcomes (RECRUITER, applied live)

| portal | id | outcome |
|---|---|---|
| Wellfound | W1 | fixed 2026-10-05 |
"""


@pytest.fixture()
def inv_tree(tree):
    os_root, js = tree
    _w(js / "state" / "recruiter" / "portal-inventory.yaml", INVENTORY)
    _w(js / "state" / "recruiter" / "owner-clicks-2026-10-05.md", CLICKS)
    _w(js / "state" / "recruiter" / "portal-content" / "readback-findings-2026-10-05.md", FINDINGS)
    return os_root, js


def test_the_inventory_is_read_in_priority_order_with_not_recorded_parts(inv_tree):
    _os_root, js = inv_tree
    d = portals_inv.load(js)
    assert d["read"] and d["updated"] == "2026-10-05"
    assert [p["portal"] for p in d["portals"]] == ["LinkedIn", "MyGreenhouse", "Wellfound"]
    li, gh, wf = d["portals"]
    assert li["parts"]["messaging"] == "todo" and li["recorded"] == 9
    assert wf["parts"]["messaging"] is None and wf["recorded"] == 6                 # not recorded, never "todo"
    assert wf["complete"] is False                                                   # six done parts do not make a finished portal
    assert gh["complete"] is True and gh["done_parts"] == 9
    assert d["counts"] == {"total": 3, "complete": 1, "waits_owner": 2, "waits_recruiter": 0, "in_progress": "LinkedIn"}


def test_updates_clicks_and_findings_are_joined_per_portal(inv_tree):
    _os_root, js = inv_tree
    d = {p["portal"]: p for p in portals_inv.load(js)["portals"]}
    assert [u["what"] for u in d["LinkedIn"]["updates"]] == ["sitting step 1 given to Alex", "profile audit read"]   # newest first
    assert d["LinkedIn"]["clicks"][0]["clicks"] == "rounds 1 to 5" and d["Wellfound"]["clicks"][0]["where"].startswith("Wellfound")
    assert [f["id"] for f in d["Wellfound"]["findings"]] == ["W1", "W16"] and d["Wellfound"]["findings"][1]["needs"] == "decide"
    assert [f["id"] for f in d["MyGreenhouse"]["findings"]] == ["G1"]
    assert d["Wellfound"]["outcomes"] and "fixed 2026-10-05" in d["Wellfound"]["outcomes"][0]["cells"]
    assert d["MyGreenhouse"]["clicks"] == [] and d["MyGreenhouse"]["updates"] == []


def test_a_missing_inventory_says_so(tree):
    _os_root, js = tree
    d = portals_inv.load(js)
    assert d["read"] is False and "not found" in d["error"] and d["portals"] == []


def test_the_snapshot_and_the_page_carry_the_inventory(inv_tree):
    import json
    import threading
    import serve
    from test_dashboard_api import _free_port
    os_root, js = inv_tree
    port = _free_port()
    cache = serve.Cache(js, os_root)
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", port), serve.make_handler(cache, "tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        code, body = _req(base + "/api/snapshot")
        snap = json.loads(body)
        assert code == 200 and snap["portal_inventory"]["counts"]["total"] == 3
        code, body = _req(base + "/")
        assert code == 200 and b'src="/portals.js"' in body and b'href="/portals.css"' in body
        assert _req(base + "/portals.js")[0] == 200 and _req(base + "/portals.css")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def page(inv_tree):
    pw = pytest.importorskip("playwright.sync_api")
    import threading
    import serve
    from test_dashboard_api import _free_port
    os_root, js = inv_tree
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


def test_the_portals_page_lists_every_portal_and_opens_one(page):
    pg, base, errors = page
    pg.goto(f"{base}/?tab=portals&theme=light")
    pg.wait_for_selector("main .portals table tbody tr", timeout=60000)
    assert pg.inner_text("#title") == "Portals"
    top = pg.locator("main .portals .topline").inner_text()
    assert "1 of 3" in top and "LinkedIn" in top                                 # fully done of total; the one in progress
    rows = pg.locator("main .portals table tbody tr.portal")
    assert rows.count() == 3
    first = rows.first.inner_text()
    assert first.startswith("1") and "LinkedIn" in first
    assert pg.locator("main .portals .ui-status.none").count() >= 3              # Wellfound's unrecorded parts
    assert "done" not in pg.locator("main .portals tr.portal", has_text="Wellfound").locator(".ui-status.none").first.inner_text()
    pg.locator("main .portals tr.portal", has_text="Wellfound").click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    txt = drawer.inner_text()
    assert "Updates" in txt and "no updates recorded" in txt
    assert "W16" in txt and "decide" in txt and "remove" in txt                   # its findings and the owner's click
    assert "not recorded" in txt
    pg.keyboard.press("Escape")
    pg.locator("main .portals tr.portal", has_text="LinkedIn").click()
    drawer = pg.locator("aside.drawer")
    drawer.wait_for(timeout=5000)
    txt = drawer.inner_text()
    assert txt.index("sitting step 1") < txt.index("profile audit read")          # newest first
    assert errors == []
