"""Live polling for Uniswap V3 events.

For each configured pool (chain, symbol0, symbol1, fee_tier) iterate the 4
event types (swap / deposit / withdraw / collect) and fire one DeFiStream
call per (pool, event) combination on every tick.

Configuration env vars (see config.py):
  UNI_V3_POOLS    — semicolon-separated `<chain>:<sym0/sym1/fee>,...` groups
                    default seeds ~50 pools across the 5 EVMs we cover.
  UNI_V3_ENABLED  — "1" (default); "0" keeps the group dormant but leaves
                    the pool list intact for the backfill endpoint.

Throttling matches the AAVE group: TICK_CONCURRENCY=1 (serial) because
DeFiStream's rate limit is tight and lives on a shared budget with the
AAVE polling + any active backfill. POLL_OVERLAP_MINUTES=3 catches up if
a tick runs long.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import UNISWAP_EVENTS, async_client
from gap_fill import latest_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [uniswap_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
TICK_CONCURRENCY = 1


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan_calls(pools: list[tuple[str, str, str, int]]) -> list[tuple[str, str, str, int, str]]:
    """Cross-product (pool, event) — one call per combination."""
    calls: list[tuple[str, str, str, int, str]] = []
    for chain, sym0, sym1, fee in pools:
        for event in UNISWAP_EVENTS:
            calls.append((chain, sym0, sym1, fee, event))
    return calls


async def fetch_and_insert(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    symbol0: str,
    symbol1: str,
    fee_tier: int,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = UNISWAP_EVENTS[event]
    # 429-aware: retry once with a small backoff so a transient burst doesn't
    # take out an entire tick. We don't loop further here — if the rate
    # budget is genuinely full, the next tick will catch up via the overlap.
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.uniswap_v3, method_name)(symbol0, symbol1, fee_tier)
            builder = builder.network(chain).time_range(_iso(since), _iso(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain, symbol0=symbol0, symbol1=symbol1, fee_tier=fee_tier)
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
    if not config.UNI_V3_ENABLED:
        log.info("UNI_V3_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)
    # Live polling uses the trimmed UNI_V3_LIVE_POOLS set — the full
    # UNI_V3_POOLS list is reserved for backfill (which paces itself).
    # Falls back to UNI_V3_POOLS if the live set is unset.
    pools = config.UNI_V3_LIVE_POOLS or config.UNI_V3_POOLS
    if not pools:
        log.info("no UNI_V3_POOLS configured; idling")
        while True:
            await asyncio.sleep(3600)

    calls = _plan_calls(pools)
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info(
        "uniswap_v3 pools=%d -> %d calls/tick (concurrency=%d); live cadence=%ss, sweep cadence=%ss",
        len(pools), len(calls), TICK_CONCURRENCY, POLL_INTERVAL_SECONDS, sweep_cadence,
    )

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
            total_rows = 0
            err: str | None = None

            async def _one(chain, sym0, sym1, fee, event):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, symbol0=sym0, symbol1=sym1, fee_tier=fee,
                            event=event, since=since, until=now,
                        )
                        total_rows += n
                        log.info("%s/%s/%s/%d/%s rows=%d", chain, sym0, sym1, fee, event, n)
                    except Exception as exc:
                        err = f"{chain}/{sym0}/{sym1}/{fee}/{event}: {type(exc).__name__}: {exc}"[:1000]
                        log.exception(
                            "%s/%s/%s/%d/%s fetch failed: %s",
                            chain, sym0, sym1, fee, event, exc,
                        )

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

            async def _one(chain, sym0, sym1, fee, event):
                _method, table, _cols, _tf = UNISWAP_EVENTS[event]
                try:
                    last_seen = await latest_time(
                        ch, table=table,
                        where="chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String} AND fee_tier = {fee:UInt32}",
                        parameters={"chain": chain, "s0": sym0, "s1": sym1, "fee": fee},
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
                    if since >= now:
                        return
                    label = f"uniswap_events/{chain}/{sym0}-{sym1}/{fee}/{event}"
                    async with sem:
                        n = await fetch_and_insert(
                            ds, chain=chain, symbol0=sym0, symbol1=sym1, fee_tier=fee,
                            event=event, since=since, until=now,
                        )
                    log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)", label, since, now, n, last_seen)
                except Exception as exc:
                    log.exception("sweep failed for %s/%s/%s/%d/%s: %s", chain, sym0, sym1, fee, event, exc)

            await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())


async def main():
    await _run()


if __name__ == "__main__":
    asyncio.run(main())
