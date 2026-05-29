"""Live polling for GMX V2 events. 9 events × 1 chain (ARB-only).

GMX V2 returns ALL markets in a single network() call — no per-market
filter on the builder. So one call per (chain, event) per tick covers
every market. ~9 calls/tick on ARB."""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream
import config
from clickhouse import GMX_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [gmx_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
TICK_CONCURRENCY = 1


def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds, *, chain, event, since, until) -> int:
    method_name, table, columns, transform = GMX_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay: await asyncio.sleep(delay)
        try:
            b = getattr(ds.evm.gmx_v2, method_name)()
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


async def live_loop(ds, calls, sem):
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


async def gap_fill_task(ds, chains, sem, t_start):
    """Watermark is per (chain, event) — every market is summed into the
    same table so the per-market dimension isn't part of the watermark.
    Mirrors the morpho pattern."""
    ch = await async_client()
    async def _one(chain, event):
        _method, table, _cols, _tf = GMX_EVENTS[event]
        last_seen = await latest_time(
            ch, table=table,
            where="chain = {chain:String}",
            parameters={"chain": chain},
        )
        since = resolve_since(last_seen, t_start=t_start)
        if since >= t_start: return
        label = f"gmx_events/{chain}/{event}"
        log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
        async def call(s, u):
            async with sem:
                return await fetch_and_insert(ds, chain=chain, event=event, since=s, until=u)
        total = await run_chunked(label=label, since=since, until=t_start, call=call)
        log.info("%s gap-fill done total_rows=%d", label, total)
    await asyncio.gather(
        *(_one(c, ev) for c in chains for ev in GMX_EVENTS),
        return_exceptions=True,
    )


async def main():
    if not config.DEFISTREAM_API_KEY: log.error("DEFISTREAM_API_KEY not set"); sys.exit(2)
    if not config.GMX_EVENTS_ENABLED:
        log.info("GMX_EVENTS_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    chains = config.GMX_CHAINS
    if not chains:
        log.info("no GMX_CHAINS; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, ev) for c in chains for ev in GMX_EVENTS]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling gmx chains=%s -> %d calls/tick (every %ss, overlap=%dm) + gap-fill from watermark",
             chains, len(calls), POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES)
    await asyncio.gather(
        live_loop(ds, calls, sem),
        gap_fill_task(ds, chains, sem, t_start),
    )


if __name__ == "__main__":
    asyncio.run(main())
