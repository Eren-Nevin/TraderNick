from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional, Union

import httpx
import polars as pl

if TYPE_CHECKING:
    from .snapshots import ScanParquetQuery, SnapshotNamespace

from .binance import BinanceNamespace, HyperliquidNamespace
from .btc import BtcNamespace
from .evm import EvmNamespace
from .jobs import JobsNamespace
from .tron import TronNamespace
from .wallets import WalletsNamespace


class DataProviderClient:
    evm: EvmNamespace
    tron: TronNamespace
    btc: BtcNamespace
    binance: BinanceNamespace
    hyperliquid: HyperliquidNamespace
    wallets: WalletsNamespace
    jobs: JobsNamespace
    snapshot: "SnapshotNamespace"

    def __init__(self, url: str):
        self._url = url.rstrip("/")
        self._session = httpx.AsyncClient(timeout=86400)
        self.evm = EvmNamespace(self._session, self._url)
        self.tron = TronNamespace(self._session, self._url)
        self.btc = BtcNamespace(self._session, self._url)
        self.binance = BinanceNamespace(self._session, self._url)
        self.hyperliquid = HyperliquidNamespace(self._session, self._url)
        self.wallets = WalletsNamespace(self._session, self._url)
        self.jobs = JobsNamespace(self._session, self._url)
        from .snapshots import SnapshotNamespace
        self.snapshot = SnapshotNamespace(self._session, self._url)

    async def health(self) -> bool:
        response = await self._session.get(self._url + "/health")
        response.raise_for_status()
        return True

    # --- Snapshots ---------------------------------------------------------
    # The snapshot surface lives under ``client.snapshot.*`` (list / load /
    # scan / delete). The methods below are thin **deprecated** delegates kept
    # for horatio-data-provider drop-in parity — prefer the namespace.

    async def load_parquet(
        self,
        key: str,
        since: Optional[Union[datetime, str, int]] = None,
        until: Optional[Union[datetime, str, int]] = None,
    ) -> pl.DataFrame:
        """DEPRECATED — use ``client.snapshot.load(key).as_polars()``.

        Load a saved snapshot as a polars DataFrame (``time`` normalized to
        ms+UTC). For pandas, ``client.snapshot.load(key).as_pandas()``.
        """
        return await self.snapshot.load(key, since=since, until=until).as_polars()

    def scan_parquet(self, key: str, *,
                     since: Optional[Union[datetime, str, int]] = None,
                     until: Optional[Union[datetime, str, int]] = None,
                     engine: Literal['polars', 'duckdb'] = 'duckdb',
                     normalize_addresses: Optional[bool] = None) -> "ScanParquetQuery":
        """DEPRECATED — use ``client.snapshot.scan(key)``.

        Lazy, server-side wallet-filtered read of a snapshot. Returns a
        ``ScanParquetQuery``; chain the wallet-selection filters then call a
        terminal ``as_polars()`` / ``as_pandas()`` / ``as_parquet(new_key)``.
        """
        return self.snapshot.scan(
            key, since=since, until=until,
            engine=engine, normalize_addresses=normalize_addresses,
        )

    async def list_snapshots(self) -> list[str]:
        """DEPRECATED — use ``client.snapshot.list()``. Saved snapshot keys."""
        return await self.snapshot.list()

    async def list_snapshots_detailed(self) -> dict:
        """DEPRECATED — use ``client.snapshot.list(detailed=True)``. Saved
        snapshots with sizes + a roster-wide total."""
        return await self.snapshot.list_detailed()

    async def delete_snapshot(self, key: str) -> None:
        """DEPRECATED — use ``client.snapshot.delete(key)``."""
        await self.snapshot.delete(key)

    async def close(self) -> None:
        await self._session.aclose()

    async def __aenter__(self) -> "DataProviderClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
