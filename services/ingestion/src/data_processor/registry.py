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

import re
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

    def __post_init__(self) -> None:
        # Hard guarantee: every materializer reads its source WITH FINAL so the
        # rebuild collapses ReplacingMergeTree duplicates before aggregating.
        # This is what makes recalc idempotent under backfill replays — the
        # source can carry un-merged duplicate rows at rebuild time (e.g. right
        # after a non-forced backfill re-inserts overlapping rows). Validated
        # at construction, so it holds for EVERY recalc path: build_partition()
        # executes spec.rebuild_sql verbatim and is the single chokepoint shared
        # by the auto-fired downstream rebuild, a manual backfill_data_processor,
        # and the live sweep. A new/edited spec that drops FINAL fails loudly at
        # import instead of silently over-counting at runtime.
        if not re.search(rf"\bFROM\s+{re.escape(self.source_table)}\s+FINAL\b",
                         self.rebuild_sql, re.IGNORECASE):
            raise ValueError(
                f"MaterializerSpec {self.name!r}: rebuild_sql must read its "
                f"source {self.source_table!r} with FINAL (recalc relies on "
                f"FINAL to dedup the RMT source before aggregating)")


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

# ─────────────────────────────────────────────────────────────────────────────
# Per-(day, wallet) trade_history rollup — the token dimension summed away so
# global-scope smart_selector ranking reads one ~50× smaller row per wallet/day
# instead of scanning the 251M-row source FINAL and collapsing ~51 tokens on
# every cold query. Values are daily ABSOLUTE (cumulative-from-inception)
# snapshots; one row per (wallet, token, day) (verified), so summing over FINAL
# is exact. HIP3 builder perps (token contains ':') are excluded here to match
# the live global query's `position(token, ':') = 0` filter — the table has no
# token column, so it can't be filtered at read time. trade_count widened to
# UInt64 before summing. sumState (not raw) keeps the per-partition rebuild
# idempotent under backfill replays.
_HL_TRADE_HISTORY_WALLET_DAILY_SELECT = """
SELECT
    toDate(time) AS day,
    wallet,
    sumState(pnl)                   AS pnl_state,
    sumState(fees)                  AS fees_state,
    sumState(net_pnl)               AS net_pnl_state,
    sumState(funding)               AS funding_state,
    sumState(volume)                AS volume_state,
    sumState(buy_volume)            AS buy_volume_state,
    sumState(sell_volume)           AS sell_volume_state,
    sumState(toUInt64(trade_count)) AS trade_count_state
FROM tradernick.hl_trade_history FINAL
WHERE position(token, ':') = 0
GROUP BY day, wallet
"""

# ─────────────────────────────────────────────────────────────────────────────
# Per-(day, wallet) GLOBAL OI rollup — full materialization of smart_selector's
# `oi_per_day` for global scope (token dimension summed away). Powers the global
# OI metrics (avg_total/long/short OI $/token, avg_roe_pct, avg_position_count,
# last_*) AND the Sharpe denominator, so neither scans all-token hourly OI per
# query. Sourced from hl_position_history_1h (reuses its hourly bucketing).
#
# THREE levels: innermost argMaxMerges the AMT states per (bucket,token,side,
# wallet); the MIDDLE collapses to per-(bucket,wallet) sums with HIP3 excluded
# via the -If predicate (`position(token,':')=0` carried as `isreal`); the OUTER
# rolls buckets up to per-(day,wallet) — sumState for the windowable sums, the
# per-bucket RoE ratio summed exactly, argMaxIf/maxIf for the latest-bucket
# values. Read each with its matching -Merge.
#
# Body MUST stay a single flat top-level GROUP BY (no WHERE anywhere) so
# build_partition's WHERE-splice lands before `GROUP BY day, wallet`; `bucket`
# is exposed by the subqueries and the spliced `WHERE bucket >= …` pushes down
# into the innermost scan (verified bounded, not a full-table scan). A WHERE in
# any subquery would be miscaptured by the splice — HIP3 is done via -If instead.
_HL_POSITION_HISTORY_OI_WALLET_DAILY_SELECT = """
SELECT
    toDate(bucket) AS day,
    wallet,
    sumState(b_total_oi_token) AS s_total_oi_token_state,
    sumState(b_long_oi_token)  AS s_long_oi_token_state,
    sumState(b_short_oi_token) AS s_short_oi_token_state,
    sumState(b_total_oi_usd)   AS s_total_oi_usd_state,
    sumState(b_long_oi_usd)    AS s_long_oi_usd_state,
    sumState(b_short_oi_usd)   AS s_short_oi_usd_state,
    sumState(if(b_total_oi_usd > 0, b_unreal / b_total_oi_usd, 0)) AS s_roe_state,
    sumState(b_n_positions)    AS s_n_positions_state,
    uniqExactIfState(bucket, b_nonhip3 > 0)              AS n_buckets_state,
    argMaxIfState(b_total_oi_usd,   bucket, b_nonhip3 > 0) AS last_total_oi_usd_state,
    argMaxIfState(b_total_oi_token, bucket, b_nonhip3 > 0) AS last_total_oi_token_state,
    argMaxIfState(b_n_positions,    bucket, b_nonhip3 > 0) AS last_n_positions_state,
    maxIfState(bucket, b_nonhip3 > 0)                    AS last_bucket_state
FROM (
    SELECT bucket, wallet,
        sumIf(amt, isreal)                      AS b_total_oi_token,
        sumIf(amt, isreal AND side = 'long')    AS b_long_oi_token,
        sumIf(amt, isreal AND side = 'short')   AS b_short_oi_token,
        sumIf(sz,  isreal)                      AS b_total_oi_usd,
        sumIf(sz,  isreal AND side = 'long')    AS b_long_oi_usd,
        sumIf(sz,  isreal AND side = 'short')   AS b_short_oi_usd,
        sumIf(pnl, isreal)                      AS b_unreal,
        uniqExactIf(token, amt > 0 AND isreal)  AS b_n_positions,
        countIf(isreal)                         AS b_nonhip3
    FROM (
        SELECT bucket, token, side, wallet,
            argMaxMerge(amount_state) AS amt,
            argMaxMerge(size_state)   AS sz,
            argMaxMerge(pnl_state)    AS pnl,
            position(token, ':') = 0  AS isreal
        FROM tradernick.hl_position_history_1h FINAL
        GROUP BY bucket, token, side, wallet
    )
    GROUP BY bucket, wallet
)
GROUP BY day, wallet
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
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_position_history_1h",
        source_table="tradernick.hl_position_history",
        target_table="tradernick.hl_position_history_1h",
        source_time_col="time",
        rebuild_sql=_HL_POSITION_HISTORY_1H_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_position_history_eod_wallet",
        source_table="tradernick.hl_position_history",
        target_table="tradernick.hl_position_history_eod_wallet",
        source_time_col="time",
        rebuild_sql=_HL_POSITION_HISTORY_EOD_WALLET_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_fills_pnl_daily",
        source_table="tradernick.hl_fills",
        target_table="tradernick.hl_fills_pnl_daily",
        source_time_col="time",
        rebuild_sql=_HL_FILLS_PNL_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_fills_vol_daily",
        source_table="tradernick.hl_fills",
        target_table="tradernick.hl_fills_vol_daily",
        source_time_col="time",
        rebuild_sql=_HL_FILLS_VOL_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    MaterializerSpec(
        name="hl_funding_daily",
        source_table="tradernick.hl_funding",
        target_table="tradernick.hl_funding_daily",
        source_time_col="time",
        rebuild_sql=_HL_FUNDING_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    # Global smart_selector accelerators (sum metrics + Sharpe). Maintained by
    # the SAME data_processor worker/jobs as the rollups above — no new worker.
    MaterializerSpec(
        name="hl_trade_history_wallet_daily",
        source_table="tradernick.hl_trade_history",
        target_table="tradernick.hl_trade_history_wallet_daily",
        source_time_col="time",
        rebuild_sql=_HL_TRADE_HISTORY_WALLET_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
    # Sourced from the hl_position_history_1h rollup (above) — must rebuild
    # after it, hence ordered last here and last in the downstream list.
    MaterializerSpec(
        name="hl_position_history_oi_wallet_daily",
        source_table="tradernick.hl_position_history_1h",
        target_table="tradernick.hl_position_history_oi_wallet_daily",
        source_time_col="bucket",
        rebuild_sql=_HL_POSITION_HISTORY_OI_WALLET_DAILY_SELECT,
        partition_grain="day",
        recent_partitions=1,
        recent_cadence_s=30 * 60,
        sweep_window_days=7,
        sweep_cadence_s=6 * 60 * 60,
    ),
]


def by_name(name: str) -> MaterializerSpec | None:
    for spec in REGISTRY:
        if spec.name == name:
            return spec
    return None


ALL_NAMES: list[str] = [s.name for s in REGISTRY]
