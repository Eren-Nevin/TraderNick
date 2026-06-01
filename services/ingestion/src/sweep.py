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


def sweep_since(*, now: datetime, sweep_cadence_seconds: float, last_seen: datetime | None) -> datetime:
    """Pick the `since` boundary for one sweep fire.

    Returns the *earlier* of:
      - now - sweep_cadence  (minimum lookback so we always cover the full
                              sweep interval, even when the table is fresh)
      - last_seen - 5min     (gap floor: if the last inserted row is older
                              than one sweep cadence, fetch all the way back
                              to it, plus 5 min safety)

    Examples (sweep_cadence = 60min):
      last_seen 30 min ago  →  since = now - 60min
      last_seen 100 min ago →  since = now - 105min
      last_seen None        →  since = now - 60min
    """
    minimum_since = now - timedelta(seconds=sweep_cadence_seconds)
    if last_seen is None:
        return minimum_since
    gap_since = last_seen - SWEEP_SAFETY_OVERLAP
    return min(minimum_since, gap_since)
