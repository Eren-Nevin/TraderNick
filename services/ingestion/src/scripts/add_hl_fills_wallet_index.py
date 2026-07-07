"""Add a bloom-filter skip index on hl_fills.wallet (idempotent).

hl_fills is sorted (token, time, tid, wallet), so a wallet-only filter (e.g. the
wallet page Trades table, /hyperliquid/wallet_fills) can't use the primary index and
must scan the whole time window across every token. A bloom_filter skip index on
`wallet` lets ClickHouse skip granules that don't contain the wallet — big win for
point-wallet lookups. GRANULARITY 1 (one bloom per 8192-row granule) maximises skipping
for this high-cardinality, highly-selective filter.

Run once against an existing table:  python -m scripts.add_hl_fills_wallet_index
New parts index automatically on write; MATERIALIZE INDEX backfills existing parts (a
background mutation on the full table — minutes on a multi-billion-row table).
"""

import asyncio
import os

from clickhouse_connect import get_async_client

TABLE = "tradernick.hl_fills"
INDEX = "idx_wallet"


async def main() -> None:
    ch = await get_async_client(
        host=os.environ.get("CLICKHOUSE_HOST", "clickhouse"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )
    await ch.command(
        f"ALTER TABLE {TABLE} ADD INDEX IF NOT EXISTS {INDEX} wallet "
        f"TYPE bloom_filter GRANULARITY 1"
    )
    print(f"index {INDEX} present on {TABLE}")
    # Background backfill of existing parts (returns once the mutation is queued).
    await ch.command(
        f"ALTER TABLE {TABLE} MATERIALIZE INDEX {INDEX} SETTINGS mutations_sync = 0"
    )
    print("MATERIALIZE INDEX kicked (runs in the background; watch system.mutations)")


if __name__ == "__main__":
    asyncio.run(main())
