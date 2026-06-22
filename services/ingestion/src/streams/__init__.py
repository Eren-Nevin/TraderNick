"""Registry of per-stream ingestion workers.

One process = one event type. The supervisor walks STREAMS at startup,
consults tradernick.ingestion_event_state for each one's on/off, and spawns
a subprocess per enabled stream.

Each StreamSpec carries a `group` label used by the admin panel UI to lay
out one table per protocol section (HL streams together, AAVE V3 together,
etc.). The group has no runtime meaning — purely presentation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamSpec:
    name: str               # e.g. "hyperliquid.ohlcv"
    module: str             # e.g. "streams.hyperliquid_ohlcv"
    group: str              # UI section label: "Hyperliquid", "AAVE V3", etc.
    cadence_s: int          # live tick interval in seconds — surfaced in the
                            # admin UI as the "Refresh" column. Matches the
                            # POLL_INTERVAL_SECONDS of the worker's module.
    enabled_default: bool = True


# HL event cadences mirror _CADENCE in groups/hyperliquid_events.py.
# All non-funding/vault events moved to 15m on 2026-06-11.
_HL_CADENCE = {
    "ohlcv": 900, "trades": 900, "fills": 900,
    "position_history": 900, "trade_history": 900, "transfers": 900,
    "funding": 1800, "vaults": 1800,
}


def _hl(ev: str) -> StreamSpec:
    return StreamSpec(f"hyperliquid.{ev}", f"streams.hyperliquid_{ev}", "Hyperliquid", _HL_CADENCE[ev])


# Binance OHLCV + raw_trades inherit the global POLL_INTERVAL_SECONDS=60
# default; the others hard-code their own (see groups/*.py).
_BINANCE_CADENCE = {
    "ohlcv": 300, "raw_trades": 300,
    "spot_ohlcv": 300, "spot_raw_trades": 300,
    "open_interest": 300, "long_short_ratios": 300,
    "funding_rate": 1800,
    "book_depth": 300,
}

# Streams that ship disabled by default — must be flipped on from the
# admin UI before they spawn. Used for endpoints that cost extra quota
# or aren't ready for production wiring yet.
_BINANCE_OFF_BY_DEFAULT = {"book_depth"}


def _binance(ev: str) -> StreamSpec:
    return StreamSpec(
        f"binance.{ev}", f"streams.binance_{ev}", "Binance",
        _BINANCE_CADENCE[ev],
        enabled_default=ev not in _BINANCE_OFF_BY_DEFAULT,
    )


def _aave(version: str, ev: str) -> StreamSpec:
    """version is 'v2'/'v3'/'v4' — only V3 has the eth_market split which the
    group module already handles internally."""
    label = f"AAVE {version.upper()}"
    return StreamSpec(f"aave_{version}.{ev}", f"streams.aave_{version}_{ev}", label, 300)


def _uni(version: str, ev: str) -> StreamSpec:
    label = f"Uniswap {version.upper()}"
    return StreamSpec(f"uniswap_{version}.{ev}", f"streams.uniswap_{version}_{ev}", label, 300)


STREAMS: list[StreamSpec] = [
    # Hyperliquid — 8 events.
    _hl("ohlcv"),
    _hl("trades"),
    _hl("fills"),
    _hl("funding"),
    _hl("position_history"),
    _hl("trade_history"),
    _hl("transfers"),
    _hl("vaults"),

    # Binance — perp/futures + spot streams.
    _binance("ohlcv"),
    _binance("raw_trades"),
    _binance("spot_ohlcv"),
    _binance("spot_raw_trades"),
    _binance("open_interest"),
    _binance("long_short_ratios"),
    _binance("funding_rate"),
    _binance("book_depth"),

    # Transfers — 5 streams (one per chain-family, multi-asset internally).
    StreamSpec("transfers.btc",         "streams.btc_transfers",         "Transfers", 1800),
    StreamSpec("transfers.tron_native", "streams.tron_native_transfers", "Transfers", 300),
    StreamSpec("transfers.tron_trc20",  "streams.tron_trc20_transfers",  "Transfers", 300),
    StreamSpec("transfers.evm_native", "streams.evm_native_transfers",  "Transfers", 300),
    StreamSpec("transfers.evm_erc20",  "streams.evm_erc20_transfers",   "Transfers", 300),

    # AAVE V3 — 6 events.
    _aave("v3", "deposit"),
    _aave("v3", "withdraw"),
    _aave("v3", "borrow"),
    _aave("v3", "repay"),
    _aave("v3", "flashloan"),
    _aave("v3", "liquidation"),

    # AAVE V2 — 6 events.
    _aave("v2", "deposit"),
    _aave("v2", "withdraw"),
    _aave("v2", "borrow"),
    _aave("v2", "repay"),
    _aave("v2", "flashloan"),
    _aave("v2", "liquidation"),

    # AAVE V4 — 5 events (no flashloan).
    _aave("v4", "deposit"),
    _aave("v4", "withdraw"),
    _aave("v4", "borrow"),
    _aave("v4", "repay"),
    _aave("v4", "liquidation"),

    # Uniswap V3 — 4 events.
    _uni("v3", "swap"),
    _uni("v3", "deposit"),
    _uni("v3", "withdraw"),
    _uni("v3", "collect"),

    # Uniswap V2 — 4 events.
    _uni("v2", "swap"),
    _uni("v2", "deposit"),
    _uni("v2", "withdraw"),
    _uni("v2", "collect"),

    # Uniswap V4 — 4 events.
    _uni("v4", "swap"),
    _uni("v4", "deposit"),
    _uni("v4", "withdraw"),
    _uni("v4", "collect"),

    # Aerodrome (concentrated) — 4 events.
    StreamSpec("aerodrome.swaps",        "streams.aerodrome_swaps",        "Aerodrome", 300),
    StreamSpec("aerodrome.deposits",     "streams.aerodrome_deposits",     "Aerodrome", 300),
    StreamSpec("aerodrome.withdrawals",  "streams.aerodrome_withdrawals",  "Aerodrome", 300),
    StreamSpec("aerodrome.collects",     "streams.aerodrome_collects",     "Aerodrome", 300),

    # Aerodrome (basic) — 4 events.
    StreamSpec("aerodrome_basic.swaps",       "streams.aerodrome_basic_swaps",       "Aerodrome Basic", 300),
    StreamSpec("aerodrome_basic.deposits",    "streams.aerodrome_basic_deposits",    "Aerodrome Basic", 300),
    StreamSpec("aerodrome_basic.withdrawals", "streams.aerodrome_basic_withdrawals", "Aerodrome Basic", 300),
    StreamSpec("aerodrome_basic.claims",      "streams.aerodrome_basic_claims",      "Aerodrome Basic", 300),

    # Lido — 5 events.
    StreamSpec("lido.deposit",                "streams.lido_deposit",                "Lido", 300),
    StreamSpec("lido.withdrawal_request",     "streams.lido_withdrawal_request",     "Lido", 300),
    StreamSpec("lido.withdrawal_claimed",     "streams.lido_withdrawal_claimed",     "Lido", 300),
    StreamSpec("lido.l2_deposit",             "streams.lido_l2_deposit",             "Lido", 300),
    StreamSpec("lido.l2_withdrawal_request",  "streams.lido_l2_withdrawal_request",  "Lido", 300),

    # Morpho — 7 events.
    StreamSpec("morpho.supply",              "streams.morpho_supply",              "Morpho", 300),
    StreamSpec("morpho.withdraw",            "streams.morpho_withdraw",            "Morpho", 300),
    StreamSpec("morpho.borrow",              "streams.morpho_borrow",              "Morpho", 300),
    StreamSpec("morpho.repay",               "streams.morpho_repay",               "Morpho", 300),
    StreamSpec("morpho.supply_collateral",   "streams.morpho_supply_collateral",   "Morpho", 300),
    StreamSpec("morpho.withdraw_collateral", "streams.morpho_withdraw_collateral", "Morpho", 300),
    StreamSpec("morpho.liquidation",         "streams.morpho_liquidation",         "Morpho", 300),

    # Spark — 6 events.
    StreamSpec("spark.deposit",     "streams.spark_deposit",     "Spark", 300),
    StreamSpec("spark.withdraw",    "streams.spark_withdraw",    "Spark", 300),
    StreamSpec("spark.borrow",      "streams.spark_borrow",      "Spark", 300),
    StreamSpec("spark.repay",       "streams.spark_repay",       "Spark", 300),
    StreamSpec("spark.flashloan",   "streams.spark_flashloan",   "Spark", 300),
    StreamSpec("spark.liquidation", "streams.spark_liquidation", "Spark", 300),

    # GMX — 9 events.
    StreamSpec("gmx.position_increase", "streams.gmx_position_increase", "GMX", 300),
    StreamSpec("gmx.position_decrease", "streams.gmx_position_decrease", "GMX", 300),
    StreamSpec("gmx.liquidation",       "streams.gmx_liquidation",       "GMX", 300),
    StreamSpec("gmx.swap",              "streams.gmx_swap",              "GMX", 300),
    StreamSpec("gmx.deposit",           "streams.gmx_deposit",           "GMX", 300),
    StreamSpec("gmx.withdraw",          "streams.gmx_withdraw",          "GMX", 300),
    StreamSpec("gmx.funding",           "streams.gmx_funding",           "GMX", 300),
    StreamSpec("gmx.borrowing",         "streams.gmx_borrowing",         "GMX", 300),
    StreamSpec("gmx.fees_collected",    "streams.gmx_fees_collected",    "GMX", 300),

    # Data process — derived-MV materializer worker. Replaces both the
    # old `exchange_flow_self_heal` 15-min rebuild loop AND the seven
    # push MVs (mv_exchange_flow, hl_position_history_*_mv, hl_fills_*_mv,
    # hl_funding_daily_mv). Single process, all seven materializers,
    # tiered rebuild via atomic REPLACE PARTITION.
    #
    # cadence_s shown in the admin UI is the SHORTEST recent-tier cadence
    # across the registry (5 min — exchange_flow). Per-materializer
    # cadence comes from data_processor.registry.REGISTRY.
    #
    # Ships disabled by default during rollout. Enable from the admin
    # panel after the schema migration runs and a parallel-validation
    # window confirms output matches the old MVs.
    StreamSpec(
        "data_process.processor_live",
        "data_processor.live",
        "Data process",
        5 * 60,
        enabled_default=False,
    ),
]


def by_name(name: str) -> StreamSpec | None:
    for s in STREAMS:
        if s.name == name:
            return s
    return None
