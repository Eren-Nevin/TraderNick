"""Streams catalogue + cache.

Lives in its own module so `routes.transfers` and `routes.groups` can
both read the cached `[{chain, kind, token}, ...]` list without
circular-importing each other.

`get_streams_cached()` is the only function callers should use — it
serves the cached value immediately (stale-while-revalidate) and kicks
off a background refresh past the TTL. `warm_streams_cache()` is a
startup-time helper that primes the cache before the server accepts
traffic, so the first dashboard page-load never blocks on the cold
DISTINCT scan.
"""
from __future__ import annotations

import asyncio
import logging
import time

from sanic import Blueprint, response

from clickhouse import client

bp = Blueprint("transfers_streams")
log = logging.getLogger(__name__)

# Distinct (chain, token, kind) tuples scan the whole transfers table —
# ~30s on the ~1B-row transfers table even without FINAL. The catalogue
# changes only when ingestion picks up a new (chain, token, kind), so
# stale-while-revalidate is fine: we always return the cached set
# instantly and refresh in the background past the TTL.
_CACHE: dict = {"at": 0.0, "value": None}
_TTL_SECONDS = 300.0
_lock = asyncio.Lock()
_refresh_task: asyncio.Task | None = None


async def _fetch() -> list[dict]:
    ch = await client()
    # No FINAL: ReplacingMergeTree dedup on transfers is keyed on the
    # full (chain, token, time, sender, receiver, amount, tx_id,
    # log_index) tuple. Duplicate rows would still share the same
    # (chain, token, kind) DISTINCT key, so dropping FINAL gives the
    # identical result set in a fraction of the time.
    rows = await ch.query(
        """
        SELECT DISTINCT chain, token, kind
        FROM tradernick.transfers
        ORDER BY chain, token
        """
    )
    return [{"chain": r[0], "token": r[1], "kind": r[2]} for r in rows.result_rows]


async def _refresh_now() -> None:
    async with _lock:
        try:
            value = await _fetch()
        except Exception:
            log.exception("transfers/streams refresh failed; keeping previous cache")
            return
        _CACHE["value"] = value
        _CACHE["at"] = time.monotonic()


def _maybe_kick_refresh() -> None:
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh_now())


async def get_streams_cached() -> list[dict]:
    now = time.monotonic()
    # First-ever call (e.g. someone hit a different endpoint that
    # bypassed the startup warm). Block once; subsequent stale reads
    # are non-blocking.
    if _CACHE["value"] is None:
        await _refresh_now()
        return _CACHE["value"] or []
    # Stale-while-revalidate: serve the cached value, kick a refresh
    # if past TTL.
    if now - _CACHE["at"] >= _TTL_SECONDS:
        _maybe_kick_refresh()
    return _CACHE["value"]


async def warm_streams_cache() -> None:
    """Prime the cache before the server starts handling requests."""
    if _CACHE["value"] is None:
        await _refresh_now()


@bp.get("/transfers/streams")
async def streams(_request):
    return response.json({"streams": await get_streams_cached()})
