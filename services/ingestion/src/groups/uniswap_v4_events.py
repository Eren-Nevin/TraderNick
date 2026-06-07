"""Live polling for Uniswap V4 events.

V4 pools are identified by the 6-tuple (chain, sym0, sym1, fee,
tick_spacing, hooks). Per-tick calls = len(UNI_V4_LIVE_POOLS) × 4
events (swap / deposit / withdraw / initialize).
"""
import asyncio, logging, sys, time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
from clickhouse import UNISWAP_V4_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [uniswap_v4_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
TICK_CONCURRENCY = 1


def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds, *, chain, symbol0, symbol1, fee, tick_spacing, hooks, event, since, until) -> int:
    method_name, table, columns, transform = UNISWAP_V4_EVENTS[event]
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay: await asyncio.sleep(delay)
        try:
            b = getattr(ds.evm.uniswap_v4, method_name)(symbol0, symbol1, fee=fee, tick_spacing=tick_spacing, hooks=hooks)
            b = b.network(chain).time_range(_iso(since), _iso(until)).verbose().with_value()
            df = await b.as_df("polars")
            if df.is_empty(): return 0
            rows = transform(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                            fee=fee, tick_spacing=tick_spacing, hooks=hooks)
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
    if not config.UNI_V4_ENABLED:
        log.info("UNI_V4_ENABLED=0; idling")
        while True: await asyncio.sleep(3600)
    pools = config.UNI_V4_LIVE_POOLS or config.UNI_V4_POOLS
    if not pools:
        log.info("no UNI_V4_POOLS; idling")
        while True: await asyncio.sleep(3600)
    calls = [(c, s0, s1, fee, ts, hk, ev)
             for (c, s0, s1, fee, ts, hk) in pools
             for ev in UNISWAP_V4_EVENTS]
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info("uniswap_v4 pools=%d -> %d calls/tick; live cadence=%ss, sweep cadence=%ss",
             len(pools), len(calls), POLL_INTERVAL_SECONDS, sweep_cadence)
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
            async def _one(chain, s0, s1, fee, ts, hk, ev):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                   fee=fee, tick_spacing=ts, hooks=hk,
                                                   event=ev, since=since, until=now)
                        total_rows += n
                        log.info("%s/%s/%s/%d/%d/%s rows=%d", chain, s0, s1, fee, ts, ev, n)
                    except Exception as exc:
                        err = f"{type(exc).__name__}: {exc}"[:1000]
                        log.exception("%s/%s/%s/%d/%d/%s fetch failed: %s", chain, s0, s1, fee, ts, ev, exc)
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
            async def _one(chain, s0, s1, fee, ts, hk, event):
                _method, table, _cols, _tf = UNISWAP_V4_EVENTS[event]
                # V4 pool identity is the full 6-tuple. ORDER BY drops `hooks`
                # in the schema (always 0x0 in V1), but we keep it in the WHERE
                # anyway so hook-bearing pools stay separated correctly.
                last_seen = await latest_time(
                    ch, table=table,
                    where=("chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String} "
                           "AND fee = {fee:UInt32} AND tick_spacing = {ts:UInt32} AND hooks = {hk:String}"),
                    parameters={"chain": chain, "s0": s0, "s1": s1,
                                "fee": fee, "ts": ts, "hk": hk},
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
                label = f"uniswap_v4_events/{chain}/{s0}-{s1}/{fee}/{ts}/{event}"
                log.info("%s sweep window=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
                async def call(s, u):
                    async with sem:
                        return await fetch_and_insert(ds, chain=chain, symbol0=s0, symbol1=s1,
                                                      fee=fee, tick_spacing=ts, hooks=hk,
                                                      event=event, since=s, until=u)
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
