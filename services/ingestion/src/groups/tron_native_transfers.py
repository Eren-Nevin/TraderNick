import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tron_native_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30

# Live window, overriding the 5-minute sweep.LIVE_OVERLAP default. At a 30s
# cadence this re-fetches each row ~4x; RMT dedupes the overlap. Anything older
# than this is the sweep's job.
LIVE_OVERLAP_TRANSFERS = timedelta(minutes=2)

# Sweep fires every 30 min but always looks back 1 HOUR — the window is
# deliberately 2x the cadence so consecutive sweeps overlap and a late-arriving
# or reorged row can't fall between two fires. Pinned absolutely rather than
# derived from live-cadence * sweep.SWEEP_MULTIPLIER, so retuning
# POLL_INTERVAL_SECONDS above does not move either number.
SWEEP_CADENCE_SECONDS = 1800
SWEEP_WINDOW_SECONDS = 3600
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, since: datetime, until: datetime) -> int:
    df = await (
        ds.tron.native.transfers()
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="tron_native", chain="TRON", token_override="TRX")
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.TRON_NATIVE_TRANSFERS_ENABLED:
        log.info("TRON_NATIVE_TRANSFERS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    log.info("polling tron native transfers every %ss + gap-fill from watermark",
             POLL_INTERVAL_SECONDS)

    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS, SWEEP_CADENCE_SECONDS)
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
            since = now - LIVE_OVERLAP_TRANSFERS
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, since, now)
                log.info("TRX rows=%d", n)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("TRX fetch failed: %s", exc)
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
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            try:
                last_seen = await latest_time(
            ch, table="tradernick.transfers",
            where="kind = 'tron_native' AND chain = 'TRON'",
        )
                since = sweep.sweep_since(
                    now=now,
                    sweep_cadence_seconds=sweep_cadence,
                    min_lookback_seconds=SWEEP_WINDOW_SECONDS,
                    last_seen=last_seen,
                    # DeFiStream EVM parquet event endpoints cap each request
                    # at 7 days (100k blocks). Leave 1 day of slack so the
                    # 5-min live overlap + clock skew can never push us over.
                    max_window_seconds=6 * 24 * 3600,
                    stream_name=stream_name,
                )
                if since < now:
                    n = await fetch_and_insert(ds, since, now)
                    log.info("tron_native_transfers sweep window=%s..%s rows=%d (last_seen=%s)", since, now, n, last_seen)
            except Exception as exc:
                log.exception("tron_native_transfers sweep failed: %s", exc)
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
