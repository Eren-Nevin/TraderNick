"""Live polling for Binance perp long/short ratios. defistream 2.22
multi-token form — one call returns rows for every configured token."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import LONG_SHORT_COLUMNS, async_client, long_short_df_to_rows
from gap_fill import min_watermark_per_token, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_long_short_ratios] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
POLL_OVERLAP_MINUTES = 15


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.long_short_ratios()
        .token(*tokens)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = long_short_df_to_rows(df)
    ch = await async_client()
    await ch.insert("tradernick.binance_long_short_ratios", rows, column_names=LONG_SHORT_COLUMNS)
    return len(rows)


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.INGEST_TOKENS:
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    tokens = list(config.INGEST_TOKENS)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling %d tokens every %ss (overlap=%dm) + gap-fill from min-watermark — 1 multi-token call/tick",
             len(tokens), POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token rows=%d (tokens=%d)", n, len(tokens))
            except Exception as exc:
                log.exception("multi-token fetch failed: %s", exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        last_seen = await min_watermark_per_token(
            ch, table="tradernick.binance_long_short_ratios", tokens=tokens,
        )
        since = resolve_since(last_seen, t_start=t_start)
        if since >= t_start:
            return
        log.info("binance_long_short_ratios gap-fill since=%s until=%s (min_last_seen=%s, tokens=%d)",
                 since, t_start, last_seen, len(tokens))
        async def call(s, u):
            return await fetch_and_insert(ds, tokens, s, u)
        total = await run_chunked(label="binance_long_short_ratios", since=since, until=t_start, call=call)
        log.info("binance_long_short_ratios gap-fill done total_rows=%d", total)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
