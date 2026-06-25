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
import token_batches
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
    if not token_batches.get_live_tokens():
        log.error("INGEST_TOKENS is empty")
        sys.exit(2)
    if event not in _CADENCE:
        log.error("unknown HL event %s", event)
        sys.exit(2)

    tokens = token_batches.get_live_tokens()
    # Generous per-request timeout — position_history responses can balloon
    # when the sweep `since` reaches back across a long stale gap. SDK
    # default is 600s; bump to 1800s so we don't trip ReadTimeout while
    # DeFiStream is still streaming the parquet body.
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY, timeout=1800.0)
    tick_s, _ = _CADENCE[event]
    # Per-event sweep cadence overrides — the default is
    # sweep.sweep_cadence_s(live) = live × SWEEP_MULTIPLIER (10×). For the
    # 15m HL streams that default works out to 2h30m, which on heavy
    # endpoints (ohlcv / trades / fills / trade_history / transfers)
    # produces multi-hundred-MB sweep responses that DeFiStream sometimes
    # rejects with "Time range too large" or 500s under load. Cap them at
    # **1 h** (2026-06-11) so each sweep covers a smaller, predictable
    # window even if the live tick missed a few minutes — the ReplacingMT
    # source table absorbs the re-fetched rows for free, so a tighter
    # cadence costs nothing on correctness.
    #
    # position_history stays at 30 min (its responses are heaviest per
    # bucket — see the 2026-06-06 OOM incident).
    # funding / vaults stay at the 10× default (5h) — they're sparse
    # endpoints where the longer window is cheap.
    _SWEEP_CADENCE_OVERRIDES = {
        "ohlcv":             3600.0,  # 1 h
        "trades":            3600.0,
        "fills":             3600.0,
        "trade_history":     3600.0,
        "transfers":         3600.0,
        "position_history":  3600.0,  # 1 h (was 30 min — aligned with trade_history)
    }
    sweep_cadence = _SWEEP_CADENCE_OVERRIDES.get(event, sweep.sweep_cadence_s(tick_s))

    # Events whose sweep `since` / `until` should be snapped to whole-hour
    # boundaries. The HL position_history and trade_history endpoints
    # aggregate over discrete buckets (15m and 1h respectively); asking DS
    # for an off-grid window forces them to recompute on partial buckets,
    # which has been the trigger for HTTP 500 Code 241 on
    # `position_history` (their CH hits its memory limit on the half-open
    # last bucket). Snapping keeps every sweep request on the same grid
    # the live tick uses and reduces the chance of DS being asked the
    # same "almost there" query twice.
    _HOUR_ALIGNED_SWEEP_EVENTS = {"position_history", "trade_history"}
    _method, table, _cols, _tf = HL_EVENTS[event]
    per_token = event in _PER_TOKEN_TABLE

    log.info("HL stream %s starting (event=%s live=%ss sweep=%ss)",
             stream_name, event, tick_s, sweep_cadence)

    async def live_loop():
        jitter = sweep.live_jitter_s(tick_s)
        log.info("live_loop: waiting %.0fs before first fire", jitter)
        await asyncio.sleep(jitter)
        while True:
            tokens = token_batches.get_live_tokens()
            tick_end = time.monotonic() + tick_s
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            _sweep_rows = 0
            _sweep_err: str | None = None
            _sweep_t0 = time.monotonic()
            # Every HL stream queries DefiStream over the most-recently-closed
            # 15-minute slot. Was previously a 5-min sliding window which:
            #   - missed snapshots on position_history (15m grid) when the tick
            #     happened to fire mid-bucket
            #   - asked DS for tiny stop-gap windows that don't compose well
            #     with their internal bucket aggregation (especially under load
            #     — the recurring HTTP 500 Code 241 incidents)
            # Aligned, fixed-width [floor_now - 15m, floor_now) windows are:
            #   - what DS's window aggregation is built for: one snapshot /
            #     event-bucket per response
            #   - idempotent across overlapping ticks (ReplacingMergeTree dedup
            #     in the source table absorbs repeated fetches)
            #   - mismatched only for funding/vaults (30m cadence) which fire
            #     half as often as 15m and so capture every other slot; the
            #     sweep tier fills in the alternate slot
            floor_now = now.replace(
                minute=(now.minute // 15) * 15,
                second=0, microsecond=0,
            )
            since = floor_now - timedelta(minutes=15)
            until = floor_now
            n = 0
            err: str | None = None
            _live_t0 = time.monotonic()
            await ch_status.write_tick_start(stream_name)
            try:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=until)
                log.info("%s rows=%d (since=%s until=%s)", event, n, since, until)
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
    # "Time range too large". Cap each sweep request and walk the gap in
    # sequential chunks so a wide window closes incrementally instead of
    # failing outright.
    #
    # Per-event override below `MAX_SWEEP_CHUNK` for heavy endpoints whose
    # response volume blows past DeFiStream's *response-side* memory limit
    # well before the documented 30-day window cap. `position_history` is
    # the worst: 31 tokens × 5-min snapshots × wallet-level rows produces
    # 50-120K rows per 5-min bucket; an 8-hour gap on that shape returned
    # HTTP 500 Code 241 (upstream ClickHouse OOM) during the 2026-06-06
    # incident. Capping at 1 hour keeps each chunk well within the volume
    # DeFiStream demonstrably handles in steady state.
    _SWEEP_CHUNK_OVERRIDES = {"position_history": timedelta(hours=1)}
    MAX_SWEEP_CHUNK = _SWEEP_CHUNK_OVERRIDES.get(event, timedelta(days=30))
    CHUNK_PACING_S = 0.5

    async def _run_sweep_once(ch, label: str) -> tuple[int, str | None]:
        """Single sweep iteration. Reads the watermark, computes since,
        runs one fetch (or a sequence of ≤30-day chunks if the gap is
        wider), and returns (rows_inserted, error_string). Shared between
        the cadenced sweep_loop and the boot sweep that fires before
        live_loop starts."""
        tokens = token_batches.get_live_tokens()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            if per_token:
                last_seen = await min_watermark_per_token(ch, table=table, tokens=tokens)
            else:
                last_seen = await latest_time(ch, table=table)
            since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
            sweep_until = now
            if event in _HOUR_ALIGNED_SWEEP_EVENTS:
                # Round `until` DOWN to the most-recently-closed hour so we
                # never ask DS for the in-progress hour. Round `since` DOWN
                # to the same grid so the request lines up bucket-to-bucket
                # with the live tick's window.
                sweep_until = now.replace(minute=0, second=0, microsecond=0)
                since = since.replace(minute=0, second=0, microsecond=0)
                if since >= sweep_until:
                    # We're inside the current hour and haven't crossed an
                    # hour boundary since last_seen — nothing to sweep.
                    return 0, None
            if since >= sweep_until:
                return 0, None
            span = sweep_until - since
            if span <= MAX_SWEEP_CHUNK:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=sweep_until)
                log.info("%s %s window=%s..%s rows=%d (last_seen=%s)",
                         event, label, since, sweep_until, n, last_seen)
                return n, None
            log.info("%s %s window=%s spans %.1f hours — chunking in %.1f-hour slices (last_seen=%s)",
                     event, label, since, span.total_seconds() / 3600.0,
                     MAX_SWEEP_CHUNK.total_seconds() / 3600.0, last_seen)
            chunks_total = 0
            n_chunks = 0
            cur = since
            while cur < sweep_until:
                nxt = min(cur + MAX_SWEEP_CHUNK, sweep_until)
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
