"""Live mode entry point: `python -m data_processor.live <stream_name>`.

Single long-running process. One coroutine per (spec, tier) pair — 7
materializers × 2 tiers = 14 coroutines. Each coroutine rebuilds its
trailing window on its own cadence and writes a heartbeat via
`ch_status`.

Tier semantics:

  recent — the last `spec.recent_partitions` trailing partitions, every
           `spec.recent_cadence_s`. Covers the live edit zone.
  sweep  — every partition in the last `spec.sweep_window_days`, every
           `spec.sweep_cadence_s`. Safety net for late writes and a
           periodic re-affirmation of older data; skips partitions held
           by the recent loop via the lock.

All work is serialized within a single coroutine (one partition at a
time) so we never schedule two REPLACE PARTITIONs against the same
target concurrently. Across coroutines we rely on the locks to
guarantee a partition is only being rebuilt by one of them at a time.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import ch_status

from .registry import REGISTRY, MaterializerSpec
from .rebuild import build_partition, partition_id_for, partition_ids_in_window

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [data_processor.live] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _recent_partition_ids(spec: MaterializerSpec, now: datetime) -> list[str]:
    """Last `spec.recent_partitions` partition ids ending at the partition
    that contains `now`. For hourly grain at 14:32 UTC with
    recent_partitions=6 we return [09, 10, 11, 12, 13, 14]."""
    if spec.partition_grain == "hour":
        step = timedelta(hours=1)
        anchor = now.replace(minute=0, second=0, microsecond=0)
    else:
        step = timedelta(days=1)
        anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[str] = []
    for i in range(spec.recent_partitions):
        out.append(partition_id_for(spec, anchor - step * (spec.recent_partitions - 1 - i)))
    return out


def _sweep_partition_ids(spec: MaterializerSpec, now: datetime) -> list[str]:
    since = now - timedelta(days=spec.sweep_window_days)
    return partition_ids_in_window(spec, since, now + timedelta(seconds=1))


async def _recent_loop(spec: MaterializerSpec, stream_name: str):
    """Per-spec recent-tier loop."""
    while True:
        next_fire = time.monotonic() + spec.recent_cadence_s
        rows_total = 0
        err: str | None = None
        t0 = time.monotonic()
        try:
            for pid in _recent_partition_ids(spec, _utcnow()):
                res = await build_partition(spec, pid)
                if not res.get("skipped"):
                    rows_total += int(res.get("rows_in_target") or 0)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"[:1000]
            log.exception("%s recent tier failed: %s", spec.name, exc)
        await ch_status.write_tick(
            stream_name, rows_total, error=err,
            duration_s=time.monotonic() - t0,
        )
        await asyncio.sleep(max(0.0, next_fire - time.monotonic()))


async def _sweep_loop(spec: MaterializerSpec, stream_name: str):
    """Per-spec sweep-tier loop. Skips partitions the recent tier owns
    via the lock; the sleep is long enough that overlap is rare."""
    # Stagger the first sweep so all materializers don't sweep in lockstep.
    await asyncio.sleep(spec.sweep_cadence_s)
    while True:
        next_fire = time.monotonic() + spec.sweep_cadence_s
        err: str | None = None
        t0 = time.monotonic()
        try:
            ids = _sweep_partition_ids(spec, _utcnow())
            log.info("%s sweep: %d partitions", spec.name, len(ids))
            for pid in ids:
                await build_partition(spec, pid)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"[:1000]
            log.exception("%s sweep tier failed: %s", spec.name, exc)
        await ch_status.write_sweep(
            stream_name, duration_s=time.monotonic() - t0, error=err,
        )
        await asyncio.sleep(max(0.0, next_fire - time.monotonic()))


async def main(stream_name: str):
    log.info("data_processor.live up: %d materializers", len(REGISTRY))
    await ch_status.bootstrap_counter(stream_name)
    # Cold-start stagger so a container-wide cold start doesn't put the
    # first wave of CH commands against fresh process / cache state.
    await asyncio.sleep(60)
    tasks = []
    for spec in REGISTRY:
        tasks.append(_recent_loop(spec, stream_name))
        tasks.append(_sweep_loop(spec, stream_name))
    await asyncio.gather(*tasks)


# The supervisor spawns `python -m data_processor.live` without args; we
# hardcode the stream-name here to match the registry entry in
# streams/__init__.py. Same pattern as the legacy
# streams/data_process_exchange_flow.py wrapper this module replaces.
STREAM_NAME = "data_process.processor_live"


if __name__ == "__main__":
    asyncio.run(main(STREAM_NAME))
