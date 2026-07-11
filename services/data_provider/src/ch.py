"""Async ClickHouse access — read-only.

Returns rows as polars DataFrames via the Arrow path. The data_provider
never writes to ingestion tables; this module exposes only SELECT-style
calls (plus the wallet-label DELETE).

get_client() returns a transparent retrying proxy: query_arrow / query /
command reconnect + retry on TRANSIENT connection errors, so a brief
ClickHouse outage (<~1 min — e.g. a container restart to apply a cpuset)
doesn't fail requests or leave a permanently-broken singleton behind.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import polars as pl
import pyarrow as pa
from clickhouse_connect import get_async_client

_log = logging.getLogger("ch")
_real_client = None
_client_lock = asyncio.Lock()


async def _get_real_client():
    global _real_client
    if _real_client is None:
        async with _client_lock:
            if _real_client is None:
                _real_client = await get_async_client(
                    host=os.environ.get('CLICKHOUSE_HOST', 'clickhouse'),
                    port=int(os.environ.get('CLICKHOUSE_PORT', '8123')),
                    username=os.environ.get('CLICKHOUSE_USER', 'tradernick'),
                    password=os.environ.get('CLICKHOUSE_PASSWORD', ''),
                    database=os.environ.get('CLICKHOUSE_DB', 'tradernick'),
                )
    return _real_client


async def _reset_real_client():
    global _real_client
    c, _real_client = _real_client, None
    if c is not None:
        try:
            await c.close()
        except Exception:  # noqa: BLE001
            pass


_TRANSIENT_HINTS = (
    "connection", "connreset", "connection reset", "refused", "reset by peer",
    "timed out", "timeout", "operational", "network", "broken pipe", "closed",
    "unreachable", "cannot connect", "connecterror", "readtimeout",
    "connecttimeout", "no route to host", "temporarily unavailable",
    "connection aborted", "server disconnected", "remotedisconnected",
    "502", "503", "504", "eof occurred", "not connected",
    # CH cancels in-flight queries as it shuts down for a restart:
    "query was cancelled", "query_was_cancelled", "killed in pending", "code: 394",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)):
        return True
    return any(h in str(exc).lower() for h in _TRANSIENT_HINTS)


_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 6.0, 9.0, 12.0)


async def _call_with_retry(method: str, *args, **kwargs):
    last: Exception | None = None
    for i, delay in enumerate(_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            ch = await _get_real_client()
            return await getattr(ch, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc):
                raise
            _log.warning("CH %s transient failure (attempt %d/%d): %s — reconnecting",
                         method, i + 1, len(_RETRY_DELAYS), str(exc)[:200])
            await _reset_real_client()
    raise last  # type: ignore[misc]


class _RetryingAsyncClient:
    async def query_arrow(self, *a, **k):
        return await _call_with_retry("query_arrow", *a, **k)

    async def query(self, *a, **k):
        return await _call_with_retry("query", *a, **k)

    async def command(self, *a, **k):
        return await _call_with_retry("command", *a, **k)

    async def close(self):
        await _reset_real_client()

    def __getattr__(self, name):
        real = _real_client
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


_proxy = _RetryingAsyncClient()


async def get_client():
    await _get_real_client()
    return _proxy


async def query_polars(sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """Run a SELECT and return a polars DataFrame.

    Uses CH's arrow stream so the round-trip stays columnar end-to-end.
    Empty results return an empty DataFrame; callers that need a schema
    on the empty path should pass an explicit empty template downstream.
    """
    client = await get_client()
    table: pa.Table = await client.query_arrow(sql, parameters=params or {})
    if table is None or table.num_rows == 0:
        if table is not None and table.schema is not None:
            return pl.from_arrow(table.schema.empty_table())
        return pl.DataFrame()
    return pl.from_arrow(table)
