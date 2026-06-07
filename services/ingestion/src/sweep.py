"""Sweep loop — replaces the old startup-only `gap_fill_task` pattern.

Each per-event stream runs TWO concurrent timers in its worker process:

  live_loop()   fires every `cadence_s`  — fetches a small overlap window
                [now - LIVE_OVERLAP, now]. Logs and skips on per-tick error;
                never short-circuits the loop.

  sweep_loop()  fires every `cadence_s * SWEEP_MULTIPLIER` (default 10×) —
                fetches a long-tail window [since, now] where `since` is
                the *earlier* of (now - sweep_cadence) and (last_seen - 5min).
                When the table is empty, last_seen is None and we just use
                the cadence floor. One DeFiStream call per fire — no chunking.

Each loop applies its own random first-fire jitter (live: 60-120s,
sweep: 300-600s) so a fleet of ~80 workers spawning together don't all
fire on the same second. After the first jittered fire each loop runs on
a clean fixed cadence; because each worker picked an independent random
offset, the fleet phase stays uniformly distributed forever (until the
worker dies, in which case the supervisor respawns it and it picks a
fresh random offset).

The supervisor's `_STARTUP_JITTER_S` is now 0 — all jitter lives here.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("sweep")

# How many live ticks make one sweep tick. Sweep cadence = live cadence * this.
SWEEP_MULTIPLIER = 10

# Safety buffer when sweeping back to last_seen — ReplacingMergeTree dedupes
# anything we re-fetch, so a few minutes of overlap is free.
SWEEP_SAFETY_OVERLAP = timedelta(minutes=5)

# Live tick overlap. Sweep handles anything bigger so this can stay small.
LIVE_OVERLAP = timedelta(minutes=5)


def live_jitter_s(cadence_s: float) -> float:
    """Uniform(0, cadence_s) — full-width random phase. Spreads N concurrent
    workers uniformly across their cadence window forever, not just on the
    first fire. (If jitter < cadence, the cluster stays cluster-shaped after
    the first fire because all subsequent ticks happen at first_fire + k*cadence.)"""
    return random.uniform(0.0, cadence_s)


def sweep_jitter_s(sweep_cadence_s: float) -> float:
    """Uniform(0, sweep_cadence_s) — same full-width principle as live jitter,
    just on the sweep timeline."""
    return random.uniform(0.0, sweep_cadence_s)


def sweep_cadence_s(live_cadence_s: float) -> float:
    return live_cadence_s * SWEEP_MULTIPLIER


def sweep_since(
    *,
    now: datetime,
    sweep_cadence_seconds: float,
    last_seen: datetime | None,
    max_window_seconds: float | None = None,
    stream_name: str | None = None,
) -> datetime:
    """Pick the `since` boundary for one sweep fire.

    Returns the *earlier* of:
      - now - sweep_cadence  (minimum lookback so we always cover the full
                              sweep interval, even when the table is fresh)
      - last_seen - 5min     (gap floor: if the last inserted row is older
                              than one sweep cadence, fetch all the way back
                              to it, plus 5 min safety)

    `max_window_seconds` caps the result at `now - max_window_seconds` —
    the upstream API's per-request range limit (e.g. 7d for binance raw
    trades, 31d for binance OHLCV). Without it, a single stale per-token
    watermark (one token that hasn't received data in months — typically
    DeFiStream-side delisting/gap) makes every sweep tick throw an
    out-of-range error and the live token cohort never gets swept.
    With the cap, the sweep covers as much as the API allows; the deeper
    gap requires a targeted backfill to fix.

    Examples (sweep_cadence = 60min, no cap):
      last_seen 30 min ago  →  since = now - 60min
      last_seen 100 min ago →  since = now - 105min
      last_seen None        →  since = now - 60min

    With cap = 7 days, last_seen = 100 days ago → since = now - 7d, and
    a WARNING is logged so the deeper gap is visible.
    """
    minimum_since = now - timedelta(seconds=sweep_cadence_seconds)
    if last_seen is None:
        since = minimum_since
    else:
        gap_since = last_seen - SWEEP_SAFETY_OVERLAP
        since = min(minimum_since, gap_since)
    if max_window_seconds is not None:
        cap = now - timedelta(seconds=max_window_seconds)
        if since < cap:
            log.warning(
                "%s sweep window capped: wanted since=%s (last_seen=%s) "
                "but API max_window=%.0fs → using since=%s. Deeper gap "
                "needs a targeted backfill.",
                stream_name or "sweep", since, last_seen,
                max_window_seconds, cap,
            )
            return cap
    return since
