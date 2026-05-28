"""Shared helpers for on-startup gap recovery in live polling groups.

The pattern each group follows is:

    t_start = datetime.now(UTC, naive=True)
    sem = asyncio.Semaphore(TICK_CONCURRENCY)
    await asyncio.gather(live_loop(sem), gap_fill_task(t_start, sem))

The live loop starts polling immediately so any events that arrive after
`t_start` are captured by its normal 3-minute overlap window. Meanwhile
gap_fill_task asks ClickHouse for the watermark per (chain[, ...], event),
walks the interval [last_seen - small_overlap, t_start] in fixed-size
chunks, and fires the same DeFiStream call the backfill driver uses.
Because gap-fill stops at `t_start` (captured BEFORE the live loop), no
new hole opens up while it runs — the live loop is already covering the
post-t_start window.

For a fresh table (no rows ever) we fall back to a 24h lookback so a
clean-install doesn't accidentally kick off a 90-day catchup. If you want
a longer initial backfill, use POST /jobs/backfill/* explicitly.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Iterable

log = logging.getLogger("gap_fill")

# How far back we read the watermark per (chain, event). 5min handles the
# usual case where the last successful tick was seconds before the
# restart; bigger overlaps just re-fetch a few rows the ReplacingMergeTree
# will dedupe.
WATERMARK_OVERLAP = timedelta(minutes=5)

# Default ceiling for fresh tables — we don't want gap-fill to ever turn
# into a multi-week backfill by accident.
DEFAULT_FRESH_LOOKBACK = timedelta(hours=24)

# Inter-chunk pacing (seconds) — matches the existing backfill drivers so
# we share the same per-key DeFiStream rate budget. Each chunk is one API
# call; with 1.2s between chunks we sit well under DeFiStream's ~50 req/min
# per-key allowance.
CHUNK_PACING_S = 1.2

# Each chunk covers this many hours. 6h is a sweet spot — small enough
# that one chunk almost always fits inside DeFiStream's per-call row cap,
# big enough that even a 24h gap takes only 4 calls per (chain, event).
DEFAULT_CHUNK_HOURS = 6


async def latest_time(
    ch,
    *,
    table: str,
    where: str = "",
    parameters: dict | None = None,
) -> datetime | None:
    """SELECT max(time) FROM <table> [WHERE ...]. Returns naive UTC."""
    sql = f"SELECT max(time) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    res = await ch.query(sql, parameters=parameters or {})
    rows = res.result_rows
    if not rows or rows[0][0] in (None, "", "1970-01-01 00:00:00"):
        return None
    v = rows[0][0]
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    return datetime.fromisoformat(str(v))


def iter_chunks(
    since: datetime,
    until: datetime,
    *,
    hours: int = DEFAULT_CHUNK_HOURS,
) -> Iterable[tuple[datetime, datetime]]:
    """Yield (s, u) tuples covering [since, until] in `hours`-wide windows."""
    cur = since
    step = timedelta(hours=hours)
    while cur < until:
        nxt = min(cur + step, until)
        yield cur, nxt
        cur = nxt


async def run_chunked(
    *,
    label: str,
    since: datetime,
    until: datetime,
    call,                     # async (s, u) -> int
    chunk_hours: int = DEFAULT_CHUNK_HOURS,
    pacing_s: float = CHUNK_PACING_S,
) -> int:
    """Sequentially walk [since, until] in `chunk_hours` slices, firing
    `call(s, u)` for each. Returns total rows inserted across all chunks.
    Per-chunk exceptions are logged but don't abort the walk — gap-fill is
    best-effort by design (the live loop is the primary signal)."""
    if since >= until:
        return 0
    total = 0
    for s, u in iter_chunks(since, until, hours=chunk_hours):
        try:
            n = await call(s, u)
            total += n
            log.info("%s gap-fill chunk %s -> %s rows=%d", label, s.isoformat(), u.isoformat(), n)
        except Exception as exc:
            log.exception("%s gap-fill chunk %s->%s failed: %s", label, s, u, exc)
        await asyncio.sleep(pacing_s)
    return total


def resolve_since(
    last_seen: datetime | None,
    *,
    t_start: datetime,
    fresh_lookback: timedelta = DEFAULT_FRESH_LOOKBACK,
) -> datetime:
    """Translate watermark + restart-time into the gap-fill start. Adds the
    safety overlap (re-fetched rows are deduped by ReplacingMergeTree)."""
    if last_seen is None:
        return t_start - fresh_lookback
    return last_seen - WATERMARK_OVERLAP
