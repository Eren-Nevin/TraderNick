from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .snapshots import SnapshotNamespace

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

    # Snapshots live under ``client.snapshot.*`` (list / load / scan / delete);
    # snapshots are *written* by any read query's ``.as_parquet(key)`` terminal.

    async def close(self) -> None:
        await self._session.aclose()

    async def __aenter__(self) -> "DataProviderClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
