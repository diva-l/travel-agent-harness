"""Shared HTTP plumbing for external tool providers.

One urllib3 PoolManager per provider gives three of the concurrency levers
from docs/concurrency-techniques.md in a single place:

- connection reuse (keep-alive) instead of per-call TCP/TLS handshakes;
- a bounded, blocking connection pool = client-side concurrency limit
  (backpressure) toward rate-limited upstreams;
- retry with exponential backoff on transient transport errors and
  429/5xx, so a saturated upstream degrades into latency instead of
  hard tool failures.

The opener signature stays ``(urllib.request.Request, timeout) -> bytes``
so tests can keep injecting fakes.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from typing import Any

import urllib3

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TTLCache:
    """Tiny thread-safe TTL cache for idempotent upstream responses."""

    def __init__(self, ttl_seconds: float, *, maxsize: int = 512) -> None:
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._entries: dict[Any, tuple[float, bytes]] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, key: Any) -> bytes | None:
        if not self.enabled:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                self._entries.pop(key, None)
                return None
            return value

    def put(self, key: Any, value: bytes) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._entries) >= self._maxsize:
                # Drop the soonest-expiring entry; precise eviction is not
                # worth the bookkeeping at this size.
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (time.monotonic() + self._ttl, value)


class PooledHttpClient:
    """Thread-safe pooled HTTP client exposing the provider opener contract."""

    def __init__(self, *, maxsize: int = 8, retries: int = 2, backoff: float = 0.5) -> None:
        self._pool = urllib3.PoolManager(
            # block=True turns pool exhaustion into queueing (backpressure)
            # instead of silently opening unbounded connections.
            maxsize=max(1, maxsize),
            block=True,
            retries=False,  # retries handled below, with backoff
        )
        self._retries = max(0, retries)
        self._backoff = backoff

    def open(self, request: urllib.request.Request, timeout: float) -> bytes:
        method = request.get_method()
        headers = {str(k): str(v) for k, v in request.header_items()}
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            if attempt:
                time.sleep(self._backoff * (2 ** (attempt - 1)))
            try:
                response = self._pool.request(
                    method,
                    request.full_url,
                    body=request.data,
                    headers=headers,
                    timeout=urllib3.Timeout(total=timeout),
                    preload_content=True,
                )
            except (urllib3.exceptions.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
                continue
            if response.status in _RETRYABLE_STATUS:
                last_error = urllib.error.HTTPError(
                    request.full_url, response.status, "retryable upstream status", None, None)
                continue
            if response.status >= 400:
                # Non-retryable 4xx: surface the body the way urlopen's
                # HTTPError would, so providers raise their usual errors.
                raise urllib.error.HTTPError(
                    request.full_url, response.status, response.reason, None,
                    __import__("io").BytesIO(response.data))
            return response.data
        assert last_error is not None
        raise urllib.error.URLError(f"exhausted retries: {last_error}")


def build_opener(*, maxsize: int = 8, retries: int = 2, backoff: float = 0.5):
    """Return an opener callable matching the provider ``Opener`` contract."""
    client = PooledHttpClient(maxsize=maxsize, retries=retries, backoff=backoff)
    return client.open
