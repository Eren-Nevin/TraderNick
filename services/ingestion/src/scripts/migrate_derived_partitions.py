"""One-shot migration: switch the 7 derived tables to finer partition keys
so the data_processor worker can rebuild them atomically per partition.

Old → new partition key:

    exchange_flow_minute              toYYYYMM(time)   → toStartOfHour(time)
    hl_position_history_15m           toYYYYMM(bucket) → toDate(bucket)
    hl_position_history_1h            toYYYYMM(bucket) → toDate(bucket)
    hl_position_history_eod_wallet    toYYYYMM(day)    → day
    hl_fills_pnl_daily                toYYYYMM(day)    → day
    hl_fills_vol_daily                toYYYYMM(day)    → day
    hl_funding_daily                  toYYYYMM(day)    → day

Strategy per table:
    1. CREATE TABLE <name>_new (<same shape, new PARTITION BY>);
    2. INSERT INTO <name>_new SELECT * FROM <name> FINAL;
    3. RENAME TABLE <name> TO <name>_old, <name>_new TO <name>;
    4. DROP TABLE <name>_old;

Steps are idempotent across re-runs: a partial run that left a stale
`_new` is dropped at the top of each table's loop. Live ingest into the
SOURCE tables (transfers, hl_position_history, hl_fills, hl_funding)
should NOT be paused; the source data is untouched. The MVs currently
firing INTO these targets will keep writing to the renamed-out _old
table until step 3 swaps; we then drop _old and the next data_processor
tick (or the next live MV-fire, until we drop the MVs) populates the
freshly-partitioned table.

CAVEAT: while this script is running, the SOURCE-side MVs are still in
place. Anything they write between steps 2 and 3 lands in the _old
table and is lost on DROP. The window is short (seconds for the small
tables, minutes for hl_position_history) but non-zero. The safest
sequencing is:

    A. STOP live source streams (transfers + HL).
    B. Run this migration.
    C. Drop the 7 MVs (separate step in 01_schema.sql).
    D. Start data_processor.processor_live.
    E. START live source streams.

This script does NOT drop the MVs — that step is part of the schema
update because dropping VIEWs is irreversible without a rebuild and
should be reviewed alongside the schema diff. Run this script after the
schema diff is reviewed and the source streams are paused.
"""
from __future__ import annotations

import asyncio
import logging

from clickhouse import async_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [migrate_derived_partitions] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


_MIGRATIONS: list[dict] = [
    {
        "name": "exchange_flow_minute",
        "create_new_sql": """
            CREATE TABLE tradernick.exchange_flow_minute_new
            (
                direction     LowCardinality(String),
                exchange      LowCardinality(String),
                chain         LowCardinality(String),
                token         LowCardinality(String),
                time          DateTime          CODEC(DoubleDelta, ZSTD(3)),
                sum_amount    Float64           CODEC(Gorilla, ZSTD(3)),
                sum_value_usd Float64           CODEC(Gorilla, ZSTD(3)),
                count         UInt64            CODEC(T64, ZSTD(3))
            )
            ENGINE = SummingMergeTree
            PARTITION BY toStartOfHour(time)
            ORDER BY (direction, exchange, chain, token, time)
            TTL time + INTERVAL 270 DAY
        """,
    },
    {
        "name": "hl_position_history_15m",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_position_history_15m_new
            (
                bucket          DateTime       CODEC(DoubleDelta, ZSTD(3)),
                token           LowCardinality(String),
                side            LowCardinality(String),
                wallet          String         CODEC(ZSTD(3)),
                amount_state    AggregateFunction(argMax, Float64, DateTime64(3)),
                size_state      AggregateFunction(argMax, Float64, DateTime64(3)),
                pnl_state       AggregateFunction(argMax, Float64, DateTime64(3))
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY toDate(bucket)
            ORDER BY (token, bucket, side, wallet)
            TTL bucket + INTERVAL 270 DAY
        """,
    },
    {
        "name": "hl_position_history_1h",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_position_history_1h_new
            (
                bucket          DateTime       CODEC(DoubleDelta, ZSTD(3)),
                token           LowCardinality(String),
                side            LowCardinality(String),
                wallet          String         CODEC(ZSTD(3)),
                amount_state    AggregateFunction(argMax, Float64, DateTime64(3)),
                size_state      AggregateFunction(argMax, Float64, DateTime64(3)),
                pnl_state       AggregateFunction(argMax, Float64, DateTime64(3))
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY toDate(bucket)
            ORDER BY (token, bucket, side, wallet)
            TTL bucket + INTERVAL 270 DAY
        """,
    },
    {
        "name": "hl_position_history_eod_wallet",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_position_history_eod_wallet_new
            (
                day         Date           CODEC(DoubleDelta, ZSTD(3)),
                wallet      String         CODEC(ZSTD(3)),
                token       LowCardinality(String),
                side        LowCardinality(String),
                pnl_state   AggregateFunction(argMax, Float64, DateTime64(3))
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY day
            ORDER BY (day, wallet, token, side)
            TTL day + INTERVAL 271 DAY
        """,
    },
    {
        "name": "hl_fills_pnl_daily",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_fills_pnl_daily_new
            (
                day       Date           CODEC(DoubleDelta, ZSTD(3)),
                wallet    String         CODEC(ZSTD(3)),
                token     LowCardinality(String),
                side      LowCardinality(String),
                pnl_state AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY day
            ORDER BY (day, wallet, token, side)
            TTL day + INTERVAL 271 DAY
        """,
    },
    {
        "name": "hl_fills_vol_daily",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_fills_vol_daily_new
            (
                day                          Date            CODEC(DoubleDelta, ZSTD(3)),
                wallet                       String          CODEC(ZSTD(3)),
                token                        LowCardinality(String),
                position_side                LowCardinality(String),
                vol_token_state              AggregateFunction(sum, Float64),
                vol_usd_state                AggregateFunction(sum, Float64),
                taker_buy_vol_token_state    AggregateFunction(sum, Float64),
                taker_buy_vol_usd_state      AggregateFunction(sum, Float64),
                taker_sell_vol_token_state   AggregateFunction(sum, Float64),
                taker_sell_vol_usd_state     AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY day
            ORDER BY (day, wallet, token, position_side)
            TTL day + INTERVAL 271 DAY
        """,
    },
    {
        "name": "hl_funding_daily",
        "create_new_sql": """
            CREATE TABLE tradernick.hl_funding_daily_new
            (
                day               Date           CODEC(DoubleDelta, ZSTD(3)),
                wallet            String         CODEC(ZSTD(3)),
                token             LowCardinality(String),
                funding_pnl_state AggregateFunction(sum, Float64)
            ) ENGINE = AggregatingMergeTree()
            PARTITION BY day
            ORDER BY (day, wallet, token)
            TTL day + INTERVAL 271 DAY
        """,
    },
]


async def _migrate_one(ch, mig: dict) -> None:
    name = mig["name"]
    log.info("=== %s ===", name)
    # Cleanup from a prior failed run.
    await ch.command(f"DROP TABLE IF EXISTS tradernick.{name}_new")
    await ch.command(f"DROP TABLE IF EXISTS tradernick.{name}_old")
    log.info("  create new table with new PARTITION BY")
    await ch.command(mig["create_new_sql"])
    log.info("  INSERT FROM old FINAL (this is the long step)")
    await ch.command(
        f"INSERT INTO tradernick.{name}_new "
        f"SELECT * FROM tradernick.{name} FINAL "
        f"SETTINGS max_execution_time = 7200"
    )
    log.info("  RENAME swap")
    await ch.command(
        f"RENAME TABLE tradernick.{name} TO tradernick.{name}_old, "
        f"tradernick.{name}_new TO tradernick.{name}"
    )
    log.info("  DROP old")
    await ch.command(f"DROP TABLE tradernick.{name}_old")
    rows = await ch.query(f"SELECT count() FROM tradernick.{name}")
    n = int(rows.result_rows[0][0]) if rows.result_rows else 0
    log.info("  done: %d rows in tradernick.%s", n, name)


async def main():
    ch = await async_client()
    log.info("starting migration for %d derived tables", len(_MIGRATIONS))
    for mig in _MIGRATIONS:
        await _migrate_one(ch, mig)
    log.info("migration complete")


if __name__ == "__main__":
    asyncio.run(main())
