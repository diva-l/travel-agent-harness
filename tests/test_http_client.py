from __future__ import annotations

import io
import json
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import urllib3

from travel_agent_harness.http_client import PooledHttpClient, TTLCache


def _request(url: str = "https://api.example.com/x", data: bytes | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, data=data, method="POST" if data else "GET")


class _FakeResponse:
    def __init__(self, status: int, data: bytes = b"{}"):
        self.status = status
        self.data = data
        self.reason = "fake"


class PooledHttpClientTests(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        client = PooledHttpClient(maxsize=2, retries=2, backoff=0)
        responses = iter([_FakeResponse(429), _FakeResponse(200, b'{"ok":1}')])
        with mock.patch.object(client._pool, "request", side_effect=lambda *a, **k: next(responses)):
            self.assertEqual(b'{"ok":1}', client.open(_request(), timeout=5))

    def test_retries_on_transport_error(self):
        client = PooledHttpClient(maxsize=2, retries=1, backoff=0)
        with mock.patch.object(
            client._pool,
            "request",
            side_effect=[urllib3.exceptions.NewConnectionError(None, "boom"), _FakeResponse(200, b"ok")],
        ):
            self.assertEqual(b"ok", client.open(_request(), timeout=5))

    def test_4xx_raises_immediately_without_retry(self):
        client = PooledHttpClient(maxsize=2, retries=2, backoff=0)
        calls = []

        def fake(*args, **kwargs):
            calls.append(1)
            return _FakeResponse(400, b"bad request")

        with mock.patch.object(client._pool, "request", side_effect=fake):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                client.open(_request(), timeout=5)
        self.assertEqual(400, ctx.exception.code)
        self.assertEqual(1, len(calls))

    def test_exhausted_retries_raise_url_error(self):
        client = PooledHttpClient(maxsize=2, retries=1, backoff=0)
        with mock.patch.object(client._pool, "request", return_value=_FakeResponse(503)):
            with self.assertRaises(urllib.error.URLError):
                client.open(_request(), timeout=5)


class TTLCacheTests(unittest.TestCase):
    def test_disabled_cache_stores_nothing(self):
        cache = TTLCache(0)
        self.assertFalse(cache.enabled)
        cache.put("k", b"v")
        self.assertIsNone(cache.get("k"))

    def test_hit_within_ttl_and_miss_after_expiry(self):
        cache = TTLCache(0.05)
        cache.put("k", b"v")
        self.assertEqual(b"v", cache.get("k"))
        time.sleep(0.06)
        self.assertIsNone(cache.get("k"))


if __name__ == "__main__":
    unittest.main()
