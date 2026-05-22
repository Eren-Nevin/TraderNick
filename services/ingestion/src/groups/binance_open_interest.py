import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import OPEN_INTEREST_COLUMNS, async_client, open_interest_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_open_interest] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
POLL_OVERLAP_MINUTES = 15


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.open_interest()
        .token(token)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = open_interest_df_to_rows(df, token)
    ch = await async_client()
    await ch.insert("tradernick.binance_open_interest", rows, column_names=OPEN_INTEREST_COLUMNS)
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
    log.info("polling open_interest tokens=%s every %ss (overlap=%dm)", tokens, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

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


if __name__ == "__main__":
    asyncio.run(main())
