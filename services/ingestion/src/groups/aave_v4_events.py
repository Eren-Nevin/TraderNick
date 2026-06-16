"""Live polling for AAVE v4 events (ETH only). 5 events × N chains.
Default chain set = ['ETH'] since V4 is currently mainnet-only."""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
from clickhouse import AAVE_V4_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aave_v4_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
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


async def _run(events_filter: list[str] | None = None, stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY: log.error("DEFISTREAM_API_KEY not set"); sys.exit(2)
    if not config.AAVE_V4_EVENTS_ENABLED:
        log.info("AAVE_V4_EVENTS_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    chains = config.AAVE_V4_CHAINS
    if not chains:
        log.info("no AAVE_V4_CHAINS configured; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, ev) for c in chains for ev in AAVE_V4_EVENTS]
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling aave_v4 chains=%s -> %d calls/tick (every %ss, overlap=%dm) + gap-fill from watermark",
             chains, len(calls), POLL_INTERVAL_SECONDS
)

    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            since = now - sweep.LIVE_OVERLAP
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
            total_rows = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            await asyncio.gather(*(_one(*c) for c in calls))
            if stream_name:
                await ch_status.write_tick(stream_name, total_rows, error=err, duration_s=time.monotonic()-_live_t0)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop(once: bool = False):
        if not once:
            jitter = sweep.sweep_jitter_s(sweep_cadence)
            log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
            await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            async def _one(chain, event):
                _method, table, _cols, _tf = AAVE_V4_EVENTS[event]
                last_seen = await latest_time(
                    ch, table=table,
                    where="chain = {chain:String}",
                    parameters={"chain": chain},
                )
                since = sweep.sweep_since(
                    now=now,
                    sweep_cadence_seconds=sweep_cadence,
                    last_seen=last_seen,
                    # DeFiStream EVM parquet event endpoints cap each request
                    # at 7 days (100k blocks). Leave 1 day of slack so the
                    # 5-min live overlap + clock skew can never push us over.
                    max_window_seconds=6 * 24 * 3600,
                    stream_name=stream_name,
                )
                if since >= now: return
                label = f"aave_v4_events/{chain}/{event}"
                log.info("%s sweep window=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
                async def call(s, u):
                    async with sem:
                        return await fetch_and_insert(ds, chain=chain, event=event, since=s, until=u)
                total = await call(since, now)
                log.info("%s sweep done rows=%d", label, total)
            await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            if once:
                return
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    # Boot-sweep — run one sweep iteration to completion BEFORE the live
    # loop starts, so a restart after a long stop recovers the full
    # [last_seen, now] gap instead of live_loop advancing the watermark
    # past it (mirrors streams/_hl_common.py).
    log.info("boot-sweep: recovering pre-restart gap before live loop starts")
    await sweep_loop(once=True)
    await asyncio.gather(live_loop(), sweep_loop())


async def main():
    await _run()


if __name__ == "__main__":
    asyncio.run(main())
