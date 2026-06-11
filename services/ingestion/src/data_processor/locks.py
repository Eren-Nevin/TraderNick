"""Cross-process per-(materializer, partition) lock.

Backed by tradernick.materializer_locks (see clickhouse/init/02_materializer_locks.sql).
Acquire-or-skip semantics let the recent and sweep tiers co-exist without
overlapping work, and let a backfill subprocess skip partitions the live
worker is currently rebuilding.

Race model: two concurrent acquire() calls for the same (materializer,
partition_id) both INSERT a row, then both SELECT FINAL. The
ReplacingMergeTree(acquired_at) collapses to the row with the latest
acquired_at; whichever process's row wins reads back its own pid and
becomes the holder. The loser sees a foreign pid and backs off.

Stale-lock TTL: each row carries an expires_at. The acquire SELECT
ignores rows with expires_at < now() so a crashed process never deadlocks
forever — after the TTL window any caller can re-acquire.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from clickhouse import async_client

log = logging.getLogger(__name__)

_TABLE = "tradernick.materializer_locks"
_DEFAULT_TTL_S = 10 * 60
_HOSTNAME = socket.gethostname()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def try_acquire(
    materializer: str,
    partition_id: str,
    *,
    ttl_s: int = _DEFAULT_TTL_S,
) -> bool:
    """Best-effort lock acquisition.

    Returns True if this process now owns the (materializer, partition_id)
    lock for `ttl_s` seconds; False if another live process already holds
    it. Callers MUST call `release()` on success — the TTL is a backstop,
    not a normal cleanup path.

    The implementation is two writes + one read: INSERT our claim row,
    then SELECT FINAL the current row for the key. If the winning row's
    pid matches ours we hold the lock; otherwise we backed off and should
    not touch the partition.
    """
    ch = await async_client()
    now = _utcnow()
    expires = now + timedelta(seconds=ttl_s)
    pid = os.getpid()
    await ch.insert(
        _TABLE,
        [[materializer, partition_id, pid, _HOSTNAME, now, expires]],
        column_names=["materializer", "partition_id", "owner_pid", "owner_host",
                      "acquired_at", "expires_at"],
    )
    rows = await ch.query(
        f"""
        SELECT owner_pid, owner_host, expires_at
        FROM {_TABLE} FINAL
        WHERE materializer = {{m:String}} AND partition_id = {{p:String}}
        """,
        parameters={"m": materializer, "p": partition_id},
    )
    if not rows.result_rows:
        # Should be unreachable — we just inserted — but treat as failure.
        return False
    held_pid, held_host, held_expires = rows.result_rows[0]
    # Stale row from a crashed predecessor: if its expires_at is in the
    # past our newer row will dominate the next merge anyway, but to keep
    # the read predictable here we treat held-but-expired as held-by-us
    # only when the row's pid/host matches ours.
    if int(held_pid) == pid and held_host == _HOSTNAME:
        return True
    if held_expires < now:
        # Foreign holder expired. Re-insert with our identity (so the
        # ReplacingMergeTree's argmax-by-acquired_at picks us next time).
        await ch.insert(
            _TABLE,
            [[materializer, partition_id, pid, _HOSTNAME, _utcnow(), expires]],
            column_names=["materializer", "partition_id", "owner_pid", "owner_host",
                          "acquired_at", "expires_at"],
        )
        return True
    return False


async def release(materializer: str, partition_id: str) -> None:
    """Drop the lock row. Idempotent — if the row is already gone (e.g.
    a TTL backstop already kicked it) this is a no-op."""
    ch = await async_client()
    try:
        await ch.command(
            f"DELETE FROM {_TABLE} "
            f"WHERE materializer = '{materializer}' "
            f"  AND partition_id = '{partition_id}'"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("lock release(%s, %s) failed: %s", materializer, partition_id, exc)
