"""Streams catalogue + cache.

Lives in its own module so `routes.transfers` and `routes.groups` can
both read the cached `[{chain, kind, token}, ...]` list without
circular-importing each other.

`get_streams_cached()` is the only function callers should use — it
respects the TTL and serialises concurrent refreshes via an asyncio lock.
"""
from __future__ import annotations

import asyncio
import time

from sanic import Blueprint, response

from clickhouse import client

bp = Blueprint("transfers_streams")

# Distinct (chain, token, kind) tuples take ~2-5s to compute over the full
# transfers table once it has 100M+ rows. The list changes only when admin
# reconfigures ingestion, so cache aggressively with a TTL.
_CACHE: dict = {"at": 0.0, "value": None}
_TTL_SECONDS = 60.0
_lock = asyncio.Lock()


async def _fetch() -> list[dict]:
    ch = await client()
    rows = await ch.query(
        """
        SELECT DISTINCT chain, token, kind
        FROM tradernick.transfers FINAL
        ORDER BY chain, token
        """
    )
    return [{"chain": r[0], "token": r[1], "kind": r[2]} for r in rows.result_rows]


async def get_streams_cached() -> list[dict]:
    now = time.monotonic()
    if _CACHE["value"] is not None and now - _CACHE["at"] < _TTL_SECONDS:
        return _CACHE["value"]
    async with _lock:
        now = time.monotonic()
        if _CACHE["value"] is None or now - _CACHE["at"] >= _TTL_SECONDS:
            _CACHE["value"] = await _fetch()
            _CACHE["at"] = now
    return _CACHE["value"]


@bp.get("/transfers/streams")
async def streams(_request):
    return response.json({"streams": await get_streams_cached()})
