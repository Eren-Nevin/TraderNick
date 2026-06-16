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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tron_trc20_transfers] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_and_insert(ds: AsyncDeFiStream, token: str, since: datetime, until: datetime) -> int:
    df = await (
        ds.tron.trc20.transfers(token)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = transfers_df_to_rows(df, kind="trc20", chain="TRON", token_override=token)
    ch = await async_client()
    await ch.insert("tradernick.transfers", rows, column_names=TRANSFER_COLUMNS)
    return len(rows)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    tokens = config.TRON_TRC20_TOKENS
    if not tokens:
        log.info("no TRON_TRC20_TRANSFERS tokens configured; idling")
        while True:
            await asyncio.sleep(3600)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)
    log.info("tron trc20 tokens=%s; live cadence=%ss, sweep cadence=%ss",
             tokens, POLL_INTERVAL_SECONDS, sweep_cadence)

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
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            for token in tokens:
                try:
                    n = await fetch_and_insert(ds, token, since, now)
                    log.info("%s rows=%d", token, n)
                    total_rows += n
                except Exception as exc:
                    err = f"{token}: {type(exc).__name__}: {exc}"[:1000]
                    log.exception("%s fetch failed: %s", token, exc)
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

            async def _one(token):
                try:
                    last_seen = await latest_time(
                        ch, table="tradernick.transfers",
                        where="kind = 'trc20' AND chain = 'TRON' AND token = {token:String}",
                        parameters={"token": token},
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
                    label = f"tron_trc20_transfers/{token}"
                    n = await fetch_and_insert(ds, token, since, now)
                    log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)", label, since, now, n, last_seen)
                except Exception as exc:
                    log.exception("sweep failed for %s: %s", token, exc)

            await asyncio.gather(*(_one(t) for t in tokens), return_exceptions=True)
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
