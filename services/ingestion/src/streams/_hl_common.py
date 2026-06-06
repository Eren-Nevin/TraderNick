"""Shared HL stream runner: one process per (event), runs live + sweep.

Used by every streams/hyperliquid_<event>.py thin wrapper. Live ticks fire
on the per-event live cadence (`_CADENCE[event][0]`) with the small overlap
from `sweep.LIVE_OVERLAP`; the sweep loop fires at 10× that cadence and
fetches `[sweep_since, now]` based on the table watermark — covering any
holes the live loop might have skipped or any data missed during a worker
restart.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from defistream import AsyncDeFiStream

import ch_status
import config
import sweep
from clickhouse import HL_EVENTS, async_client
from gap_fill import latest_time, min_watermark_per_token
from groups.hyperliquid_events import (
    _CADENCE,
    _PER_TOKEN_TABLE,
    _fetch_and_insert,
)

log = logging.getLogger(__name__)


async def run(stream_name: str, event: str) -> None:
    """Entry point for a single HL stream worker process."""
    if not config.DEFISTREAM_API_KEY:
        log.error("DEFISTREAM_API_KEY is not set")
        sys.exit(2)
    if not config.INGEST_TOKENS:
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)
    if event not in _CADENCE:
        log.error("unknown HL event %s", event)
        sys.exit(2)

    tokens = list(config.INGEST_TOKENS)
    # Generous per-request timeout — position_history responses can balloon
    # when the sweep `since` reaches back across a long stale gap. SDK
    # default is 600s; bump to 1800s so we don't trip ReadTimeout while
    # DeFiStream is still streaming the parquet body.
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY, timeout=1800.0)
    tick_s, _ = _CADENCE[event]
    # Sweep cadence override — position_history responses are heavy
    # (~50K rows per 5 min × 26 tokens), so the default 10× live cadence
    # (50 min) ends up assembling a several-hundred-MB payload per fire.
    # A tighter 30-min cadence keeps each individual sweep cheaper at
    # DeFiStream's expense and avoids long read stalls.
    _SWEEP_CADENCE_OVERRIDES = {"position_history": 1800.0}  # 30 min
    sweep_cadence = _SWEEP_CADENCE_OVERRIDES.get(event, sweep.sweep_cadence_s(tick_s))
    _method, table, _cols, _tf = HL_EVENTS[event]
    per_token = event in _PER_TOKEN_TABLE

    log.info("HL stream %s starting (event=%s live=%ss sweep=%ss)",
             stream_name, event, tick_s, sweep_cadence)

    async def live_loop():
        jitter = sweep.live_jitter_s(tick_s)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tick_end = time.monotonic() + tick_s
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            since = now - sweep.LIVE_OVERLAP
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            await ch_status.write_tick_start(stream_name)
            try:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=now)
                log.info("%s rows=%d", event, n)
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("%s fetch failed", event)
            await ch_status.write_tick(stream_name, n, error=err, duration_s=time.monotonic()-_live_t0)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    # DeFiStream caps `position_history` (and several other endpoints) at a
    # 31-day window per request. The sweep `since` comes from the table
    # watermark, which can lag arbitrarily far behind — adding a new token
    # to the live roster temporarily drags `min_watermark_per_token` back
    # to that token's earliest row, and a single request would 400 with
    # "Time range too large". Cap each sweep request at 30 days (one day
    # of headroom) and walk the gap in sequential chunks so a wide window
    # closes incrementally instead of failing outright.
    MAX_SWEEP_CHUNK = timedelta(days=30)
    CHUNK_PACING_S = 0.5

    async def _run_sweep_once(ch, label: str) -> tuple[int, str | None]:
        """Single sweep iteration. Reads the watermark, computes since,
        runs one fetch (or a sequence of ≤30-day chunks if the gap is
        wider), and returns (rows_inserted, error_string). Shared between
        the cadenced sweep_loop and the boot sweep that fires before
        live_loop starts."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            if per_token:
                last_seen = await min_watermark_per_token(ch, table=table, tokens=tokens)
            else:
                last_seen = await latest_time(ch, table=table)
            since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
            if since >= now:
                return 0, None
            span = now - since
            if span <= MAX_SWEEP_CHUNK:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=now)
                log.info("%s %s window=%s..%s rows=%d (last_seen=%s)",
                         event, label, since, now, n, last_seen)
                return n, None
            log.info("%s %s window=%s spans %.1f days — chunking in %sd slices (last_seen=%s)",
                     event, label, since, span.total_seconds() / 86400.0,
                     int(MAX_SWEEP_CHUNK.total_seconds() // 86400), last_seen)
            chunks_total = 0
            n_chunks = 0
            cur = since
            while cur < now:
                nxt = min(cur + MAX_SWEEP_CHUNK, now)
                try:
                    n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=cur, until=nxt)
                    chunks_total += n
                    n_chunks += 1
                    log.info("%s %s chunk %s..%s rows=%d", event, label, cur, nxt, n)
                except Exception as exc:  # noqa: BLE001
                    # Per-chunk failure shouldn't abort the walk — the next
                    # sweep tick re-derives `since` from whatever rows did
                    # land and resumes from there.
                    log.exception("%s %s chunk %s..%s failed: %s", event, label, cur, nxt, exc)
                await asyncio.sleep(CHUNK_PACING_S)
                cur = nxt
            log.info("%s %s complete: %d chunks, total rows=%d",
                     event, label, n_chunks, chunks_total)
            return chunks_total, None
        except Exception as exc:  # noqa: BLE001
            log.exception("%s %s failed: %s", event, label, exc)
            return 0, f"{type(exc).__name__}: {exc}"[:1000]

    async def sweep_loop(ch):
        jitter = sweep.sweep_jitter_s(sweep_cadence)
        log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
        await asyncio.sleep(jitter)
        while True:
            next_fire = time.monotonic() + sweep_cadence
            _sweep_t0 = time.monotonic()
            rows, err = await _run_sweep_once(ch, label="sweep")
            await ch_status.write_sweep(stream_name, time.monotonic() - _sweep_t0, rows=rows, error=err) if stream_name else None
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    # Boot sweep — race-fix. Before letting live_loop fire, run one sweep
    # iteration so the cadenced sweep can't be lapped by the live insert
    # advancing the watermark past a pre-startup gap. Without this, a
    # restart after a multi-hour stop (e.g. while waiting on a backfill)
    # leaves a permanent hole in the affected event tables because the
    # live tick fetches a 5-min overlap around `now`, advancing the
    # watermark past the gap, and the subsequent cadenced sweep computes
    # `since` from the new (post-live) watermark and never sees the hole.
    ch = await async_client()
    _boot_t0 = time.monotonic()
    rows, err = await _run_sweep_once(ch, label="boot-sweep")
    log.info("%s boot-sweep settled in %.1fs rows=%d", event, time.monotonic() - _boot_t0, rows)
    if stream_name:
        await ch_status.write_sweep(stream_name, time.monotonic() - _boot_t0, rows=rows, error=err)

    await asyncio.gather(live_loop(), sweep_loop(ch))
