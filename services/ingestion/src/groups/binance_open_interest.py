"""Live polling for Binance perp open interest. defistream 2.22 multi-token
form — one call returns rows for every configured token."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import OPEN_INTEREST_COLUMNS, async_client, open_interest_df_to_rows
from gap_fill import min_watermark_per_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_open_interest] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.open_interest()
        .token(*tokens)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = open_interest_df_to_rows(df)
    ch = await async_client()
    await ch.insert("tradernick.binance_open_interest", rows, column_names=OPEN_INTEREST_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.INGEST_TOKENS:
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    tokens = list(config.INGEST_TOKENS)
    log.info("polling %d tokens every %ss + gap-fill from min-watermark — 1 multi-token call/tick",
             len(tokens), POLL_INTERVAL_SECONDS)

    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - sweep.LIVE_OVERLAP
            n = 0
            err: str | None = None
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token rows=%d (tokens=%d)", n, len(tokens))
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("multi-token fetch failed: %s", exc)
            if stream_name:
                await ch_status.write_tick(stream_name, n, error=err)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop():
        jitter = sweep.sweep_jitter_s(sweep_cadence)
        log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
        await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                last_seen = await min_watermark_per_token(ch, table="tradernick.binance_open_interest", tokens=tokens)
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since < now:
                    n = await fetch_and_insert(ds, tokens, since, now)
                    log.info("binance_open_interest sweep window=%s..%s rows=%d (min_last_seen=%s, tokens=%d)", since, now, n, last_seen, len(tokens))
            except Exception as exc:
                log.exception("binance_open_interest sweep failed: %s", exc)
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())



if __name__ == "__main__":
    asyncio.run(main())
