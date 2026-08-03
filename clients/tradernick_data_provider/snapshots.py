"""Snapshot-side fluent builders.

Right now exposes ``ScanParquetQuery`` — accumulates wallet-filter steps
(the same ``involving`` / ``sender`` / ``receiver`` + ``exclude_*`` surface
as the read queries) and dispatches them server-side via
``POST /snapshots/scan``.
The server lazily scans the snapshot with ``pl.scan_parquet`` so only
the filtered subset is collected and returned, keeping client RAM
bounded by the result size rather than the full snapshot size.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Optional, TypeVar, Union

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import polars as pl

from datetime import datetime

from ._http import (
    DataProviderHTTPError,
    delete_snapshot,
    list_snapshots,
    list_snapshots_detailed,
    load_parquet_bytes,
)
from ._query import _WalletFilters, _to_timestamp


def _cast_time_ms_utc(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize a ``time`` column to ``Datetime('ms', 'UTC')``. DuckDB-written
    snapshots come back μs+UTC; cache reads come back ms+UTC — cast so both
    join cleanly on the polars side."""
    if 'time' in df.columns:
        dt = df.schema['time']
        if isinstance(dt, pl.Datetime) and (dt.time_unit != 'ms' or dt.time_zone != 'UTC'):
            df = df.with_columns(pl.col('time').cast(pl.Datetime('ms', 'UTC')))
    return df


def _to_datetime(date) -> datetime:
    return datetime.fromisoformat(_to_timestamp(date).replace('Z', '+00:00'))




# Self-type TypeVar for fluent-builder chaining. Equivalent to typing.Self
# (PEP 673) but the explicit self-type form is what jedi/IDE completion
# follows correctly through the mixin/base hierarchy.
_T = TypeVar("_T")

class ScanParquetQuery(_WalletFilters):
    """Lazy-scan a saved snapshot, filtering it server-side.

    Uses the SAME wallet-selection filter surface as the read queries
    (``involving`` / ``sender`` / ``receiver`` + ``_label`` / ``_entity`` /
    ``_category`` / ``_groups`` + ``exclude_*``, all ``str | list[str]``). On a
    scan the server resolves each selection to member addresses and filters the
    snapshot in DuckDB, so category/entity/group filters actually apply here
    (they reduce to an address set).

    Terminal calls:
        ``as_polars()`` → ``pl.DataFrame``
        ``as_pandas()`` → ``pd.DataFrame``
        ``as_parquet(new_key)`` → server-side write under ``new_key``
    """

    def __init__(self, session: httpx.AsyncClient, base_url: str, key: str,
                 *, since=None, until=None,
                 engine: str = 'duckdb', normalize_addresses=None):
        self._session = session
        self._base_url = base_url
        # Always send engine explicitly. Server treats absence as polars
        # (its own default), but we don't want the client default to leak
        # through silently — safer to be explicit.
        self._body: dict = {"key": key, "engine": engine}
        if since is not None:
            self._body["since"] = _to_timestamp(since)
        if until is not None:
            self._body["until"] = _to_timestamp(until)
        # normalize_addresses: None = auto-detect on the server (default).
        # Forward only when the caller explicitly set a value.
        if normalize_addresses is not None:
            self._body["normalize_addresses"] = bool(normalize_addresses)

    def time_range(self: _T, since, until) -> _T:
        """Set ``[since, until)`` on the chain. Overrides any range
        passed at construction time."""
        self._body["since"] = _to_timestamp(since)
        self._body["until"] = _to_timestamp(until)
        return self

    def min_amount(self: _T, amount: float) -> _T:
        self._body["min_amount"] = amount
        return self

    def max_amount(self: _T, amount: float) -> _T:
        self._body["max_amount"] = amount
        return self

    async def _post(self) -> bytes:
        resp = await self._session.post(
            f"{self._base_url}/snapshots/scan",
            json=self._body,
            timeout=None,
        )
        if not resp.is_success:
            try:
                err = resp.json().get("error", resp.text)
            except Exception:
                err = resp.text
            raise DataProviderHTTPError(resp.status_code, err)
        return resp.content

    async def as_polars(self) -> pl.DataFrame:
        data = await self._post()
        df = pl.read_parquet(io.BytesIO(data))
        # Normalize time precision to ms+UTC to match cache reads
        # (DuckDB snapshots come back at μs+UTC otherwise).
        if 'time' in df.columns:
            dt = df.schema['time']
            if isinstance(dt, pl.Datetime) and (
                dt.time_unit != 'ms' or dt.time_zone != 'UTC'
            ):
                df = df.with_columns(pl.col('time').cast(pl.Datetime('ms', 'UTC')))
        # Server-side filter (DuckDB SELECT WHERE in particular) doesn't
        # guarantee row order across reads. Re-sort here so the result
        # mirrors the canonical time-sorted ordering of the snapshot.
        if 'time' in df.columns:
            df = df.sort('time')
        return df

    async def as_pandas(self) -> pd.DataFrame:
        data = await self._post()
        df = pq.read_table(io.BytesIO(data)).to_pandas()
        if 'time' in df.columns:
            df = df.sort_values('time', ignore_index=True)
        return df

    async def as_parquet(self, new_key: str) -> None:
        """Save the filtered result to a new snapshot, server-side."""
        body = {**self._body, "save_key": new_key}
        resp = await self._session.post(
            f"{self._base_url}/snapshots/scan",
            json=body,
            timeout=None,
        )
        if not resp.is_success:
            try:
                err = resp.json().get("error", resp.text)
            except Exception:
                err = resp.text
            raise DataProviderHTTPError(resp.status_code, err)


class LoadSnapshotQuery:
    """Load a whole saved snapshot (no server-side filter — for that use
    ``client.snapshot.scan``). Optional client-side ``[since, until)`` slice on
    the ``time``/``timestamp`` column.

    Terminal calls:
        ``as_polars()``  → ``pl.DataFrame`` (``time`` cast to ms+UTC)
        ``as_pandas()``  → ``pd.DataFrame``
        ``as_arrow()``   → ``pa.Table``
        ``bytes()``      → raw stored parquet bytes (unfiltered)
    """

    def __init__(self, session: httpx.AsyncClient, base_url: str, key: str,
                 *, since=None, until=None):
        self._session = session
        self._base_url = base_url
        self._key = key
        self._since = _to_datetime(since) if since is not None else None
        self._until = _to_datetime(until) if until is not None else None

    def time_range(self: _T, since, until) -> _T:
        """Set a client-side ``[since, until)`` slice, applied after load."""
        self._since = _to_datetime(since)
        self._until = _to_datetime(until)
        return self

    def _apply_time_filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if self._since is None and self._until is None:
            return df
        col = 'timestamp' if 'timestamp' in df.columns else 'time'
        if col not in df.columns:
            return df
        if self._since is not None:
            df = df.filter(pl.col(col) >= self._since)
        if self._until is not None:
            df = df.filter(pl.col(col) <= self._until)
        return df

    async def bytes(self) -> bytes:
        """Raw stored parquet bytes (the whole file; ``since``/``until`` and the
        ms+UTC time cast are NOT applied here)."""
        return await load_parquet_bytes(self._session, self._base_url, self._key)

    async def as_polars(self) -> pl.DataFrame:
        raw = await load_parquet_bytes(self._session, self._base_url, self._key)
        df = _cast_time_ms_utc(pl.read_parquet(io.BytesIO(raw)))
        return self._apply_time_filter(df)

    async def as_pandas(self) -> pd.DataFrame:
        return (await self.as_polars()).to_pandas()

    async def as_arrow(self) -> pa.Table:
        return (await self.as_polars()).to_arrow()


class SnapshotNamespace:
    """``client.snapshot.*`` — manage saved parquet snapshots.

    - ``list()`` → keys; ``list(detailed=True)`` / ``list_detailed()`` → keys
      with sizes + a roster-wide total.
    - ``load(key)`` → :class:`LoadSnapshotQuery` (``as_polars`` / ``as_pandas`` /
      ``as_arrow`` / ``bytes``).
    - ``scan(key)`` → :class:`ScanParquetQuery` (server-side wallet-filtered read).
    - ``delete(key)`` → hard-remove (no undo).

    Snapshots are *written* by any read query's ``.as_parquet(key)`` terminal
    (available on every provider), so there is no ``save`` here.
    """

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    async def list(self, *, detailed: bool = False):
        """Saved snapshot keys. With ``detailed=True``, return the size payload
        (``{snapshots:[{key,bytes,size,modified}], count, total_bytes,
        total_size}``) instead of a bare key list."""
        if detailed:
            return await list_snapshots_detailed(self._session, self._base_url)
        return await list_snapshots(self._session, self._base_url)

    async def list_detailed(self) -> dict:
        """Saved snapshots with per-file sizes + a roster-wide total. Same as
        ``list(detailed=True)``."""
        return await list_snapshots_detailed(self._session, self._base_url)

    async def delete(self, key: str) -> None:
        """Delete a saved snapshot (hard ``os.remove`` server-side — no undo)."""
        await delete_snapshot(self._session, self._base_url, key)

    def load(self, key: str, *,
             since: Optional[Union[datetime, str, int]] = None,
             until: Optional[Union[datetime, str, int]] = None) -> LoadSnapshotQuery:
        """Load a whole snapshot. Returns a builder — call ``.as_polars()``,
        ``.as_pandas()``, ``.as_arrow()`` or ``.bytes()``."""
        return LoadSnapshotQuery(self._session, self._base_url, key, since=since, until=until)

    def scan(self, key: str, *,
             since: Optional[Union[datetime, str, int]] = None,
             until: Optional[Union[datetime, str, int]] = None,
             engine: str = 'duckdb',
             normalize_addresses: Optional[bool] = None) -> "ScanParquetQuery":
        """Lazy, server-side wallet-filtered read of a snapshot. Returns a
        :class:`ScanParquetQuery`; chain the wallet-selection filters then call
        ``.as_polars()`` / ``.as_pandas()`` / ``.as_parquet(new_key)``."""
        return ScanParquetQuery(
            self._session, self._base_url, key,
            since=since, until=until,
            engine=engine, normalize_addresses=normalize_addresses,
        )
