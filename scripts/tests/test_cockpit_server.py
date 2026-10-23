"""The cockpit's route beside the old dashboard, on the dashboard's own server fixture."""
from __future__ import annotations

import json

from test_dashboard_api import _req, server, tree  # noqa: F401  (the dashboard's own fixtures)


def test_the_server_serves_the_cockpit_inside_the_shell(server):
    base, js = server
    code, body = _req(base + "/")                                             # one shell, the cockpit its first tab
    assert code == 200 and b'src="/cockpit.js"' in body and b'src="/ui.js"' in body
    assert _req(base + "/cockpit.js")[0] == 200 and _req(base + "/cockpit.css")[0] == 200
    code, body = _req(base + "/api/cockpit")
    data = json.loads(body)
    assert code == 200 and {"next", "week", "works", "coming", "pipeline", "machine"} <= set(data)
