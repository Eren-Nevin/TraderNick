import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [evm_native_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3

# Canonical native-asset symbol per EVM chain — gets written to the
# `token` column so the dashboard shows ETH / BNB / POL rather than
# whatever DeFiStream happens to emit. Polygon's native asset was
# renamed MATIC → POL in 2024; we store the new symbol.
NATIVE_TOKEN_BY_CHAIN: dict[str, str] = {
    "ETH": "ETH",
    "ARB": "ETH",
    "BASE": "ETH",
    "BSC": "BNB",
    "POLYGON": "POL",
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, chain: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.evm.native.transfers()
        .network(chain)
        .time_range(_iso(since), _iso(until))
        .verbose()
        .with_value()
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    token = NATIVE_TOKEN_BY_CHAIN.get(chain.upper())
    rows = transfers_df_to_rows(df, kind="native", chain=chain, token_override=token)
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    chains = config.EVM_NATIVE_CHAINS
    if not chains:
        log.info("no EVM_NATIVE_TRANSFERS chains configured; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling evm native chains=%s every %ss (overlap=%dm) + gap-fill from watermark",
             chains, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
            for chain in chains:
                try:
                    n = await fetch_and_insert(ds, chain, since, now)
                    log.info("%s rows=%d", chain, n)
                except Exception as exc:
                    log.exception("%s fetch failed: %s", chain, exc)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain):
            # The native poller writes kind='native'. Token is whatever
            # NATIVE_TOKEN_BY_CHAIN maps the chain to (defaults to chain
            # itself if missing). Filter only by (kind, chain) since one
            # poller covers exactly one token per chain.
            last_seen = await latest_time(
                ch, table="tradernick.transfers",
                where="kind = 'native' AND chain = {chain:String}",
                parameters={"chain": chain},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"evm_native_transfers/{chain}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                return await fetch_and_insert(ds, chain, s, u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(c) for c in chains), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
