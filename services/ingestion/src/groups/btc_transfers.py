import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import TRANSFER_COLUMNS, async_client, transfers_df_to_rows
from gap_fill import latest_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [btc_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# BTC has low transfer volume relative to other chains — pull less often.
# Live = 30 min, sweep = 300 min (sweep_cadence_s = POLL_INTERVAL × 10).
POLL_INTERVAL_SECONDS = 1800


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, since: datetime, until: datetime) -> int:
    df = await (
        ds.bitcoin.native.transfers()
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="btc", chain="BTC", token_override="BTC")
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.BTC_TRANSFERS_ENABLED:
        log.info("BTC_TRANSFERS_ENABLED=0; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info("btc transfers; live cadence=%ss, sweep cadence=%ss",
             POLL_INTERVAL_SECONDS, sweep_cadence)

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
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, since, now)
                log.info("BTC rows=%d", n)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("BTC fetch failed: %s", exc)
            if stream_name:
                await ch_status.write_tick(stream_name, n, error=err, duration_s=time.monotonic()-_live_t0)
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
            try:
                last_seen = await latest_time(
                    ch, table="tradernick.transfers",
                    where="kind = 'btc' AND chain = 'BTC'",
                )
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since < now:
                    n = await fetch_and_insert(ds, since, now)
                    log.info("btc_transfers sweep window=%s..%s rows=%d (last_seen=%s)", since, now, n, last_seen)
            except Exception as exc:
                log.exception("btc_transfers sweep failed: %s", exc)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())


if __name__ == "__main__":
    asyncio.run(main())
