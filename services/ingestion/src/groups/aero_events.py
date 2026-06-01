"""Live polling for Aerodrome concentrated-pool events (BASE only).

Pool identity: (chain=BASE, sym0, sym1, tick_spacing). The DeFiStream
builder accepts pool_type as its first positional arg — we always pass
'concentrated' since V1 covers only that family (basic pools' stable
flag and the claims event are currently broken server-side).
"""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
from clickhouse import AERO_CL_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aero_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
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


async def _run(events_filter: list[str] | None = None, stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY: log.error("DEFISTREAM_API_KEY not set"); sys.exit(2)
    if not config.AERO_ENABLED:
        log.info("AERO_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    pools = config.AERO_LIVE_POOLS or config.AERO_POOLS
    if not pools:
        log.info("no AERO_POOLS; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, s0, s1, ts, ev) for (c, s0, s1, ts) in pools for ev in AERO_CL_EVENTS]
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info("polling aero pools=%d -> %d calls/tick (every %ss, overlap=%dm) + gap-fill from watermark",
             len(pools), len(calls), POLL_INTERVAL_SECONDS
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
            async def _one(chain, s0, s1, ts, ev):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                   tick_spacing=ts, event=ev, since=since, until=now)
                        total_rows += n
                        log.info("%s/%s/%s/%d/%s rows=%d", chain, s0, s1, ts, ev, n)
                    except Exception as exc:
                        err = f"{type(exc).__name__}: {exc}"[:1000]
                        log.exception("%s/%s/%s/%d/%s fetch failed: %s", chain, s0, s1, ts, ev, exc)
            total_rows = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            await asyncio.gather(*(_one(*c) for c in calls))
            if stream_name:
                await ch_status.write_tick(stream_name, total_rows, error=err, duration_s=time.monotonic()-_live_t0)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop():
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
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since >= now: return
                label = f"aero_events/{chain}/{s0}-{s1}/{ts}/{event}"
                log.info("%s sweep window=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
                async def call(s, u):
                    async with sem:
                        return await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                      tick_spacing=ts, event=event, since=s, until=u)
                total = await call(since, now)
                log.info("%s sweep done rows=%d", label, total)
            await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())


async def main():
    await _run()


if __name__ == "__main__":
    asyncio.run(main())
