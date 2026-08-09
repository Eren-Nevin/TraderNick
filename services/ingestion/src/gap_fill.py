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
from datetime import datetime, timedelta, timezone
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


async def min_watermark_per_token(
    ch,
    *,
    table: str,
    tokens: list[str],
    time_col: str = "time",
    token_col: str = "token",
    max_staleness_seconds: float | None = None,
) -> datetime | None:
    """For a per-token table, return the MIN across all per-token MAX(time)
    watermarks — the earliest catch-up boundary we need to reach if we want
    every token caught up via one multi-token call.

    Tokens with no rows are *excluded* from the min so a fresh-install token
    doesn't peg us at 1970 forever; their on-server data starts before the
    earliest existing watermark anyway, so the multi-token call covers them
    too. If EVERY token is empty (truly fresh install), returns None and the
    caller falls back to the standard fresh-lookback ceiling.

    `max_staleness_seconds` (usually the sweep's `max_window_seconds`): when
    set, tokens whose latest row is older than `now - max_staleness_seconds`
    are *also* excluded from the min — AS LONG AS at least one fresher token
    remains to anchor the window. This stops a single permanently-stale token
    (delisted / gapped upstream) from pinning the min at its watermark and
    forcing every sweep to re-fetch the full max window (the highest-volume
    endpoints turn that into multi-GB frames every tick). The live sweep is
    capped at that same horizon, so those deep gaps can't be closed live
    anyway — they need a targeted backfill. If EVERY token is that stale (a
    total outage), we fall back to the min over all tokens so the capped sweep
    still recovers as much as it can."""
    parameters: dict = {"tokens": tokens}
    if max_staleness_seconds is not None:
        floor = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=max_staleness_seconds)
        parameters["floor"] = floor
        # Prefer the min over fresh tokens; fall back to min over all only when
        # no fresh token exists (total outage) so recovery still happens.
        select_min = (
            "if(countIf(mx >= {floor:DateTime}) > 0, "
            "minIf(mx, mx >= {floor:DateTime}), min(mx))"
        )
    else:
        select_min = "min(mx)"
    res = await ch.query(
        f"SELECT {select_min} FROM ("
        f"  SELECT max({time_col}) AS mx FROM {table}"
        f"  WHERE {token_col} IN {{tokens:Array(String)}}"
        f"  GROUP BY {token_col}"
        f"  HAVING max({time_col}) > toDateTime('2000-01-01 00:00:00')"
        f")",
        parameters=parameters,
    )
    rows = res.result_rows
    if not rows or rows[0][0] in (None, "", "1970-01-01 00:00:00"):
        return None
    v = rows[0][0]
    if isinstance(v, datetime):
        v = v.replace(tzinfo=None) if v.tzinfo else v
    else:
        v = datetime.fromisoformat(str(v))
    return None if v.year < 2000 else v


async def latest_time(
    ch,
    *,
    table: str,
    where: str = "",
    parameters: dict | None = None,
) -> datetime | None:
    """SELECT max(time) FROM <table> [WHERE ...]. Returns naive UTC, or
    None for an empty selection.

    CH's max() over a non-nullable DateTime returns the column's *default*
    (1970-01-01 00:00:00) when no rows match — not NULL. We treat any
    pre-2000 result as "no rows" so the caller falls back to the fresh-
    install lookback instead of trying to backfill from the Unix epoch
    (which would mean ~56 years × 24h × chunk_hours of empty API calls)."""
    sql = f"SELECT max(time) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    res = await ch.query(sql, parameters=parameters or {})
    rows = res.result_rows
    if not rows or rows[0][0] in (None, "", "1970-01-01 00:00:00"):
        return None
    v = rows[0][0]
    if isinstance(v, datetime):
        v = v.replace(tzinfo=None) if v.tzinfo else v
    else:
        v = datetime.fromisoformat(str(v))
    if v.year < 2000:
        return None
    return v


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
