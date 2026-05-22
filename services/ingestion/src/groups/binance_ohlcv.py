import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import OHLCV_COLUMNS, async_client, ohlcv_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_ohlcv] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.ohlcv()
        .token(token)
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
    log.info("polling tokens=%s every %ss (overlap=%dm)", tokens, config.POLL_INTERVAL_SECONDS, config.POLL_OVERLAP_MINUTES)

    while True:
        tick_end = time.monotonic() + config.POLL_INTERVAL_SECONDS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now - timedelta(minutes=config.POLL_OVERLAP_MINUTES)
        for token in tokens:
            try:
                n = await fetch_and_insert(ds, token, since, now)
                log.info("%s rows=%d", token, n)
            except Exception as exc:
                log.exception("%s fetch failed: %s", token, exc)
        await asyncio.sleep(max(0.0, tick_end - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
