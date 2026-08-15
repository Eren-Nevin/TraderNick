"""Scheduled Binance order-book-depth ingestion.

12 rows per snapshot at fixed bps levels from mid-price (±20, ±100, ±200, ±300,
±400, ±500), one snapshot ~every 30s per token. `percentage` is a data *column*
(long format), so the ±20 level DeFiStream added (data from 2025-10-01 on) needs
no schema change — it's just extra rows; pre-2026-02 rows may carry value=0 for
±20, which is fine.

DeFiStream serves book_depth from Binance's authoritative *next-day* dataset, so
the current day (± a couple hours) is unsettled and often returns 0 / omits
symbols. We therefore DON'T poll the live edge — we fetch on a fixed daily
schedule (UTC) so every pull lands on settled data:

  * 12:00 UTC — fetch the last 2 days for all live tokens (primary daily pull).
  * 18:00 UTC — fetch the last 3 days for all live tokens (recovery: covers up
    to 3 missed days after downtime).

Overlap is harmless: the ReplacingMergeTree(ingested_at) table keeps the latest
fetch per (token, time, percentage), so a later settled (non-zero) value
replaces an earlier truncated 0.

DeFiStream caps book_depth at 31 days/request; when *no* requested symbol has
current-day coverage the API returns HTTP 422 with a `vision_through` date,
which we catch, clip `until` to, and retry.
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
from clickhouse import BOOK_DEPTH_COLUMNS, async_client, book_depth_df_to_rows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [binance_book_depth] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Fixed daily schedule (UTC). Noon = primary 2-day pull; 18:00 = 3-day recovery.
NOON_HOUR_UTC = 12
NOON_LOOKBACK = timedelta(days=2)
SWEEP_HOUR_UTC = 18
SWEEP_LOOKBACK = timedelta(days=3)

# Fetch large windows in 1-day chunks: bounds peak memory (book_depth is a
# per-30s snapshot series over ~95 tokens) and gives each day its own
# vision_through handling, so an unsettled current day is clipped, not fatal.
FETCH_CHUNK = timedelta(days=1)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_until(hour_utc: int) -> float:
    """Seconds until the next HH:00:00 UTC."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


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


async def fetch_window_chunked(ds: AsyncDeFiStream, tokens: list[str], since: datetime, until: datetime) -> int:
    """Fetch [since, until) in 1-day chunks, inserting incrementally so peak
    memory stays bounded to one day of snapshots."""
    total = 0
    start = since
    while start < until:
        end = min(start + FETCH_CHUNK, until)
        total += await fetch_and_insert(ds, tokens, start, end)
        start = end
    return total


async def main(stream_name: str | None = None):
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not token_batches.get_live_tokens():
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)

    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    log.info("book_depth scheduled ingestion: %02d:00 UTC (last %s) + %02d:00 UTC (last %s), tokens=%d",
             NOON_HOUR_UTC, NOON_LOOKBACK, SWEEP_HOUR_UTC, SWEEP_LOOKBACK, len(token_batches.get_live_tokens()))

    async def scheduled_loop(hour_utc: int, lookback: timedelta, label: str):
        while True:
            wait = _seconds_until(hour_utc)
            log.info("%s: sleeping %.0fs until next %02d:00 UTC", label, wait, hour_utc)
            await asyncio.sleep(wait)
            tokens = token_batches.get_live_tokens()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            since = now - lookback
            _t0 = time.monotonic()
            rows = 0
            err: str | None = None
            try:
                rows = await fetch_window_chunked(ds, tokens, since, now)
                log.info("book_depth %s fetch window=%s..%s rows=%d (tokens=%d)", label, since, now, rows, len(tokens))
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("book_depth %s fetch failed: %s", label, exc)
            if stream_name:
                await ch_status.write_sweep(stream_name, time.monotonic() - _t0, rows=rows, error=err)
            # Guard against a same-second re-fire if the fetch returned instantly.
            await asyncio.sleep(1)

    await asyncio.gather(
        scheduled_loop(NOON_HOUR_UTC, NOON_LOOKBACK, "noon"),
        scheduled_loop(SWEEP_HOUR_UTC, SWEEP_LOOKBACK, "sweep-6pm"),
    )


if __name__ == "__main__":
    asyncio.run(main())
