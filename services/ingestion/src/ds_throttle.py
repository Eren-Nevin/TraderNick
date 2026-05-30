"""Per-process throttle for DeFiStream HTTP calls.

Each ingestion group / backfill driver runs as its own subprocess with its
own ``AsyncDeFiStream`` instance. On startup every group's gap-fill task
fires concurrently; left unconstrained that produced bursts of >30 RPS that
DeFiStream rejected with 429, leaving permanent mid-range holes in tables
whose live overlap was too short to self-heal (the HL funding outage on
2026-05-30 was caused by this).

``install()`` monkey-patches ``AsyncDeFiStream._request`` so every HTTP call
to DeFiStream is paced by:

  - A token bucket (sustained RPS + burst capacity) — caps the per-subprocess
    request rate.
  - A concurrency semaphore — caps how many requests are in flight at once
    (back-pressure when calls fan out via ``asyncio.gather``).

Both are env-configurable. Defaults (4 RPS sustained, 6-call burst, 3
concurrent) are conservative enough to keep ALL groups + up to 4 concurrent
backfills under DeFiStream's observed limits.

Cross-process bursts (24 subprocesses all firing on cold start) are handled
separately by per-group startup jitter in ``supervisor.py``.
"""
import asyncio
import os
import time


_RPS = float(os.environ.get("DEFISTREAM_RATE_LIMIT_RPS", "4.0"))
_BURST = int(os.environ.get("DEFISTREAM_RATE_LIMIT_BURST", "6"))
_MAX_CONCURRENT = int(os.environ.get("DEFISTREAM_MAX_CONCURRENT", "3"))

_installed = False


class _TokenBucket:
    """Async token bucket: refills at ``rps`` tokens/sec, capped at ``burst``.

    Serializes acquirers through a lock — each waiter sleeps for its slot
    before returning, so calls leave the bucket paced by 1/rps."""

    def __init__(self, rps: float, burst: int):
        self._interval = 1.0 / rps
        self._burst_lead = (burst - 1) * self._interval
        # next_at = earliest monotonic time the next request is allowed.
        # Initialized so the first `burst` calls run immediately.
        self._next_at = time.monotonic() - self._burst_lead
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            slot = max(self._next_at, now - self._burst_lead)
            wait = max(0.0, slot - now)
            self._next_at = slot + self._interval
        if wait > 0:
            await asyncio.sleep(wait)


_bucket: _TokenBucket | None = None
_sem: asyncio.Semaphore | None = None


def install():
    """Monkey-patch ``AsyncDeFiStream._request`` to pace through the global
    bucket + semaphore. Idempotent — safe to call multiple times."""
    global _installed
    if _installed:
        return
    _installed = True

    from defistream.client import AsyncDeFiStream

    _orig_request = AsyncDeFiStream._request

    async def _throttled_request(self, *args, **kwargs):
        global _bucket, _sem
        if _bucket is None:
            _bucket = _TokenBucket(_RPS, _BURST)
        if _sem is None:
            _sem = asyncio.Semaphore(_MAX_CONCURRENT)
        await _bucket.acquire()
        async with _sem:
            return await _orig_request(self, *args, **kwargs)

    AsyncDeFiStream._request = _throttled_request
