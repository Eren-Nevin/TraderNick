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
from groups.hyperliquid_events import (
    _CADENCE,
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
    # Per-event sweep cadence overrides. The sweep re-fetches a fixed rolling
    # window ([now - lookback, now], see _SWEEP_LOOKBACK) on this cadence — the
    # ReplacingMT source table absorbs the re-fetched rows for free, so a tighter
    # cadence only costs request volume, not correctness. All HL events are
    # pinned to **30 min** (2026-07-10) so a hole (e.g. a DeFiStream mid-window
    # outage) self-heals within ~30 min of the data becoming available upstream,
    # instead of up to an hour. Chunking (MAX_SWEEP_CHUNK) still bounds each
    # request so heavy endpoints don't blow past DeFiStream's per-request /
    # response-memory caps (the 2026-06-06 position_history OOM).
    _SWEEP_CADENCE_OVERRIDES = {
        "ohlcv":             1800.0,  # 30 min
        "trades":            1800.0,
        "fills":             1800.0,
        "trade_history":     1800.0,
        "transfers":         1800.0,
        "funding":           1800.0,
        "vaults":            1800.0,
        "position_history":  1800.0,
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
    # 31-day window per request, and heavy endpoints blow past its response-side
    # memory limit well before that. The sweep now fetches a fixed rolling window
    # ([now - lookback, now], lookback = 8h) — still wider than some per-request
    # caps — so cap each request and walk the window in sequential chunks so it
    # closes incrementally instead of failing outright.
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

    # Sweep look-back window. The sweep no longer derives `since` from the table
    # watermark (2026-07-10). The watermark strategy could only heal gaps at the
    # leading tip (or where a stale/pinned per-token watermark happened to drag
    # `since` back) — it walked straight over mid-range holes that already had
    # fresher data on top of them (e.g. a DeFiStream outage that recovered
    # mid-window: data before + after the hole ⇒ watermark sits past it ⇒ the
    # hole is never re-fetched). Instead every sweep fire now re-fetches a FIXED
    # rolling window [now - lookback, now], UNCONDITIONALLY, so any hole inside
    # the last `lookback` self-heals on the next sweep tick (ReplacingMergeTree
    # dedups the re-fetched rows for free). Uniform 8h for every event;
    # per-event overrides go in _SWEEP_LOOKBACK. Gaps older than `lookback` (long
    # downtime) still need an explicit backfill. The two tunables are the
    # lookback here and the sweep cadence (_SWEEP_CADENCE_OVERRIDES) — raise
    # either for a longer / denser backstop.
    _DEFAULT_SWEEP_LOOKBACK = timedelta(hours=8)
    _SWEEP_LOOKBACK: dict[str, timedelta] = {}

    async def _run_sweep_once(ch, label: str) -> tuple[int, str | None]:
        """Single sweep iteration. Re-fetches the fixed rolling window
        [now - lookback, now] (one fetch, or a sequence of chunks if the
        window is wider than the per-request cap), and returns
        (rows_inserted, error_string). Shared between the cadenced
        sweep_loop and the boot sweep that fires before live_loop starts."""
        tokens = token_batches.get_live_tokens()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            # Fixed rolling-window sweep: always re-fetch [now - lookback, now],
            # independent of the table watermark (see _SWEEP_LOOKBACK above).
            lookback = _SWEEP_LOOKBACK.get(event, _DEFAULT_SWEEP_LOOKBACK)
            since = now - lookback
            sweep_until = now
            _grid = _SWEEP_GRID_MIN.get(event)
            if _grid:
                # Snap `since`/`until` DOWN to the event's bucket grid so we
                # never ask DS for the in-progress (partial) bucket, while still
                # advancing to the most-recently-closed bucket on that grid.
                sweep_until = now.replace(minute=(now.minute // _grid) * _grid, second=0, microsecond=0)
                since = since.replace(minute=(since.minute // _grid) * _grid, second=0, microsecond=0)
                if since >= sweep_until:
                    # No closed bucket inside the window — nothing to sweep.
                    return 0, None
            if since >= sweep_until:
                return 0, None
            span = sweep_until - since
            if span <= MAX_SWEEP_CHUNK:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=sweep_until)
                log.info("%s %s window=%s..%s rows=%d",
                         event, label, since, sweep_until, n)
                return n, None
            log.info("%s %s window=%s spans %.1f hours — chunking in %.1f-hour slices",
                     event, label, since, span.total_seconds() / 3600.0,
                     MAX_SWEEP_CHUNK.total_seconds() / 3600.0)
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

    # Boot sweep — fire one sweep iteration before live_loop starts so a
    # restart immediately re-fetches the last `lookback` window (closing any
    # hole from the downtime) instead of waiting up to one sweep cadence for the
    # first cadenced fire. With the fixed rolling-window sweep this is no longer
    # a correctness race (the sweep covers [now - lookback, now] regardless of
    # the watermark) — it just makes recovery prompt on restart.
    ch = await async_client()
    _boot_t0 = time.monotonic()
    rows, err = await _run_sweep_once(ch, label="boot-sweep")
    log.info("%s boot-sweep settled in %.1fs rows=%d", event, time.monotonic() - _boot_t0, rows)
    if stream_name:
        await ch_status.write_sweep(stream_name, time.monotonic() - _boot_t0, rows=rows, error=err)

    await asyncio.gather(live_loop(), sweep_loop(ch))
