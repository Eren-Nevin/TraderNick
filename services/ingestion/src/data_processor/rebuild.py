"""Partition rebuild primitive.

`build_partition(spec, partition_id)` is the atomic unit shared by both
the live worker and the backfill subprocess. The contract:

  1. Compute the partition window [start, end) from `spec.partition_grain`
     and `partition_id`. Hourly grain id is 'YYYY-MM-DD-HH', daily is
     'YYYY-MM-DD'.
  2. Acquire the cross-process lock for (spec.name, partition_id). On
     miss the function returns 0 — caller treats it as "another worker
     has this partition" and moves on.
  3. CREATE TABLE <staging> AS <spec.target_table>. The `AS <table>`
     form (no SELECT) clones engine, ORDER BY, partition key, and TTL,
     producing an empty table compatible with REPLACE PARTITION FROM.
  4. INSERT INTO <staging> <spec.rebuild_sql with WHERE bounding the
     window on spec.source_time_col>. Reads source FINAL so the result
     is exact regardless of how many times each source row was inserted.
  5. ALTER TABLE <spec.target_table> REPLACE PARTITION '<partition_id>'
     FROM <staging>. Atomic per-partition swap; readers never see a
     mid-rebuild empty bucket.
  6. DROP TABLE <staging>.
  7. Release the lock.

The staging table name embeds the materializer name + partition id so two
concurrent backfill processes for different partitions don't collide on
the staging name. (The lock would prevent the SAME-partition collision,
but cross-partition concurrency inside a single backfill subprocess is
fine and benefits from the safety.)
"""
from __future__ import annotations

import logging
import os
import re
import time as _time
from datetime import datetime, timedelta

from clickhouse import async_client

from . import locks
from .registry import MaterializerSpec

log = logging.getLogger(__name__)


# Partition id parsing — strict on shape so we never inject an arbitrary
# string into an ALTER TABLE … PARTITION '…' clause. The id format matches
# the literal value of the target table's PARTITION BY expression, which
# is what REPLACE PARTITION '<literal>' expects:
#
#   daily  (PARTITION BY toDate(<bucket>))      → '2026-06-10'
#   hourly (PARTITION BY toStartOfHour(<time>)) → '2026-06-10 14:00:00'
#
# See scripts/migrate_derived_partitions.py for the table-side definitions.
_HOUR_ID_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):00:00$")
_DAY_ID_RE  = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def partition_window(spec: MaterializerSpec, partition_id: str) -> tuple[datetime, datetime]:
    """Translate a partition id into the [start, end) datetime window the
    WHERE clause will bind. End is exclusive.

    Raises ValueError on a malformed id — the caller (live tier or
    backfill) is responsible for only passing ids it generated.
    """
    if spec.partition_grain == "hour":
        m = _HOUR_ID_RE.match(partition_id)
        if not m:
            raise ValueError(f"invalid hourly partition_id {partition_id!r}")
        y, mo, d, h = map(int, m.groups())
        start = datetime(y, mo, d, h, 0, 0)
        return start, start + timedelta(hours=1)
    if spec.partition_grain == "day":
        m = _DAY_ID_RE.match(partition_id)
        if not m:
            raise ValueError(f"invalid daily partition_id {partition_id!r}")
        y, mo, d = map(int, m.groups())
        start = datetime(y, mo, d, 0, 0, 0)
        return start, start + timedelta(days=1)
    raise ValueError(f"unknown partition_grain {spec.partition_grain!r}")


def partition_id_for(spec: MaterializerSpec, dt: datetime) -> str:
    """Inverse of partition_window — used by live + backfill to enumerate
    the partitions covered by a given window."""
    if spec.partition_grain == "hour":
        return dt.strftime("%Y-%m-%d %H:00:00")
    if spec.partition_grain == "day":
        return dt.strftime("%Y-%m-%d")
    raise ValueError(f"unknown partition_grain {spec.partition_grain!r}")


def partition_ids_in_window(
    spec: MaterializerSpec, since: datetime, until: datetime
) -> list[str]:
    """All partition ids whose window overlaps [since, until). Caller's
    bounds are inclusive-since / exclusive-until in source-time terms; we
    expand to whole partitions because REPLACE PARTITION only operates on
    full partitions.
    """
    if since >= until:
        return []
    step = timedelta(hours=1) if spec.partition_grain == "hour" else timedelta(days=1)
    if spec.partition_grain == "hour":
        cursor = since.replace(minute=0, second=0, microsecond=0)
    else:
        cursor = since.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[str] = []
    while cursor < until:
        out.append(partition_id_for(spec, cursor))
        cursor += step
    return out


_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")


def _staging_name(spec: MaterializerSpec, partition_id: str) -> str:
    """Per-(materializer, partition, pid) staging table name. The pid
    suffix lets one process safely abandon a partial staging on crash —
    the next run uses a different name and the leftover is cleaned up
    by the start-of-tick sweep (see live.py).

    Identifier-safe: partition_id can contain spaces/colons (hourly
    grain) so we strip everything but [A-Za-z0-9] when building the
    name."""
    short = _SANITIZE_RE.sub("_", partition_id).strip("_")
    return f"{spec.target_table}_staging_{short}_{os.getpid()}"


def _sql_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def build_partition(
    spec: MaterializerSpec,
    partition_id: str,
    *,
    skip_if_locked: bool = True,
) -> dict:
    """Rebuild exactly one partition of `spec.target_table`. Returns a
    small result dict suitable for logging / progress accounting:

       {"materializer", "partition_id", "skipped": bool, "duration_s",
        "rows_in_target": int | None}

    `skip_if_locked` controls whether a busy lock makes us no-op (True,
    the live tier wants this) or raise (False, a backfill caller may
    prefer to fail loudly).
    """
    t0 = _time.monotonic()
    start, end = partition_window(spec, partition_id)
    held = await locks.try_acquire(spec.name, partition_id)
    if not held:
        if skip_if_locked:
            log.info("%s/%s: skip (lock held)", spec.name, partition_id)
            return {
                "materializer": spec.name,
                "partition_id": partition_id,
                "skipped": True,
                "duration_s": 0.0,
                "rows_in_target": None,
            }
        raise RuntimeError(f"lock held for {spec.name}/{partition_id}")

    staging = _staging_name(spec, partition_id)
    ch = await async_client()
    try:
        # Drop any leftover staging table from a previous crashed run. The
        # name embeds pid so collisions are rare; idempotent regardless.
        await ch.command(f"DROP TABLE IF EXISTS {staging}")

        # Clone the target's full schema/engine/ORDER BY/PARTITION BY/TTL.
        await ch.command(f"CREATE TABLE {staging} AS {spec.target_table}")

        where_clause = (
            f"WHERE {spec.source_time_col} >= toDateTime('{_sql_dt(start)}') "
            f"  AND {spec.source_time_col} <  toDateTime('{_sql_dt(end)}')"
        )
        # The SELECT body in registry.py ends at GROUP BY <...>. We need
        # to splice WHERE between the FROM-clause's FINAL and the
        # GROUP BY / ARRAY JOIN. Easier: wrap the SELECT as a subquery
        # with the WHERE applied to a column it produces — but that
        # would break the ARRAY JOIN fan-out (one source row → N
        # classified buckets) used by exchange_flow. So we splice into
        # the spec.rebuild_sql by parsing for ' GROUP BY ' and inserting
        # the WHERE clause before it; we rely on every spec's SELECT
        # having exactly one top-level GROUP BY clause.
        select_sql = _splice_where(spec.rebuild_sql, where_clause)
        await ch.command(
            f"INSERT INTO {staging} {select_sql} "
            f"SETTINGS max_execution_time = 1800"
        )

        await ch.command(
            f"ALTER TABLE {spec.target_table} "
            f"REPLACE PARTITION '{partition_id}' FROM {staging}"
        )

        # Count rows that just landed in the target's partition. We can't
        # use `_partition_id` (which is CH's internal hash, e.g. the Unix
        # epoch for a `toStartOfHour(time)` expression) — pass the user-
        # form value via `system.parts.partition` instead so the lookup
        # matches the same string we used in REPLACE PARTITION.
        db, tbl = spec.target_table.split(".", 1)
        rows = await ch.query(
            "SELECT coalesce(sum(rows), 0) FROM system.parts "
            "WHERE database = {db:String} AND table = {tbl:String} "
            "  AND partition = {p:String} AND active",
            parameters={"db": db, "tbl": tbl, "p": partition_id},
        )
        n = int(rows.result_rows[0][0]) if rows.result_rows else 0
    finally:
        try:
            await ch.command(f"DROP TABLE IF EXISTS {staging}")
        except Exception:  # noqa: BLE001
            log.exception("%s/%s: staging drop failed", spec.name, partition_id)
        await locks.release(spec.name, partition_id)

    dt = _time.monotonic() - t0
    log.info("%s/%s: rebuilt rows=%d in %.2fs", spec.name, partition_id, n, dt)
    return {
        "materializer": spec.name,
        "partition_id": partition_id,
        "skipped": False,
        "duration_s": dt,
        "rows_in_target": n,
    }


def _splice_where(select_sql: str, where_clause: str) -> str:
    """Insert `where_clause` between the SELECT body's source clause and
    its GROUP BY. Every registry entry's SELECT has the shape
        SELECT … FROM <source> FINAL [ARRAY JOIN …] GROUP BY …
    and may also have a WHERE filtering for `dir IN (…)`. We splice
    before GROUP BY, AND-merging with any existing WHERE.
    """
    upper = select_sql.upper()
    gb_idx = upper.rfind("GROUP BY")
    if gb_idx < 0:
        raise ValueError("rebuild_sql missing GROUP BY — cannot splice WHERE")
    head = select_sql[:gb_idx].rstrip()
    tail = select_sql[gb_idx:]
    where_idx = head.upper().rfind("WHERE")
    if where_idx >= 0:
        # AND-merge with the existing WHERE.
        existing = head[where_idx + len("WHERE"):]
        head = head[:where_idx]
        merged = f"WHERE ({existing.strip()}) AND ({where_clause[len('WHERE '):]})"
        return f"{head} {merged}\n{tail}"
    return f"{head}\n{where_clause}\n{tail}"
