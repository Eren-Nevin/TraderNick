"""Live polling for Lido events.

Two flow shapes share the same per-event ingest loop:

  - ETH mainnet (3 events): deposit / withdrawal_request / withdrawal_claimed —
    the staking + unstake-queue state machine on the canonical Lido contract.
  - L2 bridge (2 events × N L2 chains): l2_deposit (mainnet stETH → L2 wstETH)
    and l2_withdrawal_request (burning bridged wstETH to redeem on mainnet).

Configuration env vars (see config.py):
  LIDO_EVENTS_ENABLED  = "1"          — "0" idles the group without losing
                                         config (backfill endpoint still works).
  LIDO_ETH_EVENTS      = csv          — events fired against ETH (default: all
                                         three mainnet types).
  LIDO_L2_EVENTS       = csv          — events fired per L2 chain (default:
                                         both bridge events).
  LIDO_L2_CHAINS       = csv          — list of L2 chains to poll (default
                                         9 chains DeFiStream supports today).

Per-tick calls = len(LIDO_ETH_EVENTS) + len(LIDO_L2_CHAINS) × len(LIDO_L2_EVENTS).
For the default config that's 3 + 9 × 2 = 21 calls/min. Same shared-rate-
budget pattern as the AAVE and Uniswap pollers — TICK_CONCURRENCY=1 keeps
us serial so a backfill running in parallel doesn't starve.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
from clickhouse import LIDO_EVENTS, async_client
from gap_fill import latest_time
import sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s [lido_events] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
TICK_CONCURRENCY = 1


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan_calls(eth_events: list[str], l2_chains: list[str], l2_events: list[str]) -> list[tuple[str, str]]:
    """Build the (chain, event) call list — ETH for L1 events, then each
    L2 chain × L2 event."""
    calls: list[tuple[str, str]] = []
    for ev in eth_events:
        calls.append(("ETH", ev))
    for chain in l2_chains:
        for ev in l2_events:
            calls.append((chain, ev))
    return calls


async def fetch_and_insert(
    ds: AsyncDeFiStream,
    *,
    chain: str,
    event: str,
    since: datetime,
    until: datetime,
) -> int:
    method_name, table, columns, transform = LIDO_EVENTS[event]
    # 429-aware: a single short backoff retry mirrors what we do in the
    # Uniswap poller, since both share DeFiStream's per-minute budget.
    last_exc: Exception | None = None
    for delay in (0.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            builder = getattr(ds.evm.lido, method_name)()
            builder = builder.network(chain).time_range(_iso(since), _iso(until))
            builder = builder.verbose().with_value()
            df = await builder.as_df("polars")
            if df.is_empty():
                return 0
            rows = transform(df, chain=chain)
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
    if not config.LIDO_EVENTS_ENABLED:
        log.info("LIDO_EVENTS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)

    eth_events = [e for e in config.LIDO_ETH_EVENTS if e in LIDO_EVENTS]
    l2_events = [e for e in config.LIDO_L2_EVENTS if e in LIDO_EVENTS]
    l2_chains = config.LIDO_L2_CHAINS

    if not eth_events and not (l2_chains and l2_events):
        log.info("no Lido events configured; idling")
        while True:
            await asyncio.sleep(3600)

    calls = _plan_calls(eth_events, l2_chains, l2_events)
    if events_filter:
        wanted = set(events_filter)
        calls = [c for c in calls if c[-1] in wanted]
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    t_start = datetime.now(timezone.utc).replace(tzinfo=None)
    log.info(
        "polling lido eth_events=%s l2_chains=%s l2_events=%s -> %d calls/tick (concurrency=%d) every %ss + gap-fill from watermark",
        eth_events, l2_chains, l2_events, len(calls), TICK_CONCURRENCY,
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

            async def _one(chain, event):
                nonlocal total_rows, err
                async with sem:
                    try:
                        n = await fetch_and_insert(
                            ds, chain=chain, event=event, since=since, until=now,
                        )
                        total_rows += n
                        log.info("%s/%s rows=%d", chain, event, n)
                    except Exception as exc:
                        err = f"{type(exc).__name__}: {exc}"[:1000]
                        log.exception("%s/%s fetch failed: %s", chain, event, exc)

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
                _method, table, _cols, _tf = LIDO_EVENTS[event]
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
                label = f"lido_events/{chain}/{event}"
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
