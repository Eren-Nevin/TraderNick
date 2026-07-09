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
    # funding / vaults are pinned to 1h (2026-07-09): their live tick dropped to
    # 60s, so the 10× default would put the sweep at 10 min — needlessly frequent
    # for sparse endpoints. 1h is a cheap backstop behind the fresh live tick.
    _SWEEP_CADENCE_OVERRIDES = {
        "ohlcv":             3600.0,  # 1 h
        "trades":            3600.0,
        "fills":             3600.0,
        "trade_history":     3600.0,
        "transfers":         3600.0,
        "funding":           3600.0,
        "vaults":            3600.0,
        "position_history":  1800.0,  # 30 min — keep the sweep backstop tight so
                                      # position_history lands within ~30 min.
    }
    sweep_cadence = _SWEEP_CADENCE_OVERRIDES.get(event, sweep.sweep_cadence_s(tick_s))

    # Per-event sweep grid (minutes). The HL position_history / trade_history
    # endpoints aggregate over discrete buckets (15m / 1h); asking DS for an
    # OFF-grid window forces them to recompute on a partial bucket, which has
    # triggered HTTP 500 Code 241 (their CH OOMs on the half-open last bucket).
    # Snapping `since`/`until` to the event's bucket grid avoids that. Crucially
    # position_history snaps to its REAL 15m grid (not whole hours) so the sweep
    # can advance to within ~15m of now — landing within ~30m instead of ~1h.
    # trade_history stays on its 1h bucket grid.
    _SWEEP_GRID_MIN = {"position_history": 15, "trade_history": 60}
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
            # Aligned, fixed-width [floor_now - lookback, floor_now) windows are:
            #   - what DS's window aggregation is built for: one snapshot /
            #     event-bucket per response
            #   - idempotent across overlapping ticks (ReplacingMergeTree dedup
            #     in the source table absorbs repeated fetches)
            # Only position_history/trade_history still use this 15-min grid;
            # ohlcv/trades/funding/transfers/vaults dropped to a 1-min grid + 60s
            # tick (2026-07-09) for ~1-min freshness (see _live_grid below).
            # fills/ohlcv/trades/funding/transfers/vaults poll every 60s on a 1-min
            # grid so `until` = floor_1min(now) and the newest data is ~1 min old
            # (+ DS's ~15s lag), instead of waiting for a coarser bucket to close.
            # fills was on a 5-min grid until 2026-07-09 — that capped the newest
            # ingested fill at the last closed 5-min boundary, so fills read 1–5 min
            # stale (avg ~2–3m) even at a 60s tick; dropping it to a 1-min grid fixes
            # that. fills are raw events (verified: DS serves fresh, narrow windows at
            # HTTP 200, no Code 241), so an off-15m-grid window is safe. Only
            # position_history/trade_history keep the 15-min grid (heavy aggregated
            # buckets — off-grid windows have triggered DS 500s).
            _FAST_1M = {"fills", "ohlcv", "trades", "funding", "transfers", "vaults"}
            _live_grid = 1 if event in _FAST_1M else 15
            floor_now = now.replace(
                minute=(now.minute // _live_grid) * _live_grid,
                second=0, microsecond=0,
            )
            # position_history snapshots are published ~25m late upstream, so a
            # 15m live window (just-closed slot) always misses them and we used
            # to rely on the slow hourly sweep. Re-fetch the last 45m of 15m
            # slots each tick so a late-published snapshot lands within ~30m.
            # Window stays on the 15m grid; RMT dedups the re-fetched overlap.
            # Fast 1-min events (incl. fills) re-fetch the last 5 one-min slots each
            # tick so DS's residual lag (~15s, measured 2026-07-09) lands within a
            # minute of publication (RMT dedups the overlap; 5m is a generous margin
            # over the ~15s lag, and the 1h sweep backstops anything missed).
            # position_history: 45m (late-published).
            _live_lookback = (
                45 if event == "position_history"
                else 5 if event in _FAST_1M
                else 15
            )
            since = floor_now - timedelta(minutes=_live_lookback)
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
    #
    # `fills` is per-token, so the sweep watermark is min_watermark_per_token —
    # a single stale token (e.g. TST, last fill 2026-05-25) drags `since` back
    # ~30 days, and with the 30-day default the whole gap loads in ONE request
    # (~150M rows ≈ 70 GB RSS — the 2026-06-26 OOM). 6-hour chunks (~1M fills)
    # keep each fetch bounded regardless of how far the watermark has drifted.
    # `trades` (like fills) is high-volume, but its DeFiStream endpoint also caps
    # each request at 1 DAY — the 30-day default chunked past that and every
    # sweep chunk 400'd ("Time range too large: 30.0 days. Maximum allowed: 1
    # days."). 6h keeps chunks bounded and well under the 1-day cap.
    _SWEEP_CHUNK_OVERRIDES = {
        "position_history": timedelta(hours=1),
        "fills":            timedelta(hours=6),
        "trades":           timedelta(hours=6),
    }
    MAX_SWEEP_CHUNK = _SWEEP_CHUNK_OVERRIDES.get(event, timedelta(days=30))
    CHUNK_PACING_S = 0.5

    # Per-event cap on how far back a sweep may reach. These endpoints are
    # per-token, so the sweep watermark is min_watermark_per_token — a single
    # dead/inactive token pins it far back and the sweep re-walks (and re-inserts)
    # that whole span every cycle, which also keeps re-invalidating the derived
    # rollups. Examples: `fills` TST (no fills since 2026-05-25, ~30d) and
    # `position_history` VINE (~2d). The live tick owns recency (15/45-min
    # window); the sweep only closes recent gaps, so cap its look-back. Larger
    # genuine gaps (long downtime, new-token history) use an explicit backfill.
    _SWEEP_MAX_LOOKBACK = {
        "fills":            timedelta(hours=24),
        "trades":           timedelta(hours=24),
        "position_history": timedelta(hours=24),
    }

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
            _cap = _SWEEP_MAX_LOOKBACK.get(event)
            if _cap is not None and since < now - _cap:
                since = now - _cap
            sweep_until = now
            _grid = _SWEEP_GRID_MIN.get(event)
            if _grid:
                # Snap `since`/`until` DOWN to the event's bucket grid so we
                # never ask DS for the in-progress (partial) bucket, while still
                # advancing to the most-recently-closed bucket on that grid.
                sweep_until = now.replace(minute=(now.minute // _grid) * _grid, second=0, microsecond=0)
                since = since.replace(minute=(since.minute // _grid) * _grid, second=0, microsecond=0)
                if since >= sweep_until:
                    # No closed bucket crossed since last_seen — nothing to sweep.
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
