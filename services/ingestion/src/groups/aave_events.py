"""Live polling for AAVE v3 events.

For each configured chain we iterate the 6 event types (deposit / withdraw /
borrow / repay / flashloan / liquidation) and fire one DeFiStream call per
(chain, eth_market, event) combination on every tick. eth_market only
applies to ETH — on other chains we issue a single call per event with
eth_market=''.

Configuration env vars:
  AAVE_EVENTS_CHAINS  : CSV of chains (e.g. ETH,ARB,BASE,BSC,POLYGON).
                        Empty list → group idles (default).
  AAVE_ETH_MARKETS    : CSV of ETH market types (Core, Prime, EtherFi).
                        Each runs as a separate DeFiStream call so the
                        rows are tagged with the right market.
  AAVE_EVENTS_ENABLED : "1" (default) — set "0" to keep the group dormant
                        while still leaving chains configured for the
                        backfill endpoint.

Per-tick calls = len(chains - {ETH}) × 6  +  has_ETH × len(markets) × 6.
For the default 5 EVMs + 3 ETH markets that's 24 + 18 = 42 calls/min.
We fire them with `asyncio.gather` so the tick takes ~as long as one
serial call instead of stacking 42 in a row.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
from clickhouse import AAVE_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aave_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
# Cap concurrent DeFiStream HTTP calls per tick. DeFiStream's per-second
# limit is fairly tight (seen ~50 req/min upper bound during the AAVE
# backfill) and live polling shares budget with any running backfill, so
# we go serial here. Each tick takes roughly 42 × call_latency seconds,
# which may overrun the 60s budget — that's fine; the overlap of
# POLL_OVERLAP_MINUTES * 60 = 180s catches up next tick.
TICK_CONCURRENCY = 1


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan_calls(chains: list[str], eth_markets: list[str]) -> list[tuple[str, str, str]]:
    """Cross-product (chain, eth_market, event) — eth_market='' for non-ETH."""
    calls: list[tuple[str, str, str]] = []
    for chain in chains:
        if chain.upper() == "ETH":
            for market in eth_markets:
                for event in AAVE_EVENTS:
                    calls.append((chain, market, event))
        else:
            for event in AAVE_EVENTS:
                calls.append((chain, "", event))
    return calls


async def fetch_and_insert(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    eth_market: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = AAVE_EVENTS[event]
    builder = getattr(ds.evm.aave_v3, method_name)()
    builder = builder.network(chain).time_range(_iso(since), _iso(until))
    builder = builder.verbose().with_value()
    if eth_market:
        builder = builder.eth_market_type(eth_market)
    df = await builder.as_df("polars")
    if df.is_empty():
        return 0
    rows = transform(df, chain=chain, eth_market=eth_market)
    ch = await async_client()
    await ch.insert(table, rows, column_names=columns)
    return len(rows)


async def _run(events_filter: list[str] | None = None, stream_name: str | None = None):
    """Run live polling + gap-fill for AAVE v3.

    When `events_filter` is set (per-event stream worker), only that event's
    (chain × market) calls are made. When None, the legacy behavior runs
    every event across every chain/market. `stream_name` enables tick writes
    to ingestion_event_status — used by per-event stream workers.
    """
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.AAVE_EVENTS_ENABLED:
        log.info("AAVE_EVENTS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)
    chains = config.AAVE_EVENTS_CHAINS
    if not chains:
        log.info("no AAVE_EVENTS_CHAINS configured; idling")
        while True:
            await asyncio.sleep(3600)

    calls = _plan_calls(chains, config.AAVE_ETH_MARKETS)
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[2] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling aave_v3 chains=%s eth_markets=%s events_filter=%s -> %d calls/tick every %ss",
        chains, config.AAVE_ETH_MARKETS, events_filter, len(calls),
        POLL_INTERVAL_SECONDS
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
            total_rows = 0
            err: str | None = None

            async def _one(chain, market, event):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, eth_market=market, event=event,
                            since=since, until=now,
                        )
                        total_rows += n
                        label = f"{chain}" + (f"/{market}" if market else "") + f"/{event}"
                        log.info("%s rows=%d", label, n)
                    except Exception as exc:
                        label = f"{chain}" + (f"/{market}" if market else "") + f"/{event}"
                        err = f"{label}: {type(exc).__name__}: {exc}"[:1000]
                        log.exception("%s fetch failed: %s", label, exc)

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
            async def _one(chain, market, event):
                _method, table, _cols, _tf = AAVE_EVENTS[event]
                last_seen = await latest_time(
                    ch, table=table,
                    where="chain = {chain:String} AND eth_market = {market:String}",
                    parameters={"chain": chain, "market": market},
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
                label = "aave_events/" + chain + (f"/{market}" if market else "") + f"/{event}"
                log.info("%s sweep window=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
                async def call(s, u):
                    async with sem:
                        return await fetch_and_insert(
                            ds, chain=chain, eth_market=market, event=event, since=s, until=u,
                        )
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
