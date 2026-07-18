from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
import pandas as pd
import polars as pl
import pyarrow as pa

from ._http import fetch_table

if TYPE_CHECKING:
    from typing import Self

_TIME_COL = "timestamp"


def _to_timestamp(date: datetime | str | int) -> str:
    """Normalise any DateType value to 'YYYY-MM-DDTHH:MM:SSZ' for the server."""
    if isinstance(date, int):
        dt = datetime.fromtimestamp(date / 1000, tz=timezone.utc)
    elif isinstance(date, str):
        if "T" in date:
            dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        elif len(date) == 10:
            dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.strptime(date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    elif isinstance(date, datetime):
        dt = date
    else:
        raise ValueError(f"Unsupported date type: {type(date)}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_list(values: str | list[str]) -> list[str]:
    """Normalize a str-or-list filter argument to a list of strings.
    A single string is wrapped; a list passes through (validated)."""
    if isinstance(values, str):
        return [values]
    out = list(values)
    if not all(isinstance(v, str) for v in out):
        raise TypeError("filter values must be a string or a list of strings")
    return out


class _WalletFilters:
    """The single wallet-selection filter surface, shared by transfer **read**
    queries and by ``ScanParquetQuery``. The same method name works in both
    places — on a read builder the filter is pushed into ClickHouse; on a scan
    builder the server resolves it to member addresses and filters the snapshot
    in DuckDB.

    Every method accepts a single value or a list (``str | list[str]``) and
    matches *any* of the values. ``involving`` matches sender OR receiver;
    ``exclude_*`` negates. Dimensions: raw address, ``_label``/``_entity``
    (synonyms — TN tags wallets with an ``entity``), ``_category``, ``_groups``.
    Values are matched case-insensitively server-side."""

    _body: dict   # set by the concrete subclass

    def _f(self, key: str, values: str | list[str]) -> Self:
        # Last-write-wins per (role, dimension) key; pass a list for "any of".
        # An empty selection is a no-op (the key is left unset).
        v = _as_list(values)
        if v:
            self._body[key] = v
        return self

    # involving (sender OR receiver)
    def involving(self, v: str | list[str]) -> Self:                  return self._f("involving", v)
    def involving_label(self, v: str | list[str]) -> Self:            return self._f("involving_label", v)
    def involving_entity(self, v: str | list[str]) -> Self:           return self._f("involving_label", v)
    def involving_category(self, v: str | list[str]) -> Self:         return self._f("involving_category", v)
    def involving_groups(self, v: str | list[str]) -> Self:           return self._f("involving_groups", v)
    def exclude_involving(self, v: str | list[str]) -> Self:          return self._f("exclude_involving", v)
    def exclude_involving_label(self, v: str | list[str]) -> Self:    return self._f("exclude_involving_label", v)
    def exclude_involving_entity(self, v: str | list[str]) -> Self:   return self._f("exclude_involving_label", v)
    def exclude_involving_category(self, v: str | list[str]) -> Self: return self._f("exclude_involving_category", v)
    def exclude_involving_groups(self, v: str | list[str]) -> Self:   return self._f("exclude_involving_groups", v)

    # sender
    def sender(self, v: str | list[str]) -> Self:                     return self._f("sender", v)
    def sender_label(self, v: str | list[str]) -> Self:               return self._f("sender_label", v)
    def sender_entity(self, v: str | list[str]) -> Self:              return self._f("sender_label", v)
    def sender_category(self, v: str | list[str]) -> Self:            return self._f("sender_category", v)
    def sender_groups(self, v: str | list[str]) -> Self:              return self._f("sender_groups", v)
    def exclude_sender(self, v: str | list[str]) -> Self:             return self._f("exclude_sender", v)
    def exclude_sender_label(self, v: str | list[str]) -> Self:       return self._f("exclude_sender_label", v)
    def exclude_sender_entity(self, v: str | list[str]) -> Self:      return self._f("exclude_sender_label", v)
    def exclude_sender_category(self, v: str | list[str]) -> Self:    return self._f("exclude_sender_category", v)
    def exclude_sender_groups(self, v: str | list[str]) -> Self:      return self._f("exclude_sender_groups", v)

    # receiver
    def receiver(self, v: str | list[str]) -> Self:                   return self._f("receiver", v)
    def receiver_label(self, v: str | list[str]) -> Self:             return self._f("receiver_label", v)
    def receiver_entity(self, v: str | list[str]) -> Self:            return self._f("receiver_label", v)
    def receiver_category(self, v: str | list[str]) -> Self:          return self._f("receiver_category", v)
    def receiver_groups(self, v: str | list[str]) -> Self:            return self._f("receiver_groups", v)
    def exclude_receiver(self, v: str | list[str]) -> Self:           return self._f("exclude_receiver", v)
    def exclude_receiver_label(self, v: str | list[str]) -> Self:     return self._f("exclude_receiver_label", v)
    def exclude_receiver_entity(self, v: str | list[str]) -> Self:    return self._f("exclude_receiver_label", v)
    def exclude_receiver_category(self, v: str | list[str]) -> Self:  return self._f("exclude_receiver_category", v)
    def exclude_receiver_groups(self, v: str | list[str]) -> Self:    return self._f("exclude_receiver_groups", v)


class BaseQuery(_WalletFilters):
    def __init__(self, session: httpx.AsyncClient, base_url: str, body: dict):
        self._session = session
        self._base_url = base_url
        self._body = body

    def network(self, n: str | list[str]) -> Self:
        # EVM-class endpoints accept a list to fan out per-network. The cache
        # is keyed per-network on the server, so each chain reads/writes its
        # own partition independently. The combined result is concatenated
        # client-side and (when len > 1) automatically tagged with
        # ``with_network`` so rows are distinguishable. TRON/BTC are always
        # single-network — passing a one-element list still works.
        if isinstance(n, list):
            self._body["networks"] = n
        else:
            self._body["network"] = n
        return self

    def with_network(self, enabled: bool = True) -> Self:
        self._body["with_network"] = enabled
        return self

    def include_zero_amounts(self, enabled: bool = True) -> Self:
        """Keep rows where amount == 0 in the result. By default these
        are filtered out — they're typically token-approval-style noise
        that inflates row counts without representing real flow.

        The filter applies right before the response is written or the
        snapshot is saved; the data-provider cache itself is unaffected
        and contains all rows including zero-amount ones, so toggling
        this flag does not invalidate cache."""
        self._body["include_zero_amounts"] = enabled
        return self

    def _auto_with_network(self) -> None:
        """Force ``with_network`` on for multi-network fan-out unless the user
        explicitly opted out via ``with_network(False)``."""
        nets = self._body.get("networks") or []
        if len(nets) > 1 and "with_network" not in self._body:
            self._body["with_network"] = True

    def time_range(self, since: datetime | str | int, until: datetime | str | int) -> Self:
        self._body["since"] = _to_timestamp(since)
        self._body["until"] = _to_timestamp(until)
        return self

    # Wallet-selection filters (involving / sender / receiver + label/entity/
    # category/groups + exclude_*) come from _WalletFilters.

    def wallet_namespace(self, ns: str) -> Self:
        self._body["wallet_namespace"] = ns
        return self

    def with_value(self) -> Self:
        self._body["with_value"] = True
        return self

    def verbose(self) -> Self:
        self._body["verbose"] = True
        return self

    def aggregate(self, group_by: str = "time", period: str = "1h") -> Self:
        self._body["aggregate"] = True
        self._body["group_by"] = group_by
        self._body["period"] = period
        return self

    async def as_pandas(self) -> pd.DataFrame:
        table = await self._fetch_table()
        df = table.to_pandas()
        # Canonicalize OHLCV / aggregate responses that come back with
        # `window` instead of `time`. Downstream consumers (backtester,
        # chain_analysis) expect `time` uniformly. Track whether this
        # came from `window` so we know to time-index the result —
        # aggregate frames are naturally time-keyed; transfer frames
        # are row-streams where multiple rows share a time.
        came_from_window = "window" in df.columns and "time" not in df.columns
        if came_from_window:
            df = df.rename(columns={"window": "time"})
        sort_col = next(
            (c for c in (_TIME_COL, "time") if c in df.columns), None
        )
        if sort_col:
            df = df.sort_values(sort_col, ignore_index=True)
        # Normalize the time column precision to ms+UTC for consistency
        # with cache reads and with the polars side. Pandas 2.x supports
        # .dt.as_unit('ms'); guard for older pandas.
        if "time" in df.columns and pd.api.types.is_datetime64_any_dtype(df["time"]):
            try:
                df["time"] = df["time"].dt.as_unit("ms")
            except (AttributeError, TypeError):
                pass
        # Aggregate-shaped frames become time-indexed so they're directly
        # usable in time-series workflows (resampling, plotting, the
        # backtester's set_index check).
        if came_from_window and "time" in df.columns:
            df = df.set_index("time")
        return df

    async def as_polars(self) -> pl.DataFrame:
        table = await self._fetch_table()
        df = pl.from_arrow(table)
        if "window" in df.columns and "time" not in df.columns:
            df = df.rename({"window": "time"})
        sort_col = next(
            (c for c in (_TIME_COL, "time") if c in df.columns), None
        )
        if sort_col:
            df = df.sort(sort_col)
        # Normalize the time column to ms+UTC so joins with snapshot /
        # cache reads (which are ms+UTC) don't trip polars' precision-
        # mismatch check.
        if "time" in df.columns:
            dt = df.schema["time"]
            if isinstance(dt, pl.Datetime) and (
                dt.time_unit != "ms" or dt.time_zone != "UTC"
            ):
                df = df.with_columns(pl.col("time").cast(pl.Datetime("ms", "UTC")))
        return df

    async def as_parquet(self, key: str) -> None:
        """Save the query result as a named parquet snapshot on the server.

        Single-network path uses the worker-side ``save_key`` mechanism
        (server-side save with no extra round-trip).

        Multi-network path can NOT use ``save_key`` because each per-network
        worker would clobber the same file. Instead we fan out without
        ``save_key``, concat client-side, then upload the combined parquet
        via ``POST /snapshots/save``.
        """
        nets = self._body.get("networks") or []
        protocol = getattr(self, "_PROTOCOL", None)
        # Single-network calls land in body['network'] (string). For
        # transfer queries that declare _PROTOCOL we still want the
        # server-side streaming + DuckDB-merge path — coerce to a one-
        # element list and route through /snapshots/save_multi. Saves
        # the legacy single-network path (which eagerly materializes the
        # full result and OOMs on huge volumes like TRON USDT).
        if not nets and protocol:
            single = self._body.get("network")
            if single:
                nets = [single]
        if nets and protocol:
            # Server-side multi-network save: data-provider fans out per-
            # network reads in subprocesses and merges via DuckDB. Bytes
            # never travel back to the client; RAM stays bounded at every
            # hop. Available for transfer queries that declare _PROTOCOL.
            # Single-network path is included — the merge step on one
            # input is still preferable to the legacy eager-DF flow.
            #
            # _resolve_path() side-effects min_amount (and similar) into
            # self._body — call it so the body we POST contains every
            # field the per-network reads need.
            if hasattr(self, "_resolve_path"):
                self._resolve_path()
            body = {**self._body, "protocol": protocol,
                    "save_key": key, "networks": nets}
            # Drop the singular-network field so the server doesn't see
            # both forms. Server reads only `networks`.
            body.pop("network", None)
            resp = await self._session.post(
                f"{self._base_url}/snapshots/save_multi",
                json=body,
                timeout=None,
            )
            resp.raise_for_status()
            return
        if len(nets) > 1:
            import os, tempfile
            df = await self.as_polars()
            # Fallback path for query types without _PROTOCOL: write to a
            # tempfile, drop the polars DF, stream the file to the server
            # via an async generator (httpx AsyncClient rejects sync file
            # handles; chunked async iteration keeps peak memory low).
            fd, tmp_path = tempfile.mkstemp(suffix='.parquet')
            os.close(fd)
            try:
                df.write_parquet(tmp_path)
                del df

                async def _stream(path, chunk=1024 * 1024):
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
                resp.raise_for_status()
            finally:
                try: os.unlink(tmp_path)
                except FileNotFoundError: pass
            return
        self._body["save_key"] = key
        try:
            await self._fetch_table()
        finally:
            del self._body["save_key"]


class CacheableQuery(BaseQuery):
    def cache(self, cache_type: str = "append") -> Self:
        self._body["cache"] = True
        self._body["cache_type"] = cache_type
        return self

    def parallel(self) -> Self:
        self._body["parallel"] = True
        return self


class EventQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, path: str, body: dict):
        super().__init__(session, base_url, body)
        self._path = path

    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return self._path.rsplit("/read", 1)[0] + "/aggregate"
        return self._path

    async def _fetch_single(self, network: str) -> pa.Table:
        # _resolve_path() may mutate self._body. Run it before the snapshot.
        path = self._resolve_path()
        body = {**self._body, "network": network}
        body.pop("networks", None)
        return await fetch_table(self._session, self._base_url + path, body)

    async def _fetch_table(self) -> pa.Table:
        import asyncio

        networks = self._body.get("networks")
        if networks:
            self._auto_with_network()
            tables = await asyncio.gather(*[self._fetch_single(n) for n in networks])
            non_empty = [t for t in tables if t is not None and len(t) > 0]
            if not non_empty:
                return tables[0] if tables else pa.table({})
            return pa.concat_tables(non_empty)
        path = self._resolve_path()
        return await fetch_table(self._session, self._base_url + path, self._body)
