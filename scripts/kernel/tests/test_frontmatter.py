import datetime as dt
from pathlib import Path

import pytest

import frontmatter as fm


SAMPLE = """---
title: Hello
count: 3
flag: true
due: 2026-10-01
tags: [a, b]
items:
  - one
  - two
nested:
  key: value
---
# Body

Some text.
"""


def test_loads_parses_meta_and_body():
    meta, body = fm.loads(SAMPLE)
    assert meta["title"] == "Hello"
    assert meta["count"] == 3
    assert meta["flag"] is True
    assert meta["due"] == dt.date(2026, 10, 1)
    assert meta["tags"] == ["a", "b"]
    assert meta["items"] == ["one", "two"]
    assert meta["nested"] == {"key": "value"}
    assert body == "# Body\n\nSome text.\n"


def test_load_round_trip(tmp_path: Path):
    p = tmp_path / "doc.md"
    p.write_text(SAMPLE, encoding="utf-8")
    meta, body = fm.load(p)
    out = fm.dump(meta, body)
    meta2, body2 = fm.loads(out)
    assert meta2 == meta
    assert body2 == body
    assert out.startswith("---\n")


def test_missing_frontmatter_returns_empty_meta(tmp_path: Path):
    p = tmp_path / "plain.md"
    p.write_text("just a body\nwith lines\n", encoding="utf-8")
    meta, body = fm.load(p)
    assert meta == {}
    assert body == "just a body\nwith lines\n"


def test_dump_without_meta_is_body_only():
    assert fm.dump({}, "body\n") == "body\n"


def test_unclosed_frontmatter_raises():
    with pytest.raises(fm.FrontmatterError):
        fm.loads("---\ntitle: x\nno closing fence\n")


def test_iter_docs_yields_sorted_and_reports_errors(tmp_path: Path):
    (tmp_path / "b.md").write_text("---\nn: 2\n---\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("---\nn: 1\n---\nA\n", encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\nn: [unclosed\n---\n", encoding="utf-8")
    docs = list(fm.iter_docs(tmp_path, "*.md"))
    names = [p.name for p, _, _ in docs]
    assert names == ["a.md", "b.md", "bad.md"]
    assert docs[0][1] == {"n": 1} and docs[0][2] == "A\n"
    assert "_error" in docs[2][1]


def test_fallback_parser_handles_common_shapes():
    meta = fm._parse_yaml_fallback(
        "title: Hello world\ncount: 3\nflag: false\nnone: null\nq: \"a: b\"\n"
        "due: 2026-10-01\ntags: [a, b]\nempty: []\nitems:\n  - one\n  - two\n"
        "nested:\n  key: value\n  n: 2\n"
    )
    assert meta == {
        "title": "Hello world",
        "count": 3,
        "flag": False,
        "none": None,
        "q": "a: b",
        "due": dt.date(2026, 10, 1),
        "tags": ["a", "b"],
        "empty": [],
        "items": ["one", "two"],
        "nested": {"key": "value", "n": 2},
    }


def test_round_trip_without_pyyaml(monkeypatch):
    monkeypatch.setattr(fm, "HAVE_YAML", False)
    meta, body = fm.loads(SAMPLE)
    assert meta["due"] == dt.date(2026, 10, 1) and meta["nested"] == {"key": "value"}
    out = fm.dump(meta, body)
    meta2, body2 = fm.loads(out)
    assert (meta2, body2) == (meta, body)


def test_dump_never_writes_yaml_anchors():
    import datetime as _dt
    import frontmatter as _fm
    d = _dt.date(2026, 9, 25)
    shared = {"verified_on": d, "source_url": "https://x.example"}
    text = _fm.dump({"created": d, "updated": d, "a": shared, "b": shared}, "# body\n")
    assert "&id" not in text and "*id" not in text
    meta, body = _fm.loads(text)
    assert meta["a"] == meta["b"] and str(meta["created"]) == "2026-09-25" and body == "# body\n"
