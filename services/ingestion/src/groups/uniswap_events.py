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

import config
from clickhouse import UNISWAP_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [uniswap_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
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


async def main():
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
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling uniswap_v3 pools=%d -> %d calls/tick (concurrency=%d) every %ss (overlap=%dm) + gap-fill from watermark",
        len(pools), len(calls), TICK_CONCURRENCY, POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES,
    )

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)

            async def _one(chain, sym0, sym1, fee, event):
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, symbol0=sym0, symbol1=sym1, fee_tier=fee,
                            event=event, since=since, until=now,
                        )
                        log.info("%s/%s/%s/%d/%s rows=%d", chain, sym0, sym1, fee, event, n)
                    except Exception as exc:
                        log.exception(
                            "%s/%s/%s/%d/%s fetch failed: %s",
                            chain, sym0, sym1, fee, event, exc,
                        )

            await asyncio.gather(*(_one(*c) for c in calls))
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, sym0, sym1, fee, event):
            _method, table, _cols, _tf = UNISWAP_EVENTS[event]
            last_seen = await latest_time(
                ch, table=table,
                where="chain = {chain:String} AND symbol0 = {s0:String} AND symbol1 = {s1:String} AND fee_tier = {fee:UInt32}",
                parameters={"chain": chain, "s0": sym0, "s1": sym1, "fee": fee},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start: return
            label = f"uniswap_events/{chain}/{sym0}-{sym1}/{fee}/{event}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                async with sem:
                    return await fetch_and_insert(ds, chain=chain, symbol0=sym0, symbol1=sym1,
                                                  fee_tier=fee, event=event, since=s, until=u)
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
