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

import ch_status
import config
from clickhouse import UNISWAP_V2_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [uniswap_v2_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
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


async def _run(events_filter: list[str] | None = None, stream_name: str | None = None):
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
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    for chain, sym0, sym1 in pools:
        for event in UNISWAP_V2_EVENTS:
            calls.append((chain, sym0, sym1, event))

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling uniswap_v2 pools=%d -> %d calls/tick (concurrency=%d) every %ss + gap-fill from watermark",
        len(pools), len(calls), TICK_CONCURRENCY, POLL_INTERVAL_SECONDS
,
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

            async def _one(chain, sym0, sym1, event):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, symbol0=sym0, symbol1=sym1, event=event,
                            since=since, until=now,
                        )
                        total_rows += n
                        log.info("%s/%s/%s/%s rows=%d", chain, sym0, sym1, event, n)
                    except Exception as exc:
                        err = f"{type(exc).__name__}: {exc}"[:1000]
                        log.exception("%s/%s/%s/%s fetch failed: %s", chain, sym0, sym1, event, exc)

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
            async def _one(chain, sym0, sym1, event):
                _method, table, _cols, _tf = UNISWAP_V2_EVENTS[event]
                last_seen = await latest_time(
                    ch, table=table,
                    where="chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String}",
                    parameters={"chain": chain, "s0": sym0, "s1": sym1},
                )
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since >= now: return
                label = f"uniswap_v2_events/{chain}/{sym0}-{sym1}/{event}"
                log.info("%s sweep window=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
                async def call(s, u):
                    async with sem:
                        return await fetch_and_insert(ds, chain=chain, symbol0=sym0, symbol1=sym1,
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
