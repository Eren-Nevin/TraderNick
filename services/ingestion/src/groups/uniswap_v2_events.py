"""Live polling for Uniswap V2 events.

Same shape as the V3 poller minus the fee_tier axis (V2 has a fixed
0.30% pool fee, no tier choice) and minus the collect event (V2 LP
fees auto-compound into the pool token, never emitting a separate
collect log).

Per-tick calls = len(UNI_V2_LIVE_POOLS) × 3 events. The default live
list is 3 pools so 9 calls/min, very small. Backfill uses the full
UNI_V2_POOLS catalogue.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import UNISWAP_V2_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [uniswap_v2_events] %(levelname)s %(message)s")
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
    symbol0: str,
    symbol1: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = UNISWAP_V2_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.uniswap_v2, method_name)(symbol0, symbol1)
            builder = builder.network(chain).time_range(_iso(since), _iso(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain, symbol0=symbol0, symbol1=symbol1)
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
    if not config.UNI_V2_ENABLED:
        log.info("UNI_V2_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)
    pools = config.UNI_V2_LIVE_POOLS or config.UNI_V2_POOLS
    if not pools:
        log.info("no UNI_V2_POOLS configured; idling")
        while True:
            await asyncio.sleep(3600)

    calls: list[tuple[str, str, str, str]] = []
    for chain, sym0, sym1 in pools:
        for event in UNISWAP_V2_EVENTS:
            calls.append((chain, sym0, sym1, event))

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling uniswap_v2 pools=%d -> %d calls/tick (concurrency=%d) every %ss (overlap=%dm) + gap-fill from watermark",
        len(pools), len(calls), TICK_CONCURRENCY, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES,
    )

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)

            async def _one(chain, sym0, sym1, event):
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, symbol0=sym0, symbol1=sym1, event=event,
                            since=since, until=now,
                        )
                        log.info("%s/%s/%s/%s rows=%d", chain, sym0, sym1, event, n)
                    except Exception as exc:
                        log.exception("%s/%s/%s/%s fetch failed: %s", chain, sym0, sym1, event, exc)

            await asyncio.gather(*(_one(*c) for c in calls))
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, sym0, sym1, event):
            _method, table, _cols, _tf = UNISWAP_V2_EVENTS[event]
            last_seen = await latest_time(
                ch, table=table,
                where="chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String}",
                parameters={"chain": chain, "s0": sym0, "s1": sym1},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"uniswap_v2_events/{chain}/{sym0}-{sym1}/{event}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                async with sem:
                    return await fetch_and_insert(ds, chain=chain, symbol0=sym0, symbol1=sym1,
                                                  event=event, since=s, until=u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
