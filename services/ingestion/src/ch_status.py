"""Persistent per-stream status helpers.

Two tables back this module (see clickhouse/init/01_schema.sql):

  tradernick.ingestion_event_state   — durable {name, enabled} flag the admin
                                       panel writes via /streams/<name>/{start,stop}.
                                       Drives whether the supervisor spawns a
                                       worker for that stream on next iteration.

  tradernick.ingestion_event_status  — heartbeat each worker emits at the end
                                       of every successful (or failed) tick.
                                       Read by /streams to surface last-tick
                                       age, rows/tick, last error, etc.

Both are ReplacingMergeTree on `(name)` so writes collapse to one current row
per stream. Workers call `write_tick(...)`; the supervisor + admin endpoints
call the reader helpers.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from clickhouse import async_client

log = logging.getLogger(__name__)

_STATE_TABLE = "tradernick.ingestion_event_state"
_STATUS_TABLE = "tradernick.ingestion_event_status"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- state (enabled flag) -----------------------------------------------

async def read_state(name: str) -> Optional[bool]:
    """Return the persisted enabled flag for a stream, or None if absent.
    Caller falls back to the registry's `enabled_default`.

    FINAL because the table is RMT — without it a stale row can shadow the
    most recent write until the next merge."""
    ch = await async_client()
    rows = await ch.query(
        f"SELECT enabled FROM {_STATE_TABLE} FINAL WHERE name = {{name:String}}",
        parameters={"name": name},
    )
    if not rows.result_rows:
        return None
    return bool(rows.result_rows[0][0])


async def read_all_state() -> dict[str, bool]:
    """Return {name: enabled} for every name that has a persisted row.
    Names without a row aren't returned (caller falls back to default)."""
    ch = await async_client()
    rows = await ch.query(f"SELECT name, enabled FROM {_STATE_TABLE} FINAL")
    return {r[0]: bool(r[1]) for r in rows.result_rows}


async def set_enabled(name: str, enabled: bool) -> None:
    """Persist the on/off flag. Idempotent — ReplacingMergeTree collapses to
    the latest `modified_at`."""
    ch = await async_client()
    await ch.insert(
        _STATE_TABLE,
        [[name, enabled, _now()]],
        column_names=["name", "enabled", "modified_at"],
    )


# ---- status (per-tick heartbeat) -----------------------------------------

# In-process running totals so a worker doesn't have to query CH to know its
# own tick count / total rows. Keyed by stream name (in case a single process
# ever serves multiple — currently 1:1).
_TICK_COUNTERS: dict[str, dict] = {}


def _ensure_counter(name: str) -> dict:
    c = _TICK_COUNTERS.get(name)
    if c is None:
        c = {
            "started_at": _now(), "pid": os.getpid(),
            "ticks": 0, "total_rows": 0, "crash_count": 0,
        }
        _TICK_COUNTERS[name] = c
    return c


_COLUMN_NAMES = [
    "name", "pid", "started_at", "last_tick_at",
    "last_rows", "total_rows_since_start", "tick_count",
    "last_error", "last_error_at", "last_success_at", "crash_count",
    "last_live_duration_s", "last_sweep_duration_s",
    "tick_in_progress", "tick_started_at", "updated_at",
]


def _build_row(c: dict, name: str, *, now: datetime, **overrides) -> list:
    """Build a status row from a counter dict + overrides. Centralizes the
    per-field defaults so callers don't have to enumerate every column."""
    base = {
        "name": name,
        "pid": c.get("pid", os.getpid()),
        "started_at": c.get("started_at", now),
        "last_tick_at": c.get("last_tick_at") or now,
        "last_rows": int(c.get("last_rows", 0)),
        "total_rows_since_start": int(c.get("total_rows", 0)),
        "tick_count": int(c.get("ticks", 0)),
        "last_error": c.get("last_error"),
        "last_error_at": c.get("last_error_at"),
        "last_success_at": c.get("last_success_at"),
        "crash_count": int(c.get("crash_count", 0)),
        "last_live_duration_s": c.get("last_live_duration_s"),
        "last_sweep_duration_s": c.get("last_sweep_duration_s"),
        "tick_in_progress": 0,
        "tick_started_at": c.get("tick_started_at"),
        "updated_at": now,
    }
    base.update(overrides)
    return [base[k] for k in _COLUMN_NAMES]


async def bootstrap_counter(name: str) -> None:
    """Seed _TICK_COUNTERS[name] from CH so tick_count / total_rows /
    crash_count survive subprocess restarts. Called once at worker startup
    BEFORE the first write_tick/write_tick_start.

    Without this, every subprocess restart resets tick_count to 0 — the user
    can't tell whether '5 ticks' means a stream just started or has been
    crashing every minute for an hour. Persisting these means the dashboard
    shows the lifetime numbers (which is what 'after-the-fact logs' implies)."""
    try:
        ch = await async_client()
        rows = await ch.query(
            f"SELECT total_rows_since_start, tick_count, crash_count, "
            f"last_error, last_error_at, last_success_at, last_tick_at, last_rows, "
            f"last_live_duration_s, last_sweep_duration_s "
            f"FROM {_STATUS_TABLE} FINAL WHERE name = {{name:String}}",
            parameters={"name": name},
        )
        if not rows.result_rows:
            return
        r = rows.result_rows[0]
        c = _ensure_counter(name)
        c["total_rows"] = int(r[0]) if r[0] is not None else 0
        c["ticks"] = int(r[1]) if r[1] is not None else 0
        c["crash_count"] = int(r[2]) if r[2] is not None else 0
        c["last_error"] = r[3]
        c["last_error_at"] = r[4]
        c["last_success_at"] = r[5]
        c["last_tick_at"] = r[6]
        c["last_rows"] = int(r[7]) if r[7] is not None else 0
        c["last_live_duration_s"] = float(r[8]) if r[8] is not None else None
        c["last_sweep_duration_s"] = float(r[9]) if r[9] is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("ch_status bootstrap_counter(%s) failed: %s", name, exc)


async def write_tick_start(name: str) -> None:
    """Mark a stream as actively fetching. Called at the very top of each
    live-loop iteration, before the DeFiStream call(s). Pairs with
    write_tick() at the end of the iteration which flips the flag back to 0.

    Writes a full row (RMT(updated_at) collapses on `name`) preserving the
    last known last_tick_at/last_rows/etc. so the dashboard doesn't lose
    those columns while the new tick is in flight."""
    if name not in _TICK_COUNTERS:
        await bootstrap_counter(name)
    c = _ensure_counter(name)
    now = _now()
    c["tick_started_at"] = now
    row = _build_row(c, name, now=now, tick_in_progress=1, tick_started_at=now)
    try:
        ch = await async_client()
        await ch.insert(_STATUS_TABLE, [row], column_names=_COLUMN_NAMES)
    except Exception as exc:  # noqa: BLE001
        log.warning("ch_status write_tick_start(%s) failed: %s", name, exc)


async def write_tick(
    name: str,
    rows: int,
    error: Optional[str] = None,
    duration_s: Optional[float] = None,
) -> None:
    """Called by a stream worker after each poll iteration.

    `rows` is the rows ingested this tick (0 is fine — emitting empty ticks
    keeps the last_tick_at advancing so the admin UI doesn't show the stream
    as stalled). `error` is the str(exception) message when the tick failed;
    pass None on success to leave last_error untouched … actually no, we
    always rewrite the row so the previous error stays in last_error until
    a NEW error comes in. ReplacingMergeTree(updated_at) collapses to the
    latest tuple, so write the full row each time.

    `duration_s` is the wall-clock duration of this LIVE tick (seconds).
    Stored separately from sweep duration so the dashboard can show both.

    Also flips tick_in_progress back to 0 (set to 1 by write_tick_start)."""
    if name not in _TICK_COUNTERS:
        await bootstrap_counter(name)
    c = _ensure_counter(name)
    if error is None:
        c["ticks"] += 1
        c["total_rows"] += int(rows)
    now = _now()
    if error is not None:
        c["last_error"] = error
        c["last_error_at"] = now
    c["last_tick_at"] = now
    c["last_rows"] = int(rows)
    if error is None:
        c["last_success_at"] = now
    if duration_s is not None:
        c["last_live_duration_s"] = float(duration_s)
    row = _build_row(
        c, name, now=now,
        last_tick_at=now, last_rows=int(rows),
        tick_in_progress=0,
    )
    try:
        ch = await async_client()
        await ch.insert(_STATUS_TABLE, [row], column_names=_COLUMN_NAMES)
    except Exception as exc:  # noqa: BLE001
        # Status writes must never crash the worker. CH unreachable, malformed
        # value, etc. — log and move on. Live data flow is more important than
        # the heartbeat row.
        log.warning("ch_status write_tick(%s) failed: %s", name, exc)


async def write_sweep(
    name: str,
    duration_s: float,
    rows: int = 0,
    error: Optional[str] = None,
) -> None:
    """Sweep finished — record its wall-clock duration. Does NOT count toward
    `tick_count` (that's live ticks only) or `total_rows` (sweep rows are
    deduplicated against live by the RMT table, so counting them as 'new
    rows' would be misleading). If sweep failed, surface the error."""
    if name not in _TICK_COUNTERS:
        await bootstrap_counter(name)
    c = _ensure_counter(name)
    now = _now()
    if error is not None:
        c["last_error"] = error
        c["last_error_at"] = now
    c["last_sweep_duration_s"] = float(duration_s)
    row = _build_row(c, name, now=now, tick_in_progress=0)
    try:
        ch = await async_client()
        await ch.insert(_STATUS_TABLE, [row], column_names=_COLUMN_NAMES)
    except Exception as exc:  # noqa: BLE001
        log.warning("ch_status write_sweep(%s) failed: %s", name, exc)


async def _read_counter_from_ch(name: str) -> Optional[dict]:
    """Internal: read the current CH row for `name` and return a counter dict.
    Used by write_crash + reset_crash_count which run in processes that may
    not have seen this name in _TICK_COUNTERS (e.g. the supervisor)."""
    ch = await async_client()
    rows = await ch.query(
        f"SELECT pid, started_at, last_tick_at, last_rows, "
        f"total_rows_since_start, tick_count, last_error, last_error_at, "
        f"last_success_at, crash_count, last_live_duration_s, "
        f"last_sweep_duration_s, tick_started_at "
        f"FROM {_STATUS_TABLE} FINAL WHERE name = {{name:String}}",
        parameters={"name": name},
    )
    if not rows.result_rows:
        return None
    r = rows.result_rows[0]
    return {
        "pid": int(r[0]) if r[0] is not None else 0,
        "started_at": r[1] or _now(),
        "last_tick_at": r[2],
        "last_rows": int(r[3]) if r[3] is not None else 0,
        "total_rows": int(r[4]) if r[4] is not None else 0,
        "ticks": int(r[5]) if r[5] is not None else 0,
        "last_error": r[6],
        "last_error_at": r[7],
        "last_success_at": r[8],
        "crash_count": int(r[9]) if r[9] is not None else 0,
        "last_live_duration_s": float(r[10]) if r[10] is not None else None,
        "last_sweep_duration_s": float(r[11]) if r[11] is not None else None,
        "tick_started_at": r[12],
    }


async def write_crash(name: str, error_text: Optional[str]) -> None:
    """Called by the supervisor (NOT by the worker) when a subprocess exits
    non-zero. Reads the current CH row, increments crash_count, writes the
    last_error (captured stderr tail) back. Idempotent: each crash bumps the
    count by exactly one because we read-modify-write under RMT(updated_at).

    Caller passes a stderr tail (last ~10 lines) so a startup-crash that never
    reaches a tick still surfaces an error in the dashboard."""
    try:
        c = await _read_counter_from_ch(name) or {
            "pid": 0, "started_at": _now(), "ticks": 0, "total_rows": 0,
            "crash_count": 0,
        }
        c["crash_count"] = int(c.get("crash_count", 0)) + 1
        now = _now()
        if error_text:
            c["last_error"] = error_text
            c["last_error_at"] = now
        row = _build_row(c, name, now=now, tick_in_progress=0)
        ch = await async_client()
        await ch.insert(_STATUS_TABLE, [row], column_names=_COLUMN_NAMES)
    except Exception as exc:  # noqa: BLE001
        log.warning("ch_status write_crash(%s) failed: %s", name, exc)


async def reset_crash_count(name: str) -> None:
    """Set crash_count back to 0 for `name`. Called from the admin panel /
    /streams/<name>/reset-crash-count endpoint."""
    try:
        c = await _read_counter_from_ch(name)
        if c is None:
            return
        c["crash_count"] = 0
        row = _build_row(c, name, now=_now(), tick_in_progress=0)
        ch = await async_client()
        await ch.insert(_STATUS_TABLE, [row], column_names=_COLUMN_NAMES)
        local = _TICK_COUNTERS.get(name)
        if local is not None:
            local["crash_count"] = 0
    except Exception as exc:  # noqa: BLE001
        log.warning("ch_status reset_crash_count(%s) failed: %s", name, exc)


async def read_all_status() -> list[dict]:
    """Return one row per stream that has ever emitted a tick, latest only."""
    ch = await async_client()
    rows = await ch.query(
        f"""
        SELECT
            name, pid, started_at, last_tick_at,
            last_rows, total_rows_since_start, tick_count,
            last_error, last_error_at, last_success_at, crash_count,
            last_live_duration_s, last_sweep_duration_s,
            tick_in_progress, tick_started_at, updated_at
        FROM {_STATUS_TABLE} FINAL
        """
    )
    out: list[dict] = []
    for r in rows.result_rows:
        out.append({
            "name": r[0],
            "pid": int(r[1]) if r[1] is not None else None,
            "started_at": r[2].isoformat() if r[2] else None,
            "last_tick_at": r[3].isoformat() if r[3] else None,
            "last_rows": int(r[4]) if r[4] is not None else 0,
            "total_rows_since_start": int(r[5]) if r[5] is not None else 0,
            "tick_count": int(r[6]) if r[6] is not None else 0,
            "last_error": r[7],
            "last_error_at": r[8].isoformat() if r[8] else None,
            "last_success_at": r[9].isoformat() if r[9] else None,
            "crash_count": int(r[10]) if r[10] is not None else 0,
            "last_live_duration_s": float(r[11]) if r[11] is not None else None,
            "last_sweep_duration_s": float(r[12]) if r[12] is not None else None,
            "tick_in_progress": bool(r[13]) if r[13] is not None else False,
            "tick_started_at": r[14].isoformat() if r[14] else None,
            "updated_at": r[15].isoformat() if r[15] else None,
        })
    return out
