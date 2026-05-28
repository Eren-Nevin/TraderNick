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

import config
from clickhouse import AAVE_EVENTS, async_client
from gap_fill import latest_time, resolve_since, run_chunked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aave_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
POLL_OVERLAP_MINUTES = 3
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


async def main():
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
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling aave_v3 chains=%s eth_markets=%s -> %d calls/tick (concurrency=%d) every %ss (overlap=%dm) + gap-fill from watermark",
        chains, config.AAVE_ETH_MARKETS, len(calls), TICK_CONCURRENCY,
        POLL_INTERVAL_SECONDS, POLL_OVERLAP_MINUTES,
    )

    async def live_loop():
        while True:
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - timedelta(minutes=POLL_OVERLAP_MINUTES)

            async def _one(chain, market, event):
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, eth_market=market, event=event,
                            since=since, until=now,
                        )
                        label = f"{chain}" + (f"/{market}" if market else "") + f"/{event}"
                        log.info("%s rows=%d", label, n)
                    except Exception as exc:
                        label = f"{chain}" + (f"/{market}" if market else "") + f"/{event}"
                        log.exception("%s fetch failed: %s", label, exc)

            await asyncio.gather(*(_one(*c) for c in calls))
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def gap_fill_task():
        ch = await async_client()
        async def _one(chain, market, event):
            _method, table, _cols, _tf = AAVE_EVENTS[event]
            last_seen = await latest_time(
                ch, table=table,
                where="chain = {chain:String} AND eth_market = {market:String}",
                parameters={"chain": chain, "market": market},
            )
            since = resolve_since(last_seen, t_start=t_start)
            if since >= t_start:
                return
            label = "aave_events/" + chain + (f"/{market}" if market else "") + f"/{event}"
            log.info("%s gap-fill since=%s until=%s (last_seen=%s)", label, since, t_start, last_seen)
            async def call(s, u):
                async with sem:
                    return await fetch_and_insert(
                        ds, chain=chain, eth_market=market, event=event, since=s, until=u,
                    )
            total = await run_chunked(label=label, since=since, until=t_start, call=call)
            log.info("%s gap-fill done total_rows=%d", label, total)
        await asyncio.gather(*(_one(*c) for c in calls), return_exceptions=True)

    await asyncio.gather(live_loop(), gap_fill_task())


if __name__ == "__main__":
    asyncio.run(main())
