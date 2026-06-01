"""Live polling for Morpho events. 7 events × N chains. ETH+BASE only."""
import asyncio, logging, sys, time
from datetime import datetime, timezone

from defistream import AsyncDeFiStream
import ch_status
import config
import sweep
from clickhouse import MORPHO_EVENTS, async_client
from gap_fill import latest_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [morpho_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
TICK_CONCURRENCY = 1


def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds, *, chain, event, since, until) -> int:
    method_name, table, columns, transform = MORPHO_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay: await asyncio.sleep(delay)
        try:
            b = getattr(ds.evm.morpho, method_name)()
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


async def live_loop(ds, calls, sem, stream_name: str | None = None):
    jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
    log.info("live_loop: waiting %.0fs before first fire", jitter)
    await asyncio.sleep(jitter)
    while True:
        tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now - sweep.LIVE_OVERLAP
        total_rows = 0
        err: str | None = None
        async def _one(chain, ev):
            nonlocal total_rows, err
            async with sem:
                try:
                    n = await fetch_and_insert(ds, chain=chain, event=ev, since=since, until=now)
                    total_rows += n
                    log.info("%s/%s rows=%d", chain, ev, n)
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"[:1000]
                    log.exception("%s/%s fetch failed: %s", chain, ev, exc)
        if stream_name:
            await ch_status.write_tick_start(stream_name)
        await asyncio.gather(*(_one(*c) for c in calls))
        if stream_name:
            await ch_status.write_tick(stream_name, total_rows, error=err)
        await asyncio.sleep(max(0.0, tick_end - time.monotonic()))


async def sweep_loop(ds, calls, sem, sweep_cadence: float):
    """Periodic sweep — fetches a [sweep_since, now] window per (chain, event)
    every sweep_cadence seconds. First fire is jittered 5-10 min so a fleet
    of workers spawning together don't all sweep at once."""
    jitter = sweep.sweep_jitter_s(sweep_cadence)
    log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
    await asyncio.sleep(jitter)
    ch = await async_client()
    while True:
        next_fire = time.monotonic() + sweep_cadence
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async def _one(chain, event):
            _method, table, _cols, _tf = MORPHO_EVENTS[event]
            try:
                last_seen = await latest_time(
                    ch, table=table,
                    where="chain = {chain:String}",
                    parameters={"chain": chain},
                )
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since >= now:
                    return
                label = f"morpho_events/{chain}/{event}"
                async with sem:
                    n = await fetch_and_insert(ds, chain=chain, event=event, since=since, until=now)
                log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)", label, since, now, n, last_seen)
            except Exception as exc:
                log.exception("sweep failed for %s/%s: %s", chain, event, exc)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)
        await asyncio.sleep(max(0.0, next_fire - time.monotonic()))


async def _run(events_filter: list[str] | None = None, stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY: log.error("DEFISTREAM_API_KEY not set"); sys.exit(2)
    if not config.MORPHO_EVENTS_ENABLED:
        log.info("MORPHO_EVENTS_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    chains = config.MORPHO_CHAINS
    if not chains:
        log.info("no MORPHO_CHAINS; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, ev) for c in chains for ev in MORPHO_EVENTS]
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info("polling morpho chains=%s -> %d calls/tick; live=%ss sweep=%ss",
             chains, len(calls), POLL_INTERVAL_SECONDS, sweep_cadence)
    await asyncio.gather(
        live_loop(ds, calls, sem, stream_name=stream_name),
        sweep_loop(ds, calls, sem, sweep_cadence),
    )


async def main():
    await _run()


if __name__ == "__main__":
    asyncio.run(main())
