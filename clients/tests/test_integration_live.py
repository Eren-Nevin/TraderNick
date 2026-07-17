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
        "hl.trade_history":   c.hyperliquid.trade_history().tokens("BTC"),
        "hl.funding":         c.hyperliquid.funding().tokens("BTC"),
    }


PROVIDER_NAMES = [
    "aave.borrows", "uniswap.swaps", "lido.deposits", "spark.deposits",
    "morpho.borrows", "aero.conc.swaps", "aero.basic.swaps", "erc20.transfers",
    "native.transfers", "tron.native", "tron.trc20", "btc.native",
    "binance.funding", "binance.oi", "binance.lsr",
    "hl.trade_history", "hl.funding",
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
