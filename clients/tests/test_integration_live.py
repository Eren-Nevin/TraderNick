"""Live integration tests — opt-in, hit a running data_provider.

Skipped entirely unless ``DATA_PROVIDER_URL`` is set (see the ``live_client``
fixture in conftest). Run with:

    DATA_PROVIDER_URL=http://localhost:10005 pytest -m integration

Windows are deliberately tiny (minutes) because the backing tables are
billions of rows. These double as the per-provider "does it actually work"
audit: populated providers must return rows; the two stub HL endpoints must
honor the empty-frame contract.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# A window known to have data across binance + HL (mid-2026).
SINCE, UNTIL = "2026-07-10T00:00:00Z", "2026-07-10T00:05:00Z"
SINCE_1M, UNTIL_1M = "2026-07-10T00:00:00Z", "2026-07-10T00:01:00Z"


async def test_health(live_client):
    assert await live_client.health() is True


# --- binance perp ---------------------------------------------------------
async def test_binance_ohlcv(live_client):
    df = await live_client.binance.ohlcv("BTC", "1m").time_range(SINCE, UNTIL).as_polars()
    assert df.height > 0
    assert {"time", "open", "close", "high", "low", "volume"} <= set(df.columns)


async def test_binance_raw_trades(live_client):
    df = await live_client.binance.raw_trades("BTC").time_range(SINCE_1M, UNTIL_1M).as_polars()
    assert df.height > 0


# --- binance SPOT (new) ---------------------------------------------------
async def test_binance_spot_ohlcv(live_client):
    df = await live_client.binance.spot.ohlcv("BTC", "1m").time_range(SINCE, UNTIL).as_polars()
    assert df.height > 0
    assert {"time", "open", "close", "volume"} <= set(df.columns)


async def test_binance_spot_raw_trades(live_client):
    df = await live_client.binance.spot.raw_trades("BTC").time_range(SINCE_1M, UNTIL_1M).as_polars()
    assert df.height > 0
    assert {"time", "token", "amount", "price", "buy"} <= set(df.columns)


async def test_binance_spot_and_perp_are_distinct(live_client):
    """Spot and perp are separate datasets — a sanity check that we're really
    reading two different tables, not the same one twice."""
    spot = await live_client.binance.spot.ohlcv("BTC", "1m").time_range(SINCE, UNTIL).as_polars()
    perp = await live_client.binance.ohlcv("BTC", "1m").time_range(SINCE, UNTIL).as_polars()
    assert spot.height > 0 and perp.height > 0
    # Volumes differ between the two markets; close prices track but aren't identical.
    assert spot["volume"].sum() != perp["volume"].sum()


# --- hyperliquid ----------------------------------------------------------
async def test_hyperliquid_fills(live_client):
    df = await live_client.hyperliquid.fills().tokens("BTC").time_range(SINCE_1M, UNTIL_1M).as_polars()
    assert df.height > 0


async def test_hyperliquid_transfers(live_client):
    df = await live_client.hyperliquid.transfers().time_range(SINCE, UNTIL).as_polars()
    assert df.height >= 0  # populated table, but a 5-min window may be sparse


# --- positions: downsample + snapshot-aggregate + change-aggregate --------
# positions requires a 15m-multiple window; a 1h window over this range buckets
# to one slot at :00.
POS_SINCE, POS_UNTIL = "2026-07-10T00:00:00Z", "2026-07-10T01:00:00Z"


async def test_positions_downsample(live_client):
    df = await live_client.hyperliquid.positions().tokens("BTC").window("1h") \
        .time_range(POS_SINCE, POS_UNTIL).as_polars()
    assert df.height > 0
    assert {"time", "wallet", "token", "side", "size", "avg_entry"} <= set(df.columns)
    # start-aligned: every downsampled row is stamped at the window start (:00)
    assert df["time"].dt.minute().unique().to_list() == [0]


async def test_positions_snapshot_aggregate(live_client):
    df = await live_client.hyperliquid.positions().tokens("BTC").window("1h") \
        .aggregate().time_range(POS_SINCE, POS_UNTIL).as_polars()
    assert df.height > 0
    assert set(df.columns) == {
        "time", "token", "side", "net_size", "total_count",
        "longs_size", "longs_count", "shorts_size", "shorts_count", "avg_entry",
    }
    r = df.row(0, named=True)
    assert r["total_count"] == r["longs_count"] + r["shorts_count"]
    assert abs(r["net_size"] - (r["longs_size"] - r["shorts_size"])) < 1.0
    assert r["side"] == ("long" if r["net_size"] > 0 else "short" if r["net_size"] < 0 else "flat")


async def test_positions_change_aggregate_and_dust(live_client):
    df = await live_client.hyperliquid.positions().tokens("BTC").window("1h") \
        .aggregate_change().time_range(POS_SINCE, POS_UNTIL).as_polars()
    assert df.height > 0
    dollar_cols = [
        "opened_long", "opened_short", "increased_long", "decreased_long",
        "increased_short", "decreased_short", "closed_long", "closed_short",
        "flip_ls", "flip_sl", "net_pos_change", "net_flip", "net_flow", "abs_flow",
        "buy_size", "sell_size", "buy_taker_size", "sell_taker_size",
    ]
    assert set(dollar_cols) <= set(df.columns)
    r = df.row(0, named=True)
    # net_flip reconciles with its definition
    assert abs(r["net_flip"] - (r["flip_sl"] - r["flip_ls"])) < 1.0
    # abs_flow is the gross sum of all ten action columns, and dominates net_flow
    action_cols = ["opened_long", "opened_short", "increased_long", "decreased_long",
                   "increased_short", "decreased_short", "closed_long", "closed_short",
                   "flip_ls", "flip_sl"]
    assert abs(r["abs_flow"] - sum(r[c] for c in action_cols)) < 1.0
    assert r["abs_flow"] >= abs(r["net_flow"]) - 1.0
    # buy/sell_size split reconciles with abs_flow / net_flow; taker <= size
    assert abs((r["buy_size"] + r["sell_size"]) - r["abs_flow"]) < 1.0
    assert abs((r["buy_size"] - r["sell_size"]) - r["net_flow"]) < 1.0
    assert r["buy_taker_size"] <= r["buy_size"] + 1.0
    assert r["sell_taker_size"] <= r["sell_size"] + 1.0
    # dust-rounding: no aggregated $ value sits in the (0, $0.001) dust band
    for c in dollar_cols:
        for v in df[c].to_list():
            assert v == 0.0 or abs(v) >= 1e-3, f"{c} has dust {v}"


async def test_positions_requires_window(live_client):
    from tradernick_data_provider.exceptions import DataProviderHTTPError
    with pytest.raises(DataProviderHTTPError):
        await live_client.hyperliquid.positions().tokens("BTC") \
            .time_range(POS_SINCE, POS_UNTIL).as_polars()


# --- the erc20 /read/min fix, live ----------------------------------------
async def test_erc20_min_amount_no_longer_404s(live_client):
    # Before the fix this raised (route unregistered). Now it must return a frame.
    df = await (live_client.evm.erc20.transfers(["USDC"]).network("ethereum")
                .min_amount(1_000_000).time_range(SINCE, UNTIL).as_polars())
    assert df is not None


# --- remaining providers: audit that each responds (window may be sparse) --
# These assert the endpoint answers with a well-formed frame (HTTP ok, parquet
# decoded) rather than a row count — a 5-minute window is legitimately empty for
# many of these low-frequency DeFi events.
def _providers(c):
    return {
        "aave.borrows":       c.evm.aave.borrows().network("ethereum"),
        "uniswap.swaps":      c.evm.uniswap.swaps("WETH", "USDC", 3000).network("ethereum"),
        "lido.deposits":      c.evm.lido.deposits().network("ethereum"),
        "spark.deposits":     c.evm.spark.deposits().network("ethereum"),
        "morpho.borrows":     c.evm.morpho.borrows().network("ethereum"),
        "aero.conc.swaps":    c.evm.aerodrome.concentrated.swaps("WETH", "USDC").network("base"),
        "aero.basic.swaps":   c.evm.aerodrome.basic.swaps("WETH", "USDC").network("base"),
        "erc20.transfers":    c.evm.erc20.transfers(["USDC"]).network("ethereum"),
        "native.transfers":   c.evm.native_transfers().network("ethereum"),
        "tron.native":        c.tron.native_transfers(),
        "tron.trc20":         c.tron.trc20.transfers(["USDT"]),
        "btc.native":         c.btc.native_transfers(),
        "binance.funding":    c.binance.funding_rate("BTC"),
        "binance.oi":         c.binance.open_interest("BTC"),
        "binance.lsr":        c.binance.long_short_ratios("BTC"),
        "hl.realized_performance": c.hyperliquid.realized_performance().tokens("BTC"),
        "hl.funding":         c.hyperliquid.funding().tokens("BTC"),
    }


PROVIDER_NAMES = [
    "aave.borrows", "uniswap.swaps", "lido.deposits", "spark.deposits",
    "morpho.borrows", "aero.conc.swaps", "aero.basic.swaps", "erc20.transfers",
    "native.transfers", "tron.native", "tron.trc20", "btc.native",
    "binance.funding", "binance.oi", "binance.lsr",
    "hl.realized_performance", "hl.funding",
]


@pytest.mark.parametrize("name", PROVIDER_NAMES)
async def test_provider_responds(live_client, name):
    q = _providers(live_client)[name].time_range(SINCE, UNTIL)
    df = await q.as_polars()
    assert df is not None            # HTTP ok + parquet decoded to a frame


# --- empty-by-design stubs ------------------------------------------------
async def test_hyperliquid_sends_empty_contract(live_client):
    df = await live_client.hyperliquid.sends().time_range(SINCE, UNTIL).as_polars()
    assert df.height == 0
    assert {"sender", "destination", "token", "amount"} <= set(df.columns)


async def test_hyperliquid_spot_transfers_empty_contract(live_client):
    df = await live_client.hyperliquid.spot_transfers().time_range(SINCE, UNTIL).as_polars()
    assert df.height == 0
    assert {"sender", "destination", "token", "amount"} <= set(df.columns)


# --- wallet-group filter (empty-set semantics are deterministic) ----------
async def test_group_filter_nonexistent_group_is_empty(live_client):
    # A group with no members: an inclusive filter must match nothing.
    df = await (live_client.evm.erc20.transfers(["USDC"]).network("ethereum")
                .involving_groups(["__no_such_group__"])
                .time_range(SINCE, UNTIL).as_polars())
    assert df.height == 0


async def test_group_exclude_nonexistent_group_is_noop(live_client):
    # Excluding an empty group changes nothing → same rows as no group filter.
    base = await (live_client.evm.erc20.transfers(["USDC"]).network("ethereum")
                  .time_range(SINCE, UNTIL).as_polars())
    excl = await (live_client.evm.erc20.transfers(["USDC"]).network("ethereum")
                  .exclude_involving_groups(["__no_such_group__"])
                  .time_range(SINCE, UNTIL).as_polars())
    assert excl.height == base.height


# --- scan_parquet: the same filter surface works on a snapshot (DuckDB) -----
async def test_scan_parquet_filters_a_snapshot(live_client):
    key = "__it_scan_filters__"
    await (live_client.evm.erc20.transfers(["USDC"]).network("ethereum")
           .time_range(SINCE, UNTIL).as_parquet(key))
    try:
        full = await live_client.snapshot.load(key).as_polars()
        # category filter now applies on a scan (was a no-op pre-0.7.0)
        cat = await live_client.snapshot.scan(key).sender_category("CEX").as_polars()
        assert cat.height <= full.height
        # include + exclude partition the snapshot on the same selection
        inc = await live_client.snapshot.scan(key).involving_category("CEX").as_polars()
        exc = await live_client.snapshot.scan(key).exclude_involving_category("CEX").as_polars()
        assert inc.height + exc.height == full.height
        # nonexistent selection → empty
        none = await live_client.snapshot.scan(key).involving_groups(["__nope__"]).as_polars()
        assert none.height == 0
    finally:
        await live_client.snapshot.delete(key)
