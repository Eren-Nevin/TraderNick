"""Snapshot-side fluent builders.

Right now exposes ``ScanParquetQuery`` — accumulates ``local_*`` filter
steps and dispatches them server-side via ``POST /snapshots/scan``.
The server lazily scans the snapshot with ``pl.scan_parquet`` so only
the filtered subset is collected and returned, keeping client RAM
bounded by the result size rather than the full snapshot size.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import httpx
import pandas as pd
import pyarrow.parquet as pq
import polars as pl

from datetime import datetime

from ._http import DataProviderHTTPError
from ._query import _WalletFilters, _to_timestamp

if TYPE_CHECKING:
    from typing import Self


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

    def time_range(self, since, until) -> "Self":
        """Set ``[since, until)`` on the chain. Overrides any range
        passed at construction time."""
        self._body["since"] = _to_timestamp(since)
        self._body["until"] = _to_timestamp(until)
        return self

    def min_amount(self, amount: float) -> "Self":
        self._body["min_amount"] = amount
        return self

    def max_amount(self, amount: float) -> "Self":
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
