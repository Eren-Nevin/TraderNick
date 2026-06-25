"""Live polling for Binance order book depth snapshots.

12 rows per snapshot at fixed bps levels from mid-price, one snapshot
~every 30s per token. Same multi-token shape as binance_open_interest:
`.token(*symbols)` returns a DataFrame containing every requested
token, distinguished by the `token` column.

DeFiStream caps book_depth at 31 days/request, same as OI/funding/LSR.

Coverage caveat (2026-06): book_depth is served from Binance's
authoritative next-day dataset. The current (not-yet-settled) day comes
from a live feed that only publishes a symbol's current-day rows once
its order book genuinely reaches ±5%. Deep-book majors (BTC, ETH, BNB,
...) therefore appear only after the ~1-day settlement. Multi-token
requests just omit uncovered symbols for the current day — except when
*none* of the requested symbols have current-day coverage, in which
case the API returns HTTP 422 with a `vision_through` date. We catch
that, clip `until` to vision_through, and retry.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream
from defistream.exceptions import DeFiStreamError

import ch_status
import config
import token_batches
import sweep
from clickhouse import BOOK_DEPTH_COLUMNS, async_client, book_depth_df_to_rows
from gap_fill import min_watermark_per_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_book_depth] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300

# Daily settlement sweep. DeFiStream serves book_depth from Binance's
# authoritative *next-day* dataset, so many symbols (deep-book majors like
# BTC/ETH/BNB especially) only publish a given day's rows once that day has
# settled, ~1 day later. The 5-min live poll and the ~50-min gap sweep both
# ride the current edge and never look back far enough to pick those up, so
# once a day we re-fetch a 2-day trailing window over the full token
# universe. We don't need to enumerate which tokens are intraday-capable:
# DeFiStream silently omits any symbol without coverage for the window
# (live already relies on this), and the ReplacingMergeTree table dedups the
# overlap against whatever the live/gap sweeps already inserted.
DAILY_SWEEP_CADENCE_SECONDS = 24 * 3600
DAILY_SWEEP_LOOKBACK = timedelta(days=2)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_vision_through(exc: DeFiStreamError) -> datetime | None:
    """Pull `vision_through` from a 422 response, as naive UTC."""
    if exc.status_code != 422 or exc.response is None:
        return None
    try:
        data = exc.response.json()
    except Exception:
        return None
    s = data.get("vision_through") if isinstance(data, dict) else None
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


async def _do_fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    df = await (
        ds.exchange.binance.book_depth()
        .token(*tokens)
        .time_range(_iso(since), _iso(until))
        .as_df("polars")
    )
    if df.is_empty():
        return 0
    rows = book_depth_df_to_rows(df)
    ch = await async_client()
    await ch.insert("tradernick.binance_book_depth", rows, column_names=BOOK_DEPTH_COLUMNS)
    return len(rows)


async def fetch_and_insert(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    try:
        return await _do_fetch_and_insert(ds, tokens, since, until)
    except DeFiStreamError as exc:
        vt = _parse_vision_through(exc)
        if vt is None or vt <= since:
            log.warning("book_depth window %s..%s rejected; no coverage (vision_through=%s)", since, until, vt)
            return 0
        log.info("book_depth clipping until %s -> %s (vision_through)", until, vt)
        return await _do_fetch_and_insert(ds, tokens, since, vt)


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not token_batches.get_ingest_tokens():
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    tokens = token_batches.get_ingest_tokens()
    log.info("polling %d tokens every %ss + gap-fill from min-watermark — 1 multi-token call/tick",
             len(tokens), POLL_INTERVAL_SECONDS)

    sweep_cadence = sweep.sweep_cadence_s(POLL_INTERVAL_SECONDS)

    async def live_loop():
        jitter = sweep.live_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tokens = token_batches.get_ingest_tokens()
            tick_end = time.monotonic() + POLL_INTERVAL_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - sweep.LIVE_OVERLAP
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            if stream_name:
                await ch_status.write_tick_start(stream_name)
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("multi-token rows=%d (tokens=%d)", n, len(tokens))
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
            tokens = token_batches.get_ingest_tokens()
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            try:
                last_seen = await min_watermark_per_token(ch, table="tradernick.binance_book_depth", tokens=tokens)
                since = sweep.sweep_since(
                    now=now,
                    sweep_cadence_seconds=sweep_cadence,
                    last_seen=last_seen,
                    # DeFiStream book_depth cap is 31 days; leave 1 day of slack.
                    max_window_seconds=30 * 24 * 3600,
                    stream_name=stream_name or "binance_book_depth",
                )
                if since < now:
                    n = await fetch_and_insert(ds, tokens, since, now)
                    log.info("binance_book_depth sweep window=%s..%s rows=%d (min_last_seen=%s, tokens=%d)",
                             since, now, n, last_seen, len(tokens))
            except Exception as exc:
                log.exception("binance_book_depth sweep failed: %s", exc)
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=_sweep_rows, error=_sweep_err) if stream_name else None
            if once:
                return
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    async def daily_sweep_loop():
        # One worker per stream, so the jitter here is only to avoid every
        # service hammering DeFiStream at the same boot instant — keep it
        # small so the first daily sweep fires shortly after startup rather
        # than up to a day later.
        jitter = sweep.sweep_jitter_s(POLL_INTERVAL_SECONDS)
        log.info("daily_sweep_loop: waiting %.0fs before first fire (cadence=%ss, lookback=%s)",
                 jitter, DAILY_SWEEP_CADENCE_SECONDS, DAILY_SWEEP_LOOKBACK)
        await asyncio.sleep(jitter)
        while True:
            tokens = token_batches.get_ingest_tokens()
            next_fire = time.monotonic() + DAILY_SWEEP_CADENCE_SECONDS
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - DAILY_SWEEP_LOOKBACK
            try:
                n = await fetch_and_insert(ds, tokens, since, now)
                log.info("binance_book_depth daily sweep window=%s..%s rows=%d (tokens=%d)",
                         since, now, n, len(tokens))
            except Exception as exc:
                log.exception("binance_book_depth daily sweep failed: %s", exc)
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    # Boot-sweep — run one sweep iteration to completion BEFORE the live
    # loop starts, so a restart after a long stop recovers the full
    # [last_seen, now] gap instead of live_loop advancing the watermark
    # past it (mirrors streams/_hl_common.py).
    log.info("boot-sweep: recovering pre-restart gap before live loop starts")
    await sweep_loop(once=True)
    await asyncio.gather(live_loop(), sweep_loop(), daily_sweep_loop())


if __name__ == "__main__":
    asyncio.run(main())
