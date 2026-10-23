"""Channel routing on the day queue: every item carries a channel (linkedin, x, t1, t2); a lane claims
with --channel only its own; a claim without --channel is today's behaviour; build sets the channel from the job's
source (platform id to tier as in the lane tree)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403
from test_sweep_cards_queue import T0, _listing, _sweep_doc, cards, dq

OUT_DAY = "2026-10-06"


@pytest.mark.parametrize("fields,channel", [
    ({"source": "linkedin"}, "linkedin"),
    ({"source": "linkedin-alert"}, "linkedin"),
    ({"source": "x"}, "x"),
    ({"source": "x-jobs"}, "x"),
    ({"source": "ziprecruiter-alert"}, "t1"),
    ({"source": "glassdoor-alert"}, "t1"),
    ({"source": "indeed"}, "t1"),
    ({"source": "yc"}, "t1"),
    ({"source": "greenhouse"}, "t2"),
    ({"source": "ashby"}, "t2"),
    ({"source": "workday"}, "t2"),
    ({"source": "sitemap"}, "t2"),
    ({"source": "hn"}, "t2"),
    ({"source": "himalayas"}, "t2"),
    ({"source": "manual"}, "t2"),
    # alert mail goes by the portal that sent it (the job's `platform`), not by the board it lists
    ({"source": "email-alert", "platform": "google-jobs-search", "alert_board": "Workday"}, "t1"),
    ({"source": "email-alert", "platform": "indeed"}, "t1"),
    ({"source": "email-alert", "platform": "wttj"}, "t2"),
    ({"source": "email-alert", "platform": "linkedin"}, "linkedin"),
    ({"source": "email-alert"}, "t2"),
])
def test_channel_for_job_maps_source_and_sender_to_a_channel(fields, channel):
    assert dq.channel_for_job(fields) == channel


def test_a_link_with_no_job_record_goes_by_its_host():
    assert dq.channel_for_url("https://www.linkedin.com/jobs/view/1/") == "linkedin"
    assert dq.channel_for_url("https://x.com/someone/status/1") == "x"
    assert dq.channel_for_url("https://www.indeed.com/viewjob?jk=1") == "t1"
    assert dq.channel_for_url("https://jobs.ashbyhq.com/acme") == "t2"
    assert dq.channel_for_url(None) is None


def _add(out, kind, title, channel=None):
    return dq.add(out, OUT_DAY, T0, kind, title, channel=channel)


def test_add_records_the_channel_and_refuses_an_unknown_one(root):
    out = root / "state" / "recruiter"
    assert _add(out, "mass", "a", "t1")["channel"] == "t1"
    assert _add(out, "mass", "b")["channel"] is None
    with pytest.raises(dq.QueueError):
        _add(out, "mass", "c", "myspace")


def test_claim_with_a_channel_takes_only_that_channels_items(root):
    out = root / "state" / "recruiter"
    _add(out, "mass", "li-1", "linkedin")
    _add(out, "mass", "t2-1", "t2")
    _add(out, "mass", "t1-1", "t1")
    got = dq.claim(out, OUT_DAY, T0, "T2-APPLY", "mass", channel="t2")
    assert got["title"] == "t2-1" and got["claimed_by"] == "T2-APPLY"
    assert dq.claim(out, OUT_DAY, T0, "T2-APPLY", "mass", channel="t2") is None      # none left in t2
    assert dq.claim(out, OUT_DAY, T0, "LI", "mass", channel="linkedin")["title"] == "li-1"


def test_claim_without_a_channel_keeps_todays_behaviour(root):
    out = root / "state" / "recruiter"
    _add(out, "mass", "li-1", "linkedin")
    _add(out, "mass", "none-1")
    first = dq.claim(out, OUT_DAY, T0, "MASS-APPLY", "mass")
    second = dq.claim(out, OUT_DAY, T0, "MASS-APPLY", "mass")
    assert [first["title"], second["title"]] == ["li-1", "none-1"]                   # rank order, channel ignored


def test_a_channel_claim_never_takes_an_item_without_a_channel(root):
    out = root / "state" / "recruiter"
    _add(out, "mass", "no-channel")
    assert dq.claim(out, OUT_DAY, T0, "T1-APPLY", "mass", channel="t1") is None


def _files(out: Path, listings: list[dict]) -> None:
    res = cards.build(out.parents[1], _sweep_doc({"listings": listings}), max_listings=0)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards-2026-10-06.json").write_text(json.dumps(res), encoding="utf-8")
    md = cards.render(res)
    batch = next((c["n"] for c in res["cards"] if c["action"] == cards.MASS_BATCH), None)
    answer = f"yes {batch}" if batch else ""
    (out / "cards-2026-10-06.md").write_text(md.replace("decisions:", f"decisions: {answer}"), encoding="utf-8")


def _job(root: Path, jid: str, **meta) -> None:
    meta = {"type": "job", "id": jid, "company": jid, "company_slug": jid, "title": "AI Engineer", **meta}
    (root / "jobs").mkdir(exist_ok=True)
    (root / "jobs" / f"{jid}.md").write_text("---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in meta.items())
                                              + "\n---\n# job\n", encoding="utf-8")


def test_build_sets_each_mass_items_channel_from_the_jobs_source(root):
    out = root / "state" / "recruiter"
    _job(root, "gh-1", source="greenhouse")
    _job(root, "li-1", source="linkedin-alert")
    _job(root, "zr-1", source="ziprecruiter-alert")
    _job(root, "mail-1", source="email-alert", platform="indeed")
    _files(out, [_listing(j, 50, "Remote US", company=j, slug=j) for j in ("gh-1", "li-1", "zr-1", "mail-1", "nojob")])
    dq.build(out, OUT_DAY, T0, root=root)
    items = {i["links"]["job"]: i for i in dq.load(out / f"queue-{OUT_DAY}.yaml")["items"]}
    assert {j: i["channel"] for j, i in items.items()} == {
        "gh-1": "t2", "li-1": "linkedin", "zr-1": "t1", "mail-1": "t1",
        "nojob": "t2"}                       # no job record: the listing's own url (example.com) is a career page


def test_non_mass_items_carry_a_null_channel(root):
    out = root / "state" / "recruiter"
    assert _add(out, "focused", "a person")["channel"] is None


def test_the_cli_takes_channel_on_add_and_claim(root, capsys):
    base = ["--root", str(root), "--date", OUT_DAY, "--now", "2026-10-06T15:00:00Z"]
    assert dq.main(base + ["add", "--kind", "mass", "--title", "x", "--channel", "x"]) == 0
    assert dq.main(base + ["claim", "--lane", "X", "--kind", "mass", "--channel", "t1"]) == 1
    capsys.readouterr()
    assert dq.main(base + ["claim", "--lane", "X", "--kind", "mass", "--channel", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["channel"] == "x"
    with pytest.raises(SystemExit):                                  # argparse refuses an unknown channel
        dq.main(base + ["add", "--kind", "mass", "--title", "y", "--channel", "bogus"])


def test_show_prints_and_filters_by_channel(root):
    out = root / "state" / "recruiter"
    _add(out, "mass", "one", "t1")
    _add(out, "mass", "two", "t2")
    data = dq.load(out / f"queue-{OUT_DAY}.yaml")
    assert "[mass E2 t1]" in dq.render(data) and "two" not in dq.render(data, channel="t1")


@pytest.mark.real_records
def test_every_mapped_platform_id_exists_in_platforms_yaml(root):
    from js_common import JS_ROOT
    ids = {p["id"] for p in yaml.safe_load((JS_ROOT / "targets" / "platforms.yaml").read_text())["platforms"]}
    mapped = set(dq.T1_PLATFORMS) | set(dq.X_PLATFORMS) | set(dq.LINKEDIN_PLATFORMS)
    assert mapped <= ids, mapped - ids


def test_build_gives_an_existing_channelless_mass_item_its_channel(root):
    out = root / "state" / "recruiter"
    _job(root, "li-9", source="linkedin")
    old = _add(out, "mass", "made before channels", None)
    path = out / f"queue-{OUT_DAY}.yaml"
    data = dq.load(path)
    data["items"][0]["links"] = {"job": "li-9"}
    path.write_text(yaml.safe_dump(data))
    _files(out, [])
    dq.build(out, OUT_DAY, T0, root=root)
    assert dq.load(path)["items"][0]["channel"] == "linkedin" and old["id"] == dq.load(path)["items"][0]["id"]
