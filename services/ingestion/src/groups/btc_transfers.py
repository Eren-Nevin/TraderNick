import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [btc_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 15


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, since: datetime, until: datetime) -> int:
    df = await (
        ds.bitcoin.native.transfers()
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="btc", chain="BTC", token_override="BTC")
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.BTC_TRANSFERS_ENABLED:
        log.info("BTC_TRANSFERS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling btc transfers every %ss (overlap=%dm) + gap-fill from watermark",
             POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
            try:
                n = await fetch_and_insert(ds, since, now)
                log.info("BTC rows=%d", n)
            except Exception as exc:
                log.exception("BTC fetch failed: %s", exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        last_seen = await latest_time(
            ch, table="tradernick.transfers",
            where="kind = 'btc' AND chain = 'BTC'",
        )
        since = resolve_since(last_seen, t_start=t_start)
        if since >= t_start: return
        log.info("btc_transfers gap-fill since=%s until=%s (last_seen=%s)", since, t_start, last_seen)
        async def call(s, u):
            return await fetch_and_insert(ds, s, u)
        total = await run_chunked(label="btc_transfers", since=since, until=t_start, call=call)
        log.info("btc_transfers gap-fill done total_rows=%d", total)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
