"""Live polling for Binance SPOT raw trades. Mirror of groups/binance_raw_trades
(perp/futures) — the only difference is `.market("spot")` on the SDK call and
the destination table. Binance spot is a fully separate dataset from perp.

Raw trades is the highest-volume binance endpoint. Each tick fetches a small
overlap window so the per-call payload stays bounded even with 23+ tokens
summed into one response."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import token_batches
import sweep
from clickhouse import async_client, raw_trades_df_for_insert
from gap_fill import min_watermark_per_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_spot_raw_trades] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30

# Sweep cadence pinned to an absolute 15 min rather than the default
# live-cadence * sweep.SWEEP_MULTIPLIER. Retuning POLL_INTERVAL_SECONDS above
# therefore leaves the gap-recovery interval alone — and since
# sweep.sweep_since() uses the cadence as its minimum lookback, the sweep's
# minimum window stays 15 min too.
SWEEP_CADENCE_SECONDS = 900

# Live window, overriding the 5-minute sweep.LIVE_OVERLAP default. Raw trades
# is the highest-volume binance endpoint (spot), and the live loop re-fetches
# this whole window every POLL_INTERVAL_SECONDS — so the window length is the
# direct multiplier on ingest bandwidth (2min/30s = 4x re-fetch, vs 10x at the
# 5-minute default). Anything older than this is the 15-minute sweep's job.
LIVE_OVERLAP_RAW_TRADES = timedelta(minutes=2)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.raw_trades()
        .market("spot")
        .token(*tokens)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    pd_df = raw_trades_df_for_insert(df)
    ch = await async_client()
    await ch.insert_df("tradernick.binance_raw_spot_trades", pd_df)
    return len(pd_df)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not token_batches.get_live_tokens():
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    tokens = token_batches.get_live_tokens()
    log.info("polling %d tokens every %ss + gap-fill from min-watermark — 1 multi-token call/tick",
             len(tokens), POLL_INTERVAL_SECONDS)

    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS, SWEEP_CADENCE_SECONDS)
    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tokens = token_batches.get_live_tokens()
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - LIVE_OVERLAP_RAW_TRADES
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token spot trades=%d (tokens=%d)", n, len(tokens))
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("multi-token fetch failed: %s", exc)
            if stream_name:
                await ch_status.write_tick(stream_name, n, error=err, duration_s=time.monotonic()-_live_t0)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop(once: bool = False):
        if not once:
            jitter = sweep.sweep_jitter_s(sweep_cadence)
            log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
            await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            tokens = token_batches.get_live_tokens()
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            try:
                last_seen = await min_watermark_per_token(ch, table="tradernick.binance_raw_spot_trades", tokens=tokens, max_staleness_seconds=20 * 3600)
                since = sweep.sweep_since(
                    now=now,
                    sweep_cadence_seconds=sweep_cadence,
                    last_seen=last_seen,
                    # DeFiStream raw_trades cap is 1 day per request; 20h leaves
                    # margin for clock skew and the 5-min live overlap.
                    max_window_seconds=20 * 3600,
                    stream_name=stream_name or "binance_spot_raw_trades",
                )
                if since < now:
                    n = await fetch_and_insert(ds, tokens, since, now)
                    log.info("binance_spot_raw_trades sweep window=%s..%s rows=%d (min_last_seen=%s, tokens=%d)", since, now, n, last_seen, len(tokens))
            except Exception as exc:
                log.exception("binance_spot_raw_trades sweep failed: %s", exc)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            if once:
                return
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    # Boot-sweep — recover the pre-restart gap before the live loop advances
    # the watermark past it.
    log.info("boot-sweep: recovering pre-restart gap before live loop starts")
    await sweep_loop(once=True)
    await asyncio.gather(live_loop(), sweep_loop())


if __name__ == "__main__":
    asyncio.run(main())
