"""Live polling for Binance 1m OHLCV.

defistream 2.22 added multi-token support to the binance.ohlcv builder
— `.token(*symbols)` returns rows for every symbol in one call, each
row carrying its own `token` column. We use that to fold the per-token
loop into a single API call per tick (and per gap-fill chunk).

Gap-fill takes the MIN(MAX(time)) across all tokens as the start, then
fires one multi-token call to recover [min_last_seen - overlap, t_start].
Tokens with more recent data get over-fetched but ReplacingMergeTree
dedupes on (token, time)."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import OHLCV_COLUMNS, async_client, ohlcv_df_to_rows
from gap_fill import min_watermark_per_token, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_ohlcv] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    """One multi-token call covering all configured tokens in one shot."""
    df = await (
        ds.exchange.binance.ohlcv()
        .token(*tokens)
        .window("1m")
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = ohlcv_df_to_rows(df)
    ch = await async_client()
    await ch.insert("tradernick.binance_ohlcv_1m", rows, column_names=OHLCV_COLUMNS)
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
             len(tokens), config.POLL_INTERVAL_SECONDS, config.POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + config.POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=config.POLL_OVERLAP_MINUTES)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token rows=%d (tokens=%d)", n, len(tokens))
            except Exception as exc:
                log.exception("multi-token fetch failed: %s", exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        last_seen = await min_watermark_per_token(
            ch, table="tradernick.binance_ohlcv_1m", tokens=tokens,
        )
        since = resolve_since(last_seen, t_start=t_start)
        if since >= t_start:
            return
        log.info("binance_ohlcv gap-fill since=%s until=%s (min_last_seen=%s, tokens=%d)",
                 since, t_start, last_seen, len(tokens))
        async def call(s, u):
            return await fetch_and_insert(ds, tokens, s, u)
        total = await run_chunked(label="binance_ohlcv", since=since, until=t_start, call=call)
        log.info("binance_ohlcv gap-fill done total_rows=%d", total)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
