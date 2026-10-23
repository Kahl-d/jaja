"""fetch_ats.Http on HTTP 429 (SETUP order 2026-10-01, people_traces crash): with max_retry_after set, a 429 waits what
Retry-After asks (capped), then retries; when retries run out the FetchError carries status 429. Without it (the
default, every board fetcher) the old exponential backoff is unchanged."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403

import fetch_ats as fa  # noqa: E402


class _Resp:
    def __init__(self, status, headers=None, body=b"{}"):
        self.status_code, self.headers, self.content = status, headers or {}, body

    def json(self):
        return {}


class _Session:
    def __init__(self, responses):
        self.responses, self.headers, self.calls = list(responses), {}, 0

    def request(self, method, url, timeout=None, **kw):
        self.calls += 1
        return self.responses.pop(0)


def _http(responses, monkeypatch, **kw):
    sleeps = []
    monkeypatch.setattr(fa.time, "sleep", lambda s: sleeps.append(s))
    return fa.Http(session=_Session(responses), min_interval=0, **kw), sleeps


def test_retry_after_is_honoured_and_capped_when_asked(monkeypatch):
    http, sleeps = _http([_Resp(429, {"Retry-After": "7"}), _Resp(429, {"Retry-After": "900"}), _Resp(200)],
                         monkeypatch, max_retry_after=60)
    assert http.get_json("https://api.example/x") == {}
    assert sleeps == [7.0, 60.0]                                  # the header, then the cap


def test_a_429_that_never_clears_raises_with_its_status(monkeypatch):
    http, sleeps = _http([_Resp(429, {"Retry-After": "3"})] * 3, monkeypatch, max_retry_after=60)
    try:
        http.get_json("https://api.example/x")
    except fa.FetchError as exc:
        assert exc.status == 429 and exc.retry_after == 3.0
    else:
        raise AssertionError("expected FetchError")
    assert sleeps == [3.0, 3.0]


def test_the_default_keeps_the_exponential_backoff(monkeypatch):
    http, sleeps = _http([_Resp(429, {"Retry-After": "40"}), _Resp(200)], monkeypatch)
    http.get_json("https://api.example/x")
    assert sleeps == [1.5]


def test_a_plain_4xx_carries_its_status(monkeypatch):
    http, _ = _http([_Resp(404)], monkeypatch)
    try:
        http.get_json("https://api.example/x")
    except fa.FetchError as exc:
        assert exc.status == 404


def test_get_page_returns_the_final_url_after_redirects(monkeypatch):
    r = _Resp(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>ok</html>")
    r.url = "https://acme.wd1.myworkdayjobs.com/External"
    http, _ = _http([r], monkeypatch)
    assert http.get_page("https://acme.example/careers") == (200, "<html>ok</html>", "https://acme.wd1.myworkdayjobs.com/External")


# S-10 item 1 (SETUP 2026-10-02): requests' timeout=30 is per read, so a server trickling bytes never times out. Every
# request now has a total wall-clock limit (max_seconds); past it the request fails at once, with no retry.
class _Trickle:
    """Like requests.Response: .content reads _content, which a streamed read fills."""
    def __init__(self, chunks, clock, step):
        self.status_code, self.headers, self._content = 200, {"Content-Type": "text/xml"}, None
        self._chunks, self._clock, self._step = chunks, clock, step

    @property
    def content(self):
        return self._content

    def iter_content(self, chunk_size=None):
        for c in self._chunks:
            self._clock["t"] += self._step
            yield c

    def close(self):
        self.closed = True


def test_a_trickling_response_is_cut_at_the_wall_clock_limit_without_retry(monkeypatch):
    clock = {"t": 0.0}
    resp = _Trickle([b"x"] * 100, clock, step=10.0)               # 10 s per chunk: 1000 s if left alone
    session = _Session([resp, _Resp(200)])
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    http = fa.Http(session=session, min_interval=0, max_seconds=120, clock=lambda: clock["t"])
    try:
        http.get_text("https://jobs.example/sitemap.xml")
    except fa.FetchError as exc:
        assert "120" in str(exc) and "wall" in str(exc)
    else:
        raise AssertionError("expected FetchError")
    assert session.calls == 1 and clock["t"] <= 130 and getattr(resp, "closed", False)


def test_a_streamed_body_under_the_limit_reads_whole(monkeypatch):
    clock = {"t": 0.0}
    http = fa.Http(session=_Session([_Trickle([b"<a>", b"ok", b"</a>"], clock, step=1.0)]), min_interval=0,
                   max_seconds=120, clock=lambda: clock["t"])
    assert http.get_text("https://jobs.example/x") == (200, "<a>ok</a>")


# CS83 (SETUP's clean-context review of CS79 to CS82, 2026-10-02). P1: the streamed body was read outside the retry, so a
# connection dropped mid-body escaped as a raw requests error (fetch_sitemap lost every page it had read); the wall-clock
# limit is its own error kind; chunk_size 64 KiB blocked on a trickle (chunk_size None yields what has arrived) and a
# byte cap bounds the body; every error response is closed. P0b: past the run deadline no request is sent.
import pytest  # noqa: E402
import requests  # noqa: E402


class _Breaks(_Trickle):
    """A body that drops after its first chunk."""
    def iter_content(self, chunk_size=None):
        yield b"<a>"
        raise requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")


class _Closing(_Resp):
    def close(self):
        self.closed = True


def _clockless(responses, monkeypatch, **kw):
    clock = {"t": 0.0}
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    return fa.Http(session=_Session(responses), min_interval=0, clock=lambda: clock["t"], **kw), clock


def test_a_body_that_breaks_mid_read_is_retried(monkeypatch):
    clock = {"t": 0.0}
    http, _ = _clockless([_Breaks([], clock, 0.0), _Trickle([b"<a>ok</a>"], clock, 0.0)], monkeypatch)
    assert http.get_text("https://jobs.example/x") == (200, "<a>ok</a>") and http.session.calls == 2


def test_a_body_that_keeps_breaking_raises_a_fetch_error(monkeypatch):
    clock = {"t": 0.0}
    http, _ = _clockless([_Breaks([], clock, 0.0) for _ in range(3)], monkeypatch)
    with pytest.raises(fa.FetchError):
        http.get_text("https://jobs.example/x")
    assert http.session.calls == 3


def test_the_wall_clock_limit_is_its_own_error_kind(monkeypatch):
    clock = {"t": 0.0}
    http = fa.Http(session=_Session([_Trickle([b"x"] * 50, clock, 10.0)]), min_interval=0, max_seconds=60,
                   clock=lambda: clock["t"])
    with pytest.raises(fa.WallClockLimit) as err:
        http.get_text("https://jobs.example/x")
    assert isinstance(err.value, fa.FetchError)


class _Raw:
    """urllib3's response: read1 returns what one socket read gave (here 1 byte, 10 s apart: a trickle)."""
    def __init__(self, clock, step, data=b"x" * 1000):
        self.clock, self.step, self.data, self.reads = clock, step, data, 0

    def read1(self, amt=None, decode_content=None):
        self.reads += 1
        self.clock["t"] += self.step
        out, self.data = self.data[:1], self.data[1:]
        return out


def test_a_trickle_reaches_the_clock_check_byte_by_byte(monkeypatch):
    # urllib3 2.5: iter_content(chunk_size=None) on a body with a Content-Length reads to the end in one call, and a
    # 64 KiB chunk waits for 64 KiB; read1 returns what has arrived, so the wall-clock check runs on every byte
    clock = {"t": 0.0}
    resp = _Trickle([], clock, 0.0)
    resp.raw = _Raw(clock, 10.0)
    http = fa.Http(session=_Session([resp]), min_interval=0, max_seconds=60, clock=lambda: clock["t"])
    with pytest.raises(fa.WallClockLimit):
        http.get_text("https://jobs.example/x")
    assert resp.raw.reads <= 7 and getattr(resp, "closed", False)


def test_the_body_is_capped_in_bytes(monkeypatch):
    clock = {"t": 0.0}
    big = _Trickle([b"x" * 6] * 5, clock, 0.0)
    http, _ = _clockless([big], monkeypatch, max_bytes=20)
    with pytest.raises(fa.FetchError, match="bytes"):
        http.get_text("https://jobs.example/x")
    assert getattr(big, "closed", False)


def test_every_error_response_is_closed(monkeypatch):
    busy, missing = _Closing(503), _Closing(404)
    http, _ = _clockless([busy, _Resp(200)], monkeypatch)
    http.get_json("https://api.example/x")
    assert getattr(busy, "closed", False)
    http, _ = _clockless([missing], monkeypatch)
    with pytest.raises(fa.FetchError):
        http.get_json("https://api.example/x")
    assert getattr(missing, "closed", False)


def test_past_the_deadline_no_request_is_sent(monkeypatch):
    http, _ = _clockless([_Resp(200)], monkeypatch)
    fa.set_deadline(lambda: 10.0, 5.0)
    try:
        with pytest.raises(fa.DeadlineReached):
            http.get_json("https://api.example/x")
    finally:
        fa.set_deadline(None, None)
    assert http.session.calls == 0 and issubclass(fa.DeadlineReached, fa.FetchError)


def test_a_retry_wait_that_would_end_past_the_deadline_is_not_slept(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fa.time, "sleep", sleeps.append)
    http = fa.Http(session=_Session([_Resp(503), _Resp(200)]), min_interval=0)
    fa.set_deadline(lambda: 0.0, 1.0)                           # 1 s left, the backoff is 1.5 s
    try:
        with pytest.raises(fa.DeadlineReached):
            http.get_json("https://api.example/x")
    finally:
        fa.set_deadline(None, None)
    assert sleeps == [] and http.session.calls == 1
