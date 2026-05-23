import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [evm_erc20_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, chain: str, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.evm.erc20.transfers(token)
        .network(chain)
        .time_range(_iso(since), _iso(until))
        .verbose()
        .with_value()
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="erc20", chain=chain, token_override=token)
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    pairs = config.EVM_ERC20_PAIRS
    if not pairs:
        log.info("no EVM_ERC20_TRANSFERS pairs configured; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    log.info("polling erc20 pairs=%s every %ss (overlap=%dm)", pairs, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    while True:
        tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
        for chain, token in pairs:
            try:
                n = await fetch_and_insert(ds, chain, token, since, now)
                log.info("%s:%s rows=%d", chain, token, n)
            except Exception as exc:
                log.exception("%s:%s fetch failed: %s", chain, token, exc)
        await asyncio.sleep(max(0.0, tick_end - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
