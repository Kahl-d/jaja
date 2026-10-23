"""x_api.py execute honours an outbox hold the way gmail_send.py does (A2 audit 2026-10-06: the X send path skipped
the hold check): an approval or item listed in state/outbox-*hold*.md is never sent, and X is never called."""
from __future__ import annotations

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import js_comms_util as cu
import x_api as xa
from test_x_api import NOW, TODAY, FakeX, _save, env  # noqa: F401  (env is a fixture)


def test_a_held_approval_is_never_sent(env):  # noqa: F811
    _save(env, "write")
    act = xa.build_action("dm", text="Hi Sam, saw your post about Claude Platform.", handle="sam_rivera",
                          user_id="11111", prospect="sam-rivera")
    _, path = xa.request(env["roots"], act, today=TODAY)
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-29", decided_via="chat")
    appr_id = cu.parse_approval(path).meta["id"]
    (env["js"] / "state").mkdir(exist_ok=True)
    (env["js"] / "state" / "outbox-hold-2026-09-29.md").write_text(f"| {appr_id} | held: wrong person |\n")
    fake = FakeX({("POST", "/2/dm_conversations/with/11111/messages"): (201, {"data": {"dm_event_id": "DM1"}})})
    with pytest.raises(xa.XError, match="on hold"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert fake.calls == [] and "started" not in path.read_text()
