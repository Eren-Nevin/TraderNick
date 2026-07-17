"""Shared fixtures + helpers for the tradernick-data-provider test suite.

Two tiers:
  * Unit tests (default) — respx mocks the single HTTP chokepoint
    (``_http.fetch_table`` → one ``POST``), so no server/ClickHouse is needed.
  * Live integration tests (``-m integration``) — skipped unless the
    ``DATA_PROVIDER_URL`` env var points at a running data_provider.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio

from tradernick_data_provider import DataProviderClient

# Any host works — respx intercepts by URL, nothing is really dialed.
BASE_URL = "http://data-provider.test"


# --------------------------------------------------------------------------
# Parquet response helper — the server answers /read routes with parquet
# bytes (content-type application/octet-stream). Build that from a table.
# --------------------------------------------------------------------------
def parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def ohlcv_table(rows: int = 3) -> pa.Table:
    """A tiny binance-ohlcv-shaped table (ms+UTC time), unsorted on purpose so
    tests can assert the client re-sorts by time."""
    base = int(datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp() * 1000)
    # Deliberately descending time to exercise the client-side sort.
    times = [base + (rows - 1 - i) * 3_600_000 for i in range(rows)]
    ts = pa.array(times, type=pa.timestamp("ms", tz="UTC"))
    return pa.table({
        "time": ts,
        "token": ["BTC"] * rows,
        "open": [1.0] * rows, "close": [2.0] * rows,
        "high": [3.0] * rows, "low": [0.5] * rows, "volume": [10.0] * rows,
        "buyer_taker_volume": [4.0] * rows, "seller_taker_volume": [6.0] * rows,
        "trade_count": pa.array([7] * rows, type=pa.int64()),
    })


def window_table(rows: int = 2) -> pa.Table:
    """A frame that comes back with a ``window`` column (aggregate shape) and
    a microsecond, tz-naive time to exercise both the window→time rename and
    the ms+UTC cast."""
    base = int(datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp() * 1_000_000)
    win = pa.array([base + i * 3_600_000_000 for i in range(rows)],
                   type=pa.timestamp("us"))  # tz-naive, microseconds
    return pa.table({"window": win, "token": ["BTC"] * rows, "value": [1.0] * rows})


@pytest_asyncio.fixture
async def client():
    c = DataProviderClient(BASE_URL)
    try:
        yield c
    finally:
        await c.close()


# --------------------------------------------------------------------------
# Live integration gate
# --------------------------------------------------------------------------
LIVE_URL = os.environ.get("DATA_PROVIDER_URL")


@pytest_asyncio.fixture
async def live_client():
    if not LIVE_URL:
        pytest.skip("DATA_PROVIDER_URL not set — skipping live integration test")
    c = DataProviderClient(LIVE_URL)
    try:
        yield c
    finally:
        await c.close()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: hits a live data_provider (needs DATA_PROVIDER_URL)",
    )
