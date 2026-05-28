"""Live polling for Aerodrome concentrated-pool events (BASE only).

Pool identity: (chain=BASE, sym0, sym1, tick_spacing). The DeFiStream
builder accepts pool_type as its first positional arg — we always pass
'concentrated' since V1 covers only that family (basic pools' stable
flag and the claims event are currently broken server-side).
"""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import config
from clickhouse import AERO_CL_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aero_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
TICK_CONCURRENCY = 1


def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds, *, chain, symbol0, symbol1, tick_spacing, event, since, until) -> int:
    method_name, table, columns, transform = AERO_CL_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay: await asyncio.sleep(delay)
        try:
            b = getattr(ds.evm.aero, method_name)("concentrated", symbol0, symbol1, tick_spacing=tick_spacing)
            b = b.network(chain).time_range(_iso(since), _iso(until)).verbose().with_value()
            df = await b.as_df("polars")
            if df.is_empty(): return 0
            rows = transform(df, chain=chain, symbol0=symbol0, symbol1=symbol1, tick_spacing=tick_spacing)
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
    if not config.AERO_ENABLED:
        log.info("AERO_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    pools = config.AERO_LIVE_POOLS or config.AERO_POOLS
    if not pools:
        log.info("no AERO_POOLS; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, s0, s1, ts, ev) for (c, s0, s1, ts) in pools for ev in AERO_CL_EVENTS]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling aero pools=%d -> %d calls/tick (every %ss, overlap=%dm) + gap-fill from watermark",
             len(pools), len(calls), POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)
            async def _one(chain, s0, s1, ts, ev):
                async with sem:
                    try:
                        n = await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                   tick_spacing=ts, event=ev, since=since, until=now)
                        log.info("%s/%s/%s/%d/%s rows=%d", chain, s0, s1, ts, ev, n)
                    except Exception as exc:
                        log.exception("%s/%s/%s/%d/%s fetch failed: %s", chain, s0, s1, ts, ev, exc)
            await asyncio.gather(*(_one(*c) for c in calls))
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, s0, s1, ts, event):
            _method, table, _cols, _tf = AERO_CL_EVENTS[event]
            # Pool identity is (chain, symbol0, symbol1, tick_spacing) — each
            # pool has its own watermark since rare pools may sit idle for
            # days while busy pools tick every minute.
            last_seen = await latest_time(
                ch, table=table,
                where="chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String} AND tick_spacing = {ts:UInt32}",
                parameters={"chain": chain, "s0": s0, "s1": s1, "ts": ts},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"aero_events/{chain}/{s0}-{s1}/{ts}/{event}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                async with sem:
                    return await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                  tick_spacing=ts, event=event, since=s, until=u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
