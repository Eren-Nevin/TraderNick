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
import os
import tempfile
from typing import TYPE_CHECKING, Optional, TypeVar, Union

import httpx
import pandas as pd
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


def _guard_snapshot_key(key: str) -> str:
    """Reject keys that could escape the snapshot dir server-side (the server
    names a file after the key). Rejects — rather than silently sanitizes — so
    a typo like ``foo/bar`` errors instead of quietly landing somewhere else."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("snapshot key must be a non-empty string")
    if key in ('.', '..') or any(bad in key for bad in ('/', '\\', '..')):
        raise ValueError(
            f"invalid snapshot key {key!r}: must not contain '/', '\\', or '..'")
    return key


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
    (they reduce to an address set). On HL fills snapshots (one ``wallet``
    column, no sender/receiver) ``involving`` matches ``wallet``.

    Fills-oriented filters (no-ops on snapshots lacking the column):
        ``side('buy'|'sell')``            — the ``side`` column (B/A encoding)
        ``min_size`` / ``max_size``       — the base ``size`` column
        ``min_size_notional`` / ``max_size_notional`` — ``size * price``
        ``tokens([...])``                 — case-insensitive ``token`` filter
                                            (fills AND transfer snapshots)

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

    # --- HL fills-oriented filters ------------------------------------------
    # No-ops on snapshots that lack the referenced column (e.g. `side`/`size`
    # on a transfer snapshot) — the server skips a filter whose column is
    # absent, so these are safe to chain on any snapshot.

    def side(self: _T, side: str) -> _T:
        """Keep only ``'buy'`` or ``'sell'`` fills. Matches the ``side`` column
        (mapped to the snapshot's B/A encoding)."""
        self._body["side"] = side
        return self

    def min_size(self: _T, size: float) -> _T:
        """Lower bound on the base ``size`` column."""
        self._body["min_size"] = size
        return self

    def max_size(self: _T, size: float) -> _T:
        """Upper bound on the base ``size`` column."""
        self._body["max_size"] = size
        return self

    def min_size_notional(self: _T, notional: float) -> _T:
        """Lower bound on notional (``size * price``)."""
        self._body["min_size_notional"] = notional
        return self

    def max_size_notional(self: _T, notional: float) -> _T:
        """Upper bound on notional (``size * price``)."""
        self._body["max_size_notional"] = notional
        return self

    def tokens(self: _T, tokens: Union[str, list]) -> _T:
        """Case-insensitive token filter on the ``token`` column. Works on
        both fills and transfer snapshots."""
        self._body["tokens"] = [tokens] if isinstance(tokens, str) else list(tokens)
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


def _apply_time_filter(df: pl.DataFrame, since, until) -> pl.DataFrame:
    """Client-side ``[since, until]`` slice on the ``time``/``timestamp`` col."""
    if since is None and until is None:
        return df
    col = 'timestamp' if 'timestamp' in df.columns else 'time'
    if col not in df.columns:
        return df
    if since is not None:
        df = df.filter(pl.col(col) >= _to_datetime(since))
    if until is not None:
        df = df.filter(pl.col(col) <= _to_datetime(until))
    return df


class SnapshotNamespace:
    """``client.snapshot.*`` — manage saved parquet snapshots.

    - ``list()`` → keys; ``list(detailed=True)`` / ``list_detailed()`` → keys
      with sizes + a roster-wide total.
    - ``load(key)`` → the whole snapshot as a ``pl.DataFrame`` (convert yourself,
      e.g. ``.to_pandas()`` / ``.to_arrow()``).
    - ``scan(key)`` → :class:`ScanParquetQuery` (server-side wallet-filtered read).
    - ``save(df, key)`` → persist an arbitrary DataFrame as a snapshot.
    - ``delete(key)`` → hard-remove (no undo).

    A read query's ``.as_parquet(key)`` terminal also writes a snapshot directly
    from a server-side query; :meth:`save` is for uploading a frame you already
    hold on the client.
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

    async def save(self, df, key: str, *, overwrite: bool = False) -> None:
        """Save a DataFrame as a snapshot under ``key``.

        Accepts a polars ``DataFrame`` / ``LazyFrame`` (a lazy frame is
        collected) or a pandas ``DataFrame`` (converted via ``pl.from_pandas``).
        A ``time`` column is normalized to ``Datetime('ms', 'UTC')`` so the
        snapshot reloads with a dtype that joins cleanly (mirrors :meth:`load`).

        ``overwrite`` defaults to ``False`` — saving over an existing key raises
        ``FileExistsError`` (snapshots delete with a hard ``os.remove``, so a
        typo would otherwise clobber one with no undo). An empty (0-row) frame is
        rejected. Parquet bytes are streamed to the server from a tempfile, so
        peak memory stays bounded by the frame, not doubled by the upload.
        """
        # Coerce any accepted input to a polars DataFrame (pandas matters — half
        # the pipeline is pandas).
        if isinstance(df, pl.LazyFrame):
            df = df.collect()
        elif isinstance(df, pd.DataFrame):
            df = pl.from_pandas(df)
        elif not isinstance(df, pl.DataFrame):
            raise TypeError(
                "save() expects a polars DataFrame/LazyFrame or a pandas "
                f"DataFrame, got {type(df).__name__}")

        if df.height == 0:
            raise ValueError(f"refusing to save an empty (0-row) snapshot to {key!r}")

        key = _guard_snapshot_key(key)
        if not overwrite and key in await self.list():
            raise FileExistsError(
                f"snapshot {key!r} already exists; pass overwrite=True to replace it")

        df = _cast_time_ms_utc(df)

        fd, tmp_path = tempfile.mkstemp(suffix='.parquet')
        os.close(fd)
        try:
            df.write_parquet(tmp_path)
            del df

            async def _stream(path, chunk=1024 * 1024):
                # httpx AsyncClient rejects sync file handles; chunked async
                # iteration keeps peak upload memory low.
                with open(path, 'rb') as fh:
                    while True:
                        buf = fh.read(chunk)
                        if not buf:
                            break
                        yield buf

            size = os.path.getsize(tmp_path)
            resp = await self._session.post(
                f"{self._base_url}/snapshots/save",
                content=_stream(tmp_path),
                headers={
                    "X-Snapshot-Key": key,
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                },
                timeout=None,
            )
            if not resp.is_success:
                try:
                    err = resp.json().get("error", resp.text)
                except Exception:
                    err = resp.text
                raise DataProviderHTTPError(resp.status_code, err)
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    async def load(self, key: str, *,
                   since: Optional[Union[datetime, str, int]] = None,
                   until: Optional[Union[datetime, str, int]] = None) -> pl.DataFrame:
        """Load a whole snapshot as a polars ``DataFrame`` (``time`` normalized to
        ms+UTC). Convert as you like — ``(await ...).to_pandas()`` /
        ``.to_arrow()``. Optional client-side ``[since, until]`` slice; for a
        server-side wallet filter use :meth:`scan` instead."""
        raw = await load_parquet_bytes(self._session, self._base_url, key)
        df = _cast_time_ms_utc(pl.read_parquet(io.BytesIO(raw)))
        return _apply_time_filter(df, since, until)

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
