"""Live polling for Binance raw trades. defistream 2.22 multi-token form
— one call returns rows for every configured token, each row carrying
its own `token` column.

Raw trades is the highest-volume binance endpoint. Each tick fetches a
small overlap window so the per-call payload stays bounded even with
23+ tokens summed into one response."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import token_batches
import sweep
from clickhouse import async_client, raw_trades_df_for_insert
from gap_fill import min_watermark_per_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_raw_trades] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.raw_trades()
        .token(*tokens)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    pd_df = raw_trades_df_for_insert(df)
    ch = await async_client()
    await ch.insert_df("tradernick.binance_raw_trades", pd_df)
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
             len(tokens), config.POLL_INTERVAL_SECONDS)

    sweep_cadence = sweep.sweep_cadence_s(config.POLL_INTERVAL_SECONDS)
    async def live_loop():
        jitter = sweep.live_jitter_s(config.POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tokens = token_batches.get_live_tokens()
            tick_end = time.monotonic() + config.POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            since = now - sweep.LIVE_OVERLAP
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token trades=%d (tokens=%d)", n, len(tokens))
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
                last_seen = await min_watermark_per_token(ch, table="tradernick.binance_raw_trades", tokens=tokens)
                since = sweep.sweep_since(
                    now=now,
                    sweep_cadence_seconds=sweep_cadence,
                    last_seen=last_seen,
                    # DeFiStream raw_trades cap is 1 day per request (not 7 —
                    # confirmed by upstream "Time range too large: N days.
                    # Maximum allowed: 1 days." 2026-06-06). 20h leaves
                    # margin for clock skew and the 5-min live overlap.
                    max_window_seconds=20 * 3600,
                    stream_name=stream_name or "binance_raw_trades",
                )
                if since < now:
                    n = await fetch_and_insert(ds, tokens, since, now)
                    log.info("binance_raw_trades sweep window=%s..%s rows=%d (min_last_seen=%s, tokens=%d)", since, now, n, last_seen, len(tokens))
            except Exception as exc:
                log.exception("binance_raw_trades sweep failed: %s", exc)
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



if __name__ == "__main__":
    asyncio.run(main())
