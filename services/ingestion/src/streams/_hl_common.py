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
from datetime import datetime, timezone

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
    ds = AsyncDeFiStream(api_key=config.DEFISTREAM_API_KEY)
    tick_s, _ = _CADENCE[event]
    sweep_cadence = sweep.sweep_cadence_s(tick_s)
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
            since = now - sweep.LIVE_OVERLAP
            n = 0
            err: str | None = None
            await ch_status.write_tick_start(stream_name)
            try:
                n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=now)
                log.info("%s rows=%d", event, n)
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"[:1000]
                log.exception("%s fetch failed", event)
            await ch_status.write_tick(stream_name, n, error=err)
            await asyncio.sleep(max(0.0, tick_end - time.monotonic()))

    async def sweep_loop():
        jitter = sweep.sweep_jitter_s(sweep_cadence)
        log.info("sweep_loop: waiting %.0fs before first fire (cadence=%ss)", jitter, sweep_cadence)
        await asyncio.sleep(jitter)
        ch = await async_client()
        while True:
            next_fire = time.monotonic() + sweep_cadence
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                if per_token:
                    last_seen = await min_watermark_per_token(ch, table=table, tokens=tokens)
                else:
                    last_seen = await latest_time(ch, table=table)
                since = sweep.sweep_since(now=now, sweep_cadence_seconds=sweep_cadence, last_seen=last_seen)
                if since < now:
                    n = await _fetch_and_insert(ds, event=event, tokens=tokens, since=since, until=now)
                    log.info("%s sweep window=%s..%s rows=%d (last_seen=%s)",
                             event, since, now, n, last_seen)
            except Exception as exc:  # noqa: BLE001
                log.exception("%s sweep failed: %s", event, exc)
            await asyncio.sleep(max(0.0, next_fire - time.monotonic()))

    await asyncio.gather(live_loop(), sweep_loop())
