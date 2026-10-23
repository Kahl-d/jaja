"""hiring_signal on the job schema: posts.py writes `hiring_signal: {url, date, by}` on each
job it makes from a hiring post (rows from fixtures/posts). schemas/job.yaml lists the field, and
check_records.py reads it as a mapping, never as a string field. Nothing touches the network."""
from __future__ import annotations

import yaml

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import check_records as cr
import js_common as jc
import posts
from test_posts_quality import TODAY, js  # noqa: F401  (the OS-shaped root holding the fixture rows)


def _schema_keys() -> set[str]:
    schema = yaml.safe_load((__import__("agency_paths").SCHEMAS_DIR / "job.yaml").read_text(encoding="utf-8"))
    return {str(f) for f in schema.get("fields") or []} | set((schema.get("enums") or {}).keys())


def test_the_job_schema_lists_hiring_signal_and_check_records_reads_it_as_a_mapping():
    assert "hiring_signal" in _schema_keys()
    assert "hiring_signal" in cr.NOT_STRING["job"] and "hiring_signal" not in cr.string_fields("job")


def test_a_job_made_from_a_hiring_post_carries_only_schema_fields(js):
    rep = posts.ingest(js, today=TODAY, write=True, os_root=js.parents[3])
    assert rep["errors"] == [] and rep["jobs"] == 2
    keys = _schema_keys()
    for f in (js / "jobs").glob("*.md"):
        meta, _ = jc.read_record(f)
        sig = meta.get("hiring_signal")
        assert isinstance(sig, dict) and set(sig) == {"url", "date", "by"} and sig["url"].startswith("https://x.com/")
        assert set(meta) <= keys, (f.name, sorted(set(meta) - keys))
