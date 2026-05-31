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

# Distinct (chain, token, kind) tuples scan the whole transfers table —
# ~3s on 1B rows with the index-friendly DISTINCT below, much slower
# with FINAL (which forces a merge-scan that doesn't change the unique
# tuple set anyway since dedup runs on a different ORDER BY tuple).
# Cache for a minute regardless — admin reconfigures ingestion rarely.
_CACHE: dict = {"at": 0.0, "value": None}
_TTL_SECONDS = 60.0
_lock = asyncio.Lock()


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
