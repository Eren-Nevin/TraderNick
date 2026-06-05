"""Two-tier ClickHouse concurrency limiter.

Wraps route handlers with an asyncio.Semaphore so expensive ClickHouse
scans don't trample the kernel's page cache and balloon resident set
size. Heavy routes share a small pool (default 3); cheap status routes
share a large one (default 20) so a slow HL scan never starves
``/tokens`` or ``/transfers/categories``.

Usage::

    from throttle import throttled

    @bp.get("/hyperliquid/oi_split")
    @throttled("heavy")
    async def oi_split(request):
        ...

The decorator does the acquire BEFORE the handler runs and releases on
the way out — even if the handler raises. Permit waiters are FIFO
(asyncio default), so a dashboard reload that fires 8 chart requests at
once lets the first 3 execute immediately and serves the remaining 5 as
slots free up.

A ``/health/queue`` endpoint (registered by ``register_health_endpoint``)
exposes live depth + waiter counts so the dashboard's operator panel can
spot when the cap is throttling traffic.
"""

import asyncio
import os
from functools import wraps
from typing import Awaitable, Callable, Literal


Tier = Literal["heavy", "light"]


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


HEAVY_CONCURRENCY = _env_int("CH_HEAVY_CONCURRENCY", 2)
LIGHT_CONCURRENCY = _env_int("CH_LIGHT_CONCURRENCY", 20)


class _Tier:
    """Semaphore + live counters for one tier."""

    __slots__ = ("name", "limit", "sem", "_in_flight", "_waiting")

    def __init__(self, name: str, limit: int) -> None:
        self.name = name
        self.limit = limit
        self.sem = asyncio.Semaphore(limit)
        self._in_flight = 0
        self._waiting = 0

    async def acquire(self) -> None:
        # `_waiting` accounts for slots a caller is hoping for; once the
        # semaphore lets us through we move the count to `_in_flight`.
        # We can't rely on Semaphore.locked() to give that breakdown.
        self._waiting += 1
        try:
            await self.sem.acquire()
        finally:
            self._waiting -= 1
        self._in_flight += 1

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self.sem.release()

    def snapshot(self) -> dict:
        return {
            "limit": self.limit,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
        }


_TIERS: dict[Tier, _Tier] = {
    "heavy": _Tier("heavy", HEAVY_CONCURRENCY),
    "light": _Tier("light", LIGHT_CONCURRENCY),
}


def throttled(tier: Tier) -> Callable:
    """Decorator: gate the handler behind the tier's semaphore."""
    if tier not in _TIERS:
        raise ValueError(f"unknown tier: {tier!r}")

    def decorator(handler: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
        @wraps(handler)
        async def wrapper(*args, **kwargs):
            t = _TIERS[tier]
            await t.acquire()
            try:
                return await handler(*args, **kwargs)
            finally:
                t.release()
        return wrapper

    return decorator


def queue_snapshot() -> dict:
    """Live counters for /health/queue. Cheap, no locks."""
    return {tier_name: t.snapshot() for tier_name, t in _TIERS.items()}


def register_health_endpoint(app) -> None:
    """Wire ``/health/queue`` onto the Sanic app."""
    from sanic import response

    @app.get("/health/queue")
    async def _queue_health(_request):
        return response.json(queue_snapshot())
