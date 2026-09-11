"""Async ClickHouse access — read-only.

Returns rows as polars DataFrames via the Arrow path. The data_provider
never writes to ingestion tables; this module exposes only SELECT-style
calls (plus the wallet-label DELETE).

get_client() returns a transparent retrying proxy: query_arrow / query /
command reconnect + retry on TRANSIENT connection errors, so a brief
ClickHouse outage (<~1 min — e.g. a container restart to apply a cpuset)
doesn't fail requests or leave a permanently-broken singleton behind.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

import polars as pl
import pyarrow as pa
from clickhouse_connect import get_async_client

_log = logging.getLogger("ch")
_real_client = None
_client_lock = asyncio.Lock()


# Per-query memory policy sent with every data_provider query.
#
# These ride on data_provider's OWN ClickHouse session, so they bound reads
# only — the ingestion services use their own client and their inserts are
# unaffected. That is deliberate: a user-level cap would couple reads to
# writes and let a runaway read fail live ingestion.
#
# Two layers:
#
#  * SPILL (max_bytes_before_external_{sort,group_by}) — ClickHouse defaults
#    both to 0, meaning "never spill, sort/aggregate entirely in RAM". Above
#    this threshold it streams intermediate blocks to /var/lib/clickhouse/tmp
#    (LZ4) instead: slower, but the query COMPLETES. Measured 2026-09-11 over
#    7 days / 2.3M queries, p99.9 memory was 813 MiB, so normal traffic is
#    orders of magnitude below this and never pays the disk round-trip.
#
#  * CAP (max_memory_usage) — the backstop, because spilling only covers sort
#    and group-by. Without it a read can climb toward the server-wide limit:
#    on 2026-09-11 six queries between 08:30 and 12:04 used 98-210 GiB each on
#    a swapless 251 GB host. Past the cap a query dies with
#    MEMORY_LIMIT_EXCEEDED — one failed request instead of a threatened host.
#
# The cap is 2x the spill threshold on ClickHouse's own guidance: merging
# spilled group-by state can roughly double peak usage, so a cap at or near
# the spill point would kill queries exactly as they tried to save themselves.
#
# NB concurrency is NOT bounded by these: N simultaneous reads can each reach
# the cap. At 64 GiB that means two concurrent large reads ~= 128 GiB. Lower
# CH_MAX_QUERY_BYTES if heavy reads start overlapping.
_SPILL_BYTES = int(os.environ.get('CH_SPILL_BYTES', str(32 * 1024 ** 3)))       # 32 GiB
_MAX_QUERY_BYTES = int(os.environ.get('CH_MAX_QUERY_BYTES', str(64 * 1024 ** 3)))  # 64 GiB


def _query_memory_settings() -> dict[str, int]:
    return {
        'max_bytes_before_external_sort': _SPILL_BYTES,
        'max_bytes_before_external_group_by': _SPILL_BYTES,
        'max_memory_usage': _MAX_QUERY_BYTES,
        # Merge each partition's parts INDEPENDENTLY for FINAL rather than
        # across the whole selection. Measured on a 2.7-year btc transfers
        # FINAL scan (sum(amount), so no result-set effects):
        #     off: 170.1s / 505 MiB      on: 19.9s / 608 MiB
        # i.e. ~8.5x FASTER for marginally more memory. It is a LATENCY win,
        # not a memory one — it does NOT rescue a read that dies on result
        # size (a wide range with a global ORDER BY still materialises the
        # sorted result and will hit max_memory_usage; chunk the range
        # instead).
        #
        # Safe because every partitioned ReplacingMergeTree here partitions on
        # a column that is IN its sorting key (verified across all 98: 80
        # partition on `time`, which is in the sort key; the other 18 are
        # unpartitioned, where this is a no-op). Two rows sharing a sort key
        # therefore share a partition, so duplicates can never span partitions
        # and per-partition collapsing is equivalent. Cross-checked on a month
        # of btc transfers: 18,118,583 rows with and without.
        #
        # If a future table partitions on something NOT derived from its
        # sorting key, this must be revisited — it would then under-collapse.
        'do_not_merge_across_partitions_select_final': 1,
    }


async def _get_real_client():
    global _real_client
    if _real_client is None:
        async with _client_lock:
            if _real_client is None:
                _real_client = await get_async_client(
                    host=os.environ.get('CLICKHOUSE_HOST', 'clickhouse'),
                    port=int(os.environ.get('CLICKHOUSE_PORT', '8123')),
                    username=os.environ.get('CLICKHOUSE_USER', 'tradernick'),
                    password=os.environ.get('CLICKHOUSE_PASSWORD', ''),
                    database=os.environ.get('CLICKHOUSE_DB', 'tradernick'),
                    settings=_query_memory_settings(),
                )
    return _real_client


async def _reset_real_client():
    global _real_client
    c, _real_client = _real_client, None
    if c is not None:
        try:
            await c.close()
        except Exception:  # noqa: BLE001
            pass


_TRANSIENT_HINTS = (
    "connection", "connreset", "connection reset", "refused", "reset by peer",
    "timed out", "timeout", "operational", "network", "broken pipe", "closed",
    "unreachable", "cannot connect", "connecterror", "readtimeout",
    "connecttimeout", "no route to host", "temporarily unavailable",
    "connection aborted", "server disconnected", "remotedisconnected",
    "502", "503", "504", "eof occurred", "not connected",
    # CH cancels in-flight queries as it shuts down for a restart:
    "query was cancelled", "query_was_cancelled", "killed in pending", "code: 394",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)):
        return True
    return any(h in str(exc).lower() for h in _TRANSIENT_HINTS)


_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 6.0, 9.0, 12.0)


async def _call_with_retry(method: str, *args, **kwargs):
    last: Exception | None = None
    for i, delay in enumerate(_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            ch = await _get_real_client()
            return await getattr(ch, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc):
                raise
            _log.warning("CH %s transient failure (attempt %d/%d): %s — reconnecting",
                         method, i + 1, len(_RETRY_DELAYS), str(exc)[:200])
            await _reset_real_client()
    raise last  # type: ignore[misc]


class _RetryingAsyncClient:
    async def query_arrow(self, *a, **k):
        return await _call_with_retry("query_arrow", *a, **k)

    async def query(self, *a, **k):
        return await _call_with_retry("query", *a, **k)

    async def command(self, *a, **k):
        return await _call_with_retry("command", *a, **k)

    async def close(self):
        await _reset_real_client()

    def __getattr__(self, name):
        real = _real_client
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


_proxy = _RetryingAsyncClient()


async def get_client():
    await _get_real_client()
    return _proxy


async def query_polars(sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """Run a SELECT and return a polars DataFrame.

    Uses CH's arrow stream so the round-trip stays columnar end-to-end.
    Empty results return an empty DataFrame; callers that need a schema
    on the empty path should pass an explicit empty template downstream.
    """
    client = await get_client()
    table: pa.Table = await client.query_arrow(sql, parameters=params or {})
    if table is None or table.num_rows == 0:
        if table is not None and table.schema is not None:
            return pl.from_arrow(table.schema.empty_table())
        return pl.DataFrame()
    return pl.from_arrow(table)


# Rows to coalesce per parquet row group when streaming a save. Each CH Arrow
# block is only ~65k rows; writing one row group per block yields thousands of
# tiny groups (poor read locality + weaker zstd). We buffer blocks up to this
# many rows, then write one large row group. Peak memory ≈ this many rows of
# Arrow (~130 B/row for fills → ~1.3 GiB at 10M), which the box has to spare.
# Tunable via env; the whole result never lands in RAM regardless.
STREAM_ROWS_PER_GROUP = int(os.environ.get("SNAPSHOT_STREAM_ROWS_PER_GROUP", "10000000"))


# ---------------------------------------------------------------------------
# Range chunking
#
# A wide read dies on RESULT SIZE, which neither spilling nor per-partition
# FINAL addresses: a global `ORDER BY time` cannot emit its first row until the
# whole sort completes, so the sorted result is materialised in full. The
# 3.7-year btc transfers pull (1.06B rows) hits max_memory_usage at 64 GiB with
# ExternalSortWritePart=0 — nothing spillable is involved.
#
# Splitting the range fixes it. Each chunk is an ordinary small read; row
# groups are appended to ONE parquet, so the client sees a single normal
# response. Chunks are MONTH-ALIGNED because every table here is
# PARTITION BY toYYYYMM(time): a chunk then lands in exactly one partition,
# which also minimises the FINAL merge and maximises partition pruning.
#
# Ingestion already learned this lesson — see binance_raw_trades.FETCH_CHUNK,
# added after an unchunked window "left ~20 GiB high-water-marked in swap".
# ---------------------------------------------------------------------------

CHUNK_THRESHOLD_DAYS = int(os.environ.get('READ_CHUNK_THRESHOLD_DAYS', '32'))

# Constructs that make "split the range and concatenate" NOT equivalent to the
# single query. GROUP BY / OVER aggregate across the range, so per-chunk
# evaluation yields different rows. LIMIT would be applied per chunk. WITH may
# hide any of the above in a CTE. Conservative on purpose: a false negative
# only costs the old behaviour, a false positive returns WRONG DATA.
_UNCHUNKABLE = re.compile(r'\bGROUP\s+BY\b|\bOVER\s*\(|\bLIMIT\b|\bWITH\b', re.I)

# Concatenating chunks preserves global ordering only when the sort leads with
# `time` (chunks are emitted in ascending time order).
_ORDER_BY_TIME = re.compile(r'ORDER\s+BY\s+(?:[a-z]\.)?time\b', re.I)


def _chunk_ranges(sql: str, params: dict | None) -> list[tuple[str, str]] | None:
    """Month-aligned [since, until) sub-ranges, or None to run the query whole.

    None is returned whenever chunking would be unsafe OR pointless — a query
    without both bounds, an aggregating shape, a sort that is not time-leading,
    or a range short enough that one query is fine."""
    if not params or 'since' not in params or 'until' not in params:
        return None
    if _UNCHUNKABLE.search(sql) or not _ORDER_BY_TIME.search(sql):
        return None
    try:
        since = datetime.fromisoformat(str(params['since']).replace('Z', ''))
        until = datetime.fromisoformat(str(params['until']).replace('Z', ''))
    except (TypeError, ValueError):
        return None
    if until <= since or (until - since) <= timedelta(days=CHUNK_THRESHOLD_DAYS):
        return None
    out: list[tuple[str, str]] = []
    cur = since
    while cur < until:
        nxt = (cur.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
               + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = min(nxt, until)
        out.append((cur.isoformat(sep='T'), end.isoformat(sep='T')))
        cur = end
    return out or None


async def stream_query_to_parquet(
    sql: str,
    params: dict[str, Any] | None,
    dst_path: str,
    *,
    empty_df: "pl.DataFrame | None" = None,
    compression: str = "zstd",
    rows_per_group: int | None = None,
) -> int:
    """Stream a SELECT straight to a parquet file, coalescing CH Arrow blocks
    into ~``rows_per_group``-row row groups, so peak memory is bounded to one
    group rather than the whole result. This is the memory-safe path for saving
    large snapshots — a full ``query_arrow`` would materialize the entire result
    (hundreds of millions of rows → tens of GiB) in RAM before a byte is written.

    Returns the row count written. On an empty result, writes ``empty_df`` (to
    preserve the stable snapshot schema) or a 0-row file from the stream schema.
    Writes to ``dst_path + '.tmp'`` and atomically renames on success so a
    failure never leaves a half-written snapshot at the real key."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    target = rows_per_group if rows_per_group and rows_per_group > 0 else STREAM_ROWS_PER_GROUP
    client = await _get_real_client()  # streaming can't be retried mid-flight
    tmp = dst_path + ".tmp"
    writer: "pq.ParquetWriter | None" = None
    total = 0
    buf: list = []
    buf_rows = 0

    def _flush():
        nonlocal writer, total, buf, buf_rows
        if not buf:
            return
        tbl = pa.Table.from_batches(buf)
        if writer is None:
            writer = pq.ParquetWriter(tmp, tbl.schema, compression=compression)
        # one row group per flushed table (row_group_size ≥ its length)
        writer.write_table(tbl, row_group_size=tbl.num_rows)
        total += tbl.num_rows
        buf = []
        buf_rows = 0

    # One pass when the range is narrow or the shape is not chunk-safe;
    # otherwise a month-aligned sequence of small reads appended into the SAME
    # writer, so the caller still gets one parquet with one schema. Chunks run
    # in ascending time order and each is internally sorted, so concatenation
    # reproduces the single query's global ordering exactly.
    chunks = _chunk_ranges(sql, params)
    if chunks:
        _log.info("chunked read: %d month-aligned chunks over %s..%s",
                  len(chunks), chunks[0][0], chunks[-1][1])
    passes = ([dict(params or {}, since=cs, until=cu) for cs, cu in chunks]
              if chunks else [params or {}])
    try:
        for i, p_i in enumerate(passes):
            ctx = await client.query_arrow_stream(sql, parameters=p_i)
            async with ctx as reader:
                async for batch in reader:
                    buf.append(batch)
                    buf_rows += batch.num_rows
                    if buf_rows >= target:
                        _flush()
            if chunks:
                # Flush at each chunk boundary: bounds peak memory to one
                # chunk's tail and keeps row groups from straddling months.
                _flush()
                _log.debug("chunk %d/%d done (%s..%s) rows=%d",
                           i + 1, len(chunks), p_i['since'], p_i['until'], total)
        _flush()
        if writer is None:
            # No rows streamed — write a schema-stable empty parquet.
            if empty_df is not None:
                empty_df.write_parquet(tmp, compression=compression)
            else:
                pq.write_table(pa.table({}), tmp, compression=compression)
        else:
            writer.close()
            writer = None
        os.replace(tmp, dst_path)
        return total
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
