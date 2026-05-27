"""Live polling for AAVE v4 events (ETH only). 5 events × N chains.
Default chain set = ['ETH'] since V4 is currently mainnet-only."""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import AAVE_V4_EVENTS, async_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aave_v4_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
TICK_CONCURRENCY = 1


def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds, *, chain, event, since, until) -> int:
    method_name, table, columns, transform = AAVE_V4_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay: await asyncio.sleep(delay)
        try:
            # V4 builder takes optional token; passing none returns all reserves.
            b = getattr(ds.evm.aave_v4, method_name)()
            b = b.network(chain).time_range(_iso(since), _iso(until)).verbose().with_value()
            df = await b.as_df("polars")
            if df.is_empty(): return 0
            rows = transform(df, chain=chain)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            m = str(exc).lower()
            if "429" not in m and "too many" not in m and "rate limit" not in m: raise
    raise last_exc


async def main():
    if not config.DEFISTREAM_API_KEY: log.error("DEFISTREAM_API_KEY not set"); sys.exit(2)
    if not config.AAVE_V4_EVENTS_ENABLED:
        log.info("AAVE_V4_EVENTS_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    chains = config.AAVE_V4_CHAINS
    if not chains:
        log.info("no AAVE_V4_CHAINS configured; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, ev) for c in chains for ev in AAVE_V4_EVENTS]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    log.info("polling aave_v4 chains=%s -> %d calls/tick (every %ss, overlap=%dm)",
             chains, len(calls), POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)
    while True:
        tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
        async def _one(chain, ev):
            async with sem:
                try:
                    n = await fetch_and_insert(ds, chain=chain, event=ev, since=since, until=now)
                    log.info("%s/%s rows=%d", chain, ev, n)
                except Exception as exc:
                    log.exception("%s/%s fetch failed: %s", chain, ev, exc)
        await asyncio.gather(*(_one(*c) for c in calls))
        await asyncio.sleep(max(0.0, tick_end - time.monotonic()))


if __name__ == "__main__":
    asyncio.run(main())
