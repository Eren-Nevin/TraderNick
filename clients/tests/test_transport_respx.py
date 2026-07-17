"""Transport tests — respx mocks the single POST chokepoint.

These pin: (a) the request URL/method/body the client emits for each route,
including the new binance-spot paths and the erc20 /read/min fix and the
multi-network fan-out; and (b) the client-side response transforms
(window→time rename, sort-by-time, ms+UTC cast, aggregate time-indexing).
"""
from __future__ import annotations

import json

import httpx
import pytest

from tradernick_data_provider.exceptions import DataProviderHTTPError
from conftest import BASE_URL, ohlcv_table, window_table, parquet_bytes

PARQUET_HEADERS = {"content-type": "application/octet-stream"}
JSON_HEADERS = {"content-type": "application/json"}


def _parquet_response(table):
    return httpx.Response(200, content=parquet_bytes(table), headers=PARQUET_HEADERS)


# ---------------------------------------------------------------------------
# Response transforms
# ---------------------------------------------------------------------------
async def test_as_polars_sorts_by_time(client, respx_mock):
    respx_mock.post(BASE_URL + "/binance/ohlcv/read").mock(
        return_value=_parquet_response(ohlcv_table(3)))
    df = await client.binance.ohlcv("BTC", "1h").time_range("2026-07-10", "2026-07-11").as_polars()
    assert df.height == 3
    times = df["time"].to_list()
    assert times == sorted(times)                       # client re-sorted ascending
    assert str(df.schema["time"]) == "Datetime(time_unit='ms', time_zone='UTC')"


async def test_as_polars_window_renamed_to_time(client, respx_mock):
    respx_mock.post(BASE_URL + "/binance/ohlcv/read").mock(
        return_value=_parquet_response(window_table(2)))
    df = await client.binance.ohlcv("BTC", "1h").as_polars()
    assert "time" in df.columns and "window" not in df.columns
    # us+naive in → ms+UTC out
    assert str(df.schema["time"]) == "Datetime(time_unit='ms', time_zone='UTC')"


async def test_as_pandas_aggregate_is_time_indexed(client, respx_mock):
    respx_mock.post(BASE_URL + "/binance/ohlcv/read").mock(
        return_value=_parquet_response(window_table(2)))
    df = await client.binance.ohlcv("BTC", "1h").as_pandas()
    assert df.index.name == "time"           # came_from_window → set_index('time')


# ---------------------------------------------------------------------------
# New endpoints — binance spot paths get the request
# ---------------------------------------------------------------------------
async def test_binance_spot_ohlcv_hits_spot_path(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/binance/spot/ohlcv/read").mock(
        return_value=_parquet_response(ohlcv_table(1)))
    df = await client.binance.spot.ohlcv("BTC", "1h").time_range("2026-07-10", "2026-07-11").as_polars()
    assert df.height == 1
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body == {"token": "BTC", "window": "1h",
                    "since": "2026-07-10T00:00:00Z", "until": "2026-07-11T00:00:00Z"}


async def test_binance_spot_raw_trades_hits_spot_path(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/binance/spot/raw_trades/read").mock(
        return_value=_parquet_response(ohlcv_table(1)))
    await client.binance.spot.raw_trades("BTC").with_id().as_polars()
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["token"] == "BTC" and body["with_id"] is True


# ---------------------------------------------------------------------------
# erc20 /read/min routing (the bug fix)
# ---------------------------------------------------------------------------
async def test_erc20_min_amount_posts_to_read_min(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/evm/erc20_transfers/read/min").mock(
        return_value=_parquet_response(ohlcv_table(1)))
    await (client.evm.erc20.transfers(["USDC"]).network("ethereum")
           .min_amount(1_000_000).time_range("2026-07-10", "2026-07-11").as_polars())
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["min_amount"] == 1_000_000


async def test_erc20_default_posts_to_read(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/evm/erc20_transfers/read").mock(
        return_value=_parquet_response(ohlcv_table(1)))
    await (client.evm.erc20.transfers(["USDC"]).network("ethereum")
           .time_range("2026-07-10", "2026-07-11").as_polars())
    assert route.called


# ---------------------------------------------------------------------------
# Multi-network fan-out — one POST per network, concatenated
# ---------------------------------------------------------------------------
async def test_multi_network_fanout_concats(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/evm/aave/read").mock(
        return_value=_parquet_response(ohlcv_table(2)))
    df = await (client.evm.aave.deposits().network(["ethereum", "base"])
                .time_range("2026-07-10", "2026-07-11").as_polars())
    assert route.call_count == 2                 # one per network
    assert df.height == 4                         # 2 rows × 2 networks concatenated
    # per-network requests carry `network`, not `networks`
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert all(b.get("network") in ("ethereum", "base") for b in bodies)
    assert all("networks" not in b for b in bodies)


# ---------------------------------------------------------------------------
# as_parquet — single-network save_key path (binance, no _PROTOCOL)
# ---------------------------------------------------------------------------
async def test_as_parquet_single_network_uses_save_key(client, respx_mock):
    route = respx_mock.post(BASE_URL + "/binance/ohlcv/read").mock(
        return_value=httpx.Response(200, json={"saved": True, "key": "snap1"},
                                    headers=JSON_HEADERS))
    result = await client.binance.ohlcv("BTC", "1h").as_parquet("snap1")
    assert result is None
    body = json.loads(route.calls.last.request.content)
    assert body["save_key"] == "snap1"


# ---------------------------------------------------------------------------
# Error propagation — JSON error body → DataProviderHTTPError
# ---------------------------------------------------------------------------
async def test_json_error_raises(client, respx_mock):
    respx_mock.post(BASE_URL + "/binance/ohlcv/read").mock(
        return_value=httpx.Response(400, json={"error": "bad window"},
                                    headers=JSON_HEADERS))
    with pytest.raises(DataProviderHTTPError):
        await client.binance.ohlcv("BTC", "5x").as_polars()


# ---------------------------------------------------------------------------
# Snapshots + health
# ---------------------------------------------------------------------------
async def test_list_snapshots(client, respx_mock):
    respx_mock.get(BASE_URL + "/snapshots/list").mock(
        return_value=httpx.Response(200, json={"keys": ["a", "b"]}, headers=JSON_HEADERS))
    assert await client.list_snapshots() == ["a", "b"]


async def test_load_parquet_casts_time(client, respx_mock):
    respx_mock.post(BASE_URL + "/snapshots/load").mock(
        return_value=_parquet_response(ohlcv_table(2)))
    df = await client.load_parquet("snap1")
    assert df.height == 2
    assert str(df.schema["time"]) == "Datetime(time_unit='ms', time_zone='UTC')"


async def test_health(client, respx_mock):
    respx_mock.get(BASE_URL + "/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"}, headers=JSON_HEADERS))
    assert await client.health() is True
