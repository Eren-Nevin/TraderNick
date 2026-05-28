import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time, resolve_since, run_chunked

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
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling erc20 pairs=%s every %ss (overlap=%dm) + gap-fill from watermark",
             pairs, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
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

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, token):
            last_seen = await latest_time(
                ch, table="tradernick.transfers",
                where="kind = 'erc20' AND chain = {chain:String} AND token = {token:String}",
                parameters={"chain": chain, "token": token},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"evm_erc20_transfers/{chain}:{token}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                return await fetch_and_insert(ds, chain, token, s, u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(c, t) for c, t in pairs), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
