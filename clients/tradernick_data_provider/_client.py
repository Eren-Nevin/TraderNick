import io
from datetime import datetime
from typing import Literal, Optional, Union

import httpx
import polars as pl

from ._http import load_parquet_bytes, list_snapshots, delete_snapshot
from ._query import _to_timestamp


def _to_datetime(date: datetime | str | int) -> datetime:
    """Convert any date input to a timezone-aware datetime."""
    ts = _to_timestamp(date)  # returns 'YYYY-MM-DDTHH:MM:SSZ'
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _cast_time_ms_utc(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize the ``time`` column to ``Datetime('ms', 'UTC')``.

    DuckDB-written snapshots come back as μs+UTC; cache reads come back
    as ms+UTC. The cast keeps everything joinable on the polars side.
    """
    if 'time' in df.columns:
        dt = df.schema['time']
        if isinstance(dt, pl.Datetime) and (
            dt.time_unit != 'ms' or dt.time_zone != 'UTC'
        ):
            df = df.with_columns(pl.col('time').cast(pl.Datetime('ms', 'UTC')))
    return df

from .binance import BinanceNamespace, HyperliquidNamespace
from .btc import BtcNamespace
from .evm import EvmNamespace
from .jobs import JobsNamespace
from .protocols import CacheNamespace
from .tron import TronNamespace
from .wallets import WalletsNamespace


class DataProviderClient:
    evm: EvmNamespace
    tron: TronNamespace
    btc: BtcNamespace
    binance: BinanceNamespace
    hyperliquid: HyperliquidNamespace
    wallets: WalletsNamespace
    cache: CacheNamespace
    jobs: JobsNamespace

    def __init__(self, url: str):
        self._url = url.rstrip("/")
        self._session = httpx.AsyncClient(timeout=86400)
        self.evm = EvmNamespace(self._session, self._url)
        self.tron = TronNamespace(self._session, self._url)
        self.btc = BtcNamespace(self._session, self._url)
        self.binance = BinanceNamespace(self._session, self._url)
        self.hyperliquid = HyperliquidNamespace(self._session, self._url)
        self.wallets = WalletsNamespace(self._session, self._url)
        self.cache = CacheNamespace(self._session, self._url)
        self.jobs = JobsNamespace(self._session, self._url)

    async def health(self) -> bool:
        response = await self._session.get(self._url + "/health")
        response.raise_for_status()
        return True

    async def load_parquet(
        self,
        key: str,
        since: Optional[Union[datetime, str, int]] = None,
        until: Optional[Union[datetime, str, int]] = None,
    ) -> pl.DataFrame:
        """Load a saved snapshot as a polars DataFrame.

        ``time`` is normalized to ``Datetime('ms', UTC)`` so joins with
        transfer-read DataFrames (which the cache layer also returns at
        ms+UTC) don't trip the polars 'datatypes of join keys don't
        match' check. Snapshots saved via DuckDB COPY are stored at
        μs+UTC internally; we cast on read.

        For pandas, call ``(await client.load_parquet(key)).to_pandas()``.
        """
        raw = await load_parquet_bytes(self._session, self._url, key)
        df = pl.read_parquet(io.BytesIO(raw))
        df = _cast_time_ms_utc(df)
        if since is not None or until is not None:
            time_col = "timestamp" if "timestamp" in df.columns else "time"
            if time_col in df.columns:
                if since is not None:
                    df = df.filter(pl.col(time_col) >= _to_datetime(since))
                if until is not None:
                    df = df.filter(pl.col(time_col) <= _to_datetime(until))
        return df

    def scan_parquet(self, key: str, *,
                     since: Optional[Union[datetime, str, int]] = None,
                     until: Optional[Union[datetime, str, int]] = None,
                     engine: Literal['polars', 'duckdb'] = 'duckdb',
                     normalize_addresses: Optional[bool] = None):
        """Lazy-scan a saved snapshot with ``local_*`` filters applied
        server-side. Returns a ``ScanParquetQuery`` builder. Chain
        ``local_*`` filter methods then call a terminal ``as_polars()`` /
        ``as_pandas()`` / ``as_parquet(new_key)``.

        ``engine``:
          - ``'duckdb'`` (default): server mounts the snapshot + wallets
            parquets as DuckDB views and runs the filter as SQL.
            Streams via ``COPY ... TO PARQUET``. Best optimizer for
            large ``IN`` filters; ~3-50× faster than polars on big
            wallet-set queries.
          - ``'polars'``: server uses ``pl.scan_parquet`` and a polars
            lazy filter pipeline. Streams via ``sink_parquet``.

        ``normalize_addresses``: default ``None`` (auto). Set to ``False``
        only when you know the snapshot is canonical and the file lacks
        the metadata flag — auto-detect already handles canonical files.

        Example::

            df = await client.scan_parquet('huge_snapshot') \\
                .local_exclude_sender_categories(['Hot-Wallet','Cold-Wallet']) \\
                .local_involving_entities(['Binance']) \\
                .as_polars()
        """
        from .snapshots import ScanParquetQuery
        return ScanParquetQuery(
            self._session, self._url, key,
            since=since, until=until,
            engine=engine, normalize_addresses=normalize_addresses,
        )

    async def list_snapshots(self) -> list[str]:
        """List all saved snapshot keys."""
        return await list_snapshots(self._session, self._url)

    async def delete_snapshot(self, key: str) -> None:
        """Delete a saved snapshot."""
        await delete_snapshot(self._session, self._url, key)

    async def close(self) -> None:
        await self._session.aclose()

    async def __aenter__(self) -> "DataProviderClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
