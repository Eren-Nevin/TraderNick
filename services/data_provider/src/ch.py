"""Async ClickHouse access — read-only.

Returns rows as polars DataFrames via the Arrow path. The data_provider
never writes to ingestion tables; this module exposes only SELECT-style
calls.
"""

from __future__ import annotations

import os
from typing import Any

import polars as pl
import pyarrow as pa
from clickhouse_connect import get_async_client


_client = None


async def get_client():
    global _client
    if _client is None:
        _client = await get_async_client(
            host=os.environ.get('CLICKHOUSE_HOST', 'clickhouse'),
            port=int(os.environ.get('CLICKHOUSE_PORT', '8123')),
            username=os.environ.get('CLICKHOUSE_USER', 'tradernick'),
            password=os.environ.get('CLICKHOUSE_PASSWORD', ''),
            database=os.environ.get('CLICKHOUSE_DB', 'tradernick'),
        )
    return _client


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
