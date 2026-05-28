"""Live polling for AAVE v2 events.

V2 only has the single legacy pool (no eth_market axis like V3), and
DeFiStream's runtime only configures ETH + POLYGON for the protocol —
configured chains beyond that just return "not configured" errors. We
fire one DeFiStream call per (chain, event) on every 60s tick.

Per-tick calls = len(AAVE_V2_CHAINS) × 6 events. For the default 2
chains that's 12 calls/min — comfortably under the shared rate budget
alongside AAVE v3 / Uniswap / Lido pollers.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import AAVE_V2_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aave_v2_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
TICK_CONCURRENCY = 1


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = AAVE_V2_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.aave_v2, method_name)()
            builder = builder.network(chain).time_range(_iso(since), _iso(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain)
            ch = await async_client()
            await ch.insert(table, rows, column_names=columns)
            return len(rows)
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "429" not in msg and "too many" not in msg and "rate limit" not in msg:
                raise
    assert last_exc is not None
    raise last_exc


async def main():
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.AAVE_V2_EVENTS_ENABLED:
        log.info("AAVE_V2_EVENTS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)
    chains = config.AAVE_V2_CHAINS
    if not chains:
        log.info("no AAVE_V2_CHAINS configured; idling")
        while True:
            await asyncio.sleep(3600)

    calls = [(chain, event) for chain in chains for event in AAVE_V2_EVENTS]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling aave_v2 chains=%s -> %d calls/tick (concurrency=%d) every %ss (overlap=%dm) + gap-fill from watermark",
        chains, len(calls), TICK_CONCURRENCY, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES,
    )

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)

            async def _one(chain, event):
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, event=event, since=since, until=now,
                        )
                        log.info("%s/%s rows=%d", chain, event, n)
                    except Exception as exc:
                        log.exception("%s/%s fetch failed: %s", chain, event, exc)

            await asyncio.gather(*(_one(*c) for c in calls))
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, event):
            _method, table, _cols, _tf = AAVE_V2_EVENTS[event]
            last_seen = await latest_time(
                ch, table=table,
                where="chain = {chain:String}",
                parameters={"chain": chain},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start:
                return
            label = f"aave_v2_events/{chain}/{event}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                async with sem:
                    return await fetch_and_insert(ds, chain=chain, event=event, since=s, until=u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
