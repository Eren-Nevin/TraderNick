"""MaterializerSpec catalogue. One entry per derived table.

The `rebuild_sql` for each spec is the SELECT body the data_processor will
wrap with an `INSERT INTO <staging> ... <SELECT> ... WHERE
<source_time_col> >= <start> AND <source_time_col> < <end>` envelope at
rebuild time. The SELECT bodies are byte-for-byte equivalent to the
existing push MVs in clickhouse/init/01_schema.sql so the migration
preserves aggregation semantics exactly.

Two things to know about the SELECT bodies:

  1. They all read `FROM <source> FINAL` so ReplacingMergeTree duplicates
     on the source side are collapsed before aggregation. This is what
     makes the pipeline idempotent under backfill replays — the original
     push MVs read raw INSERT batches and would over-count.

  2. The WHERE clause is injected on `source_time_col` (the source
     table's natural time column). For most specs that's the same column
     the target's bucket is derived from; for the daily HL aggregates
     it's still `time` on the source even though the target buckets by
     `day = toDate(time)`. The rebuild primitive translates a partition
     id like '2026-06-10' into [2026-06-10 00:00:00, 2026-06-11 00:00:00)
     for daily grain, '2026-06-10-14' into [..14:00, ..15:00) for
     hourly grain.

Cadence rationale (per the approved plan):
  - recent tier: 5 min for transfers, 15 min for HL
  - sweep tier: 30 d window, 6 h cadence — paranoia / safety net only,
    since the recent tier already covers everything that can plausibly
    change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MaterializerSpec:
    name: str                                # stable identifier — used in lock rows and job args
    source_table: str                        # fully qualified, e.g. "tradernick.transfers"
    target_table: str                        # fully qualified, e.g. "tradernick.exchange_flow_minute"
    source_time_col: str                     # column on source to bound the rebuild WHERE
    rebuild_sql: str                         # SELECT body, no INSERT prefix, no WHERE clause
    partition_grain: Literal["hour", "day"]
    recent_partitions: int                   # how many trailing partitions the recent tier touches each tick
    recent_cadence_s: int                    # cadence for the recent tier
    sweep_window_days: int                   # how far back the sweep tier covers
    sweep_cadence_s: int                     # cadence for the sweep tier


# ─────────────────────────────────────────────────────────────────────────────
# Exchange flow — sourced from tradernick.transfers' materialized category /
# entity columns. Hourly partitions on the target so the recent tier can
# rebuild the last 6 hours every 5 minutes cheaply (one hour of transfers
# FINAL ~ 1-2s wall-clock on the heavy chains).
# ─────────────────────────────────────────────────────────────────────────────

_EXCHANGE_FLOW_SELECT = """
SELECT
    classified.1 AS direction,
    classified.2 AS exchange,
    chain,
    token,
    toStartOfMinute(time) AS time,
    sum(amount) AS sum_amount,
    sum(if(token IN ('USDC', 'USDT', 'DAI', 'USDE'),
           amount,
           coalesce(value_usd, 0.0))) AS sum_value_usd,
    count() AS count
FROM tradernick.transfers FINAL
ARRAY JOIN arrayConcat(
    if(has(receiver_categories, 'binance-deposit')     AND NOT has(sender_categories, 'cex'),  [('in', 'binance')],     CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'coinbase-deposit')    AND NOT has(sender_categories, 'cex'),  [('in', 'coinbase')],    CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'okx-deposit')         AND NOT has(sender_categories, 'cex'),  [('in', 'okx')],         CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'bybit-deposit')       AND NOT has(sender_categories, 'cex'),  [('in', 'bybit')],       CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'hyperliquid-deposit') AND NOT has(sender_categories, 'perp'), [('in', 'hyperliquid')], CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'binance'     AND NOT has(receiver_categories, 'cex'),         [('out', 'binance')],     CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'coinbase'    AND NOT has(receiver_categories, 'cex'),         [('out', 'coinbase')],    CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'okx'         AND NOT has(receiver_categories, 'cex'),         [('out', 'okx')],         CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'bybit'       AND NOT has(receiver_categories, 'cex'),         [('out', 'bybit')],       CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'hyperliquid' AND coalesce(receiver_entity, '') != 'hyperliquid', [('out', 'hyperliquid')], CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String)))))
) AS classified
GROUP BY direction, exchange, chain, token, time
"""

# ─────────────────────────────────────────────────────────────────────────────
# HL position_history rollups — 15-minute, 1-hour, end-of-day. argMaxState on
# (amount, size, unrealized_pnl) so re-emission of the same source row is
# fully idempotent at the state layer even before we consider FROM FINAL.
# Daily partitions on the target.
# ─────────────────────────────────────────────────────────────────────────────

_HL_POSITION_HISTORY_15M_SELECT = """
SELECT
    toStartOfInterval(time, INTERVAL 15 MINUTE) AS bucket,
    token, side, wallet,
    argMaxState(amount,         time) AS amount_state,
    argMaxState(size,           time) AS size_state,
    argMaxState(unrealized_pnl, time) AS pnl_state
FROM tradernick.hl_position_history FINAL
GROUP BY bucket, token, side, wallet
"""

_HL_POSITION_HISTORY_1H_SELECT = """
SELECT
    toStartOfInterval(time, INTERVAL 1 HOUR) AS bucket,
    token, side, wallet,
    argMaxState(amount,         time) AS amount_state,
    argMaxState(size,           time) AS size_state,
    argMaxState(unrealized_pnl, time) AS pnl_state
FROM tradernick.hl_position_history FINAL
GROUP BY bucket, token, side, wallet
"""

_HL_POSITION_HISTORY_EOD_WALLET_SELECT = """
SELECT
    toDate(time) AS day,
    wallet, token, side,
    argMaxState(unrealized_pnl, time) AS pnl_state
FROM tradernick.hl_position_history FINAL
GROUP BY day, wallet, token, side
"""

# ─────────────────────────────────────────────────────────────────────────────
# HL fills rollups — daily PnL (close-only) and daily volume (open + close).
# Both are sumState so they NEED FROM FINAL to avoid the same compounding the
# exchange_flow rollup exhibited under backfills. Position-side mapping
# matches the existing MVs byte-for-byte.
# ─────────────────────────────────────────────────────────────────────────────

_HL_FILLS_PNL_DAILY_SELECT = """
SELECT
    toDate(time) AS day,
    wallet, token,
    multiIf(
        dir IN ('Close Long',  'Long > Short'), 'long',
        dir IN ('Close Short', 'Short > Long'), 'short',
        ''
    ) AS side,
    sumState(closed_pnl) AS pnl_state
FROM tradernick.hl_fills FINAL
WHERE dir IN ('Close Long', 'Close Short', 'Long > Short', 'Short > Long')
GROUP BY day, wallet, token, side
"""

_HL_FILLS_VOL_DAILY_SELECT = """
SELECT
    toDate(time) AS day,
    wallet, token,
    multiIf(
        dir IN ('Open Long',  'Close Long',  'Long > Short'), 'long',
        dir IN ('Open Short', 'Close Short', 'Short > Long'), 'short',
        ''
    ) AS position_side,
    sumState(size)                                            AS vol_token_state,
    sumState(size * price)                                    AS vol_usd_state,
    sumStateIf(size,         crossed = 1 AND side = 'B')      AS taker_buy_vol_token_state,
    sumStateIf(size * price, crossed = 1 AND side = 'B')      AS taker_buy_vol_usd_state,
    sumStateIf(size,         crossed = 1 AND side = 'A')      AS taker_sell_vol_token_state,
    sumStateIf(size * price, crossed = 1 AND side = 'A')      AS taker_sell_vol_usd_state
FROM tradernick.hl_fills FINAL
WHERE dir IN ('Open Long', 'Close Long', 'Long > Short',
              'Open Short', 'Close Short', 'Short > Long')
GROUP BY day, wallet, token, position_side
"""

# ─────────────────────────────────────────────────────────────────────────────
# HL funding rollup — sum of signed amount per (day, wallet, token). Same
# sumState idempotency caveat as fills_pnl_daily.
# ─────────────────────────────────────────────────────────────────────────────

_HL_FUNDING_DAILY_SELECT = """
SELECT
    toDate(time) AS day,
    wallet,
    token,
    sumState(amount) AS funding_pnl_state
FROM tradernick.hl_funding FINAL
GROUP BY day, wallet, token
"""


REGISTRY: list[MaterializerSpec] = [
    MaterializerSpec(
        name="exchange_flow_minute",
        source_table="tradernick.transfers",
        target_table="tradernick.exchange_flow_minute",
        source_time_col="time",
        rebuild_sql=_EXCHANGE_FLOW_SELECT,
        partition_grain="hour",
        recent_partitions=6,
        recent_cadence_s=5 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_position_history_15m",
        source_table="tradernick.hl_position_history",
        target_table="tradernick.hl_position_history_15m",
        source_time_col="time",
        rebuild_sql=_HL_POSITION_HISTORY_15M_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_position_history_1h",
        source_table="tradernick.hl_position_history",
        target_table="tradernick.hl_position_history_1h",
        source_time_col="time",
        rebuild_sql=_HL_POSITION_HISTORY_1H_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_position_history_eod_wallet",
        source_table="tradernick.hl_position_history",
        target_table="tradernick.hl_position_history_eod_wallet",
        source_time_col="time",
        rebuild_sql=_HL_POSITION_HISTORY_EOD_WALLET_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_fills_pnl_daily",
        source_table="tradernick.hl_fills",
        target_table="tradernick.hl_fills_pnl_daily",
        source_time_col="time",
        rebuild_sql=_HL_FILLS_PNL_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_fills_vol_daily",
        source_table="tradernick.hl_fills",
        target_table="tradernick.hl_fills_vol_daily",
        source_time_col="time",
        rebuild_sql=_HL_FILLS_VOL_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_funding_daily",
        source_table="tradernick.hl_funding",
        target_table="tradernick.hl_funding_daily",
        source_time_col="time",
        rebuild_sql=_HL_FUNDING_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=3,
        recent_cadence_s=15 * 60,
        sweep_window_days=30,
        sweep_cadence_s=6 * 60 * 60,
    ),
]


def by_name(name: str) -> MaterializerSpec | None:
    for spec in REGISTRY:
        if spec.name == name:
            return spec
    return None


ALL_NAMES: list[str] = [s.name for s in REGISTRY]
