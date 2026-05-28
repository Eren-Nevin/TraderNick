import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import FUNDING_RATE_COLUMNS, async_client, funding_rate_df_to_rows
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_funding_rate] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800
POLL_OVERLAP_MINUTES = 1440


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.funding_rate()
        .token(token)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = funding_rate_df_to_rows(df, token)
    ch = await async_client()
    await ch.insert("tradernick.binance_funding_rate", rows, column_names=FUNDING_RATE_COLUMNS)
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
    log.info("polling funding_rate tokens=%s every %ss (overlap=%dm) + gap-fill from watermark",
             tokens, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
            for token in tokens:
                try:
                    n = await fetch_and_insert(ds, token, since, now)
                    log.info("%s rows=%d", token, n)
                except Exception as exc:
                    log.exception("%s fetch failed: %s", token, exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(token):
            last_seen = await latest_time(
                ch, table="tradernick.binance_funding_rate",
                where="token = {token:String}",
                parameters={"token": token},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"binance_funding_rate/{token}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                return await fetch_and_insert(ds, token, s, u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(t) for t in tokens), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
