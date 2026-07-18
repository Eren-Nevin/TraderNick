"""Bitcoin chain namespace — native BTC transfers and coinbase payouts.

Exposed as ``client.btc`` on :class:`DataProviderClient`. Targets the
server's ``/btc/...`` HTTP routes.
"""
from __future__ import annotations

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import CacheableQuery
from .protocols import BitcoinNativeNamespace, BitcoinNativeTransfersQuery

if __import__('typing').TYPE_CHECKING:
    from typing import Self


class BtcMinedQuery(CacheableQuery):
    """Query builder for Bitcoin coinbase (block-reward) payouts."""

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        super().__init__(session, base_url, {'network': 'BITCOIN'})
        self._min_amount = None

    # receiver / receiver_label (and the rest of the wallet-selection surface)
    # are inherited from _WalletFilters.
    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def _resolve_path(self) -> str:
        if self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/btc/mined/read/min"
        return "/btc/mined/read"

    async def _fetch_table(self) -> pa.Table:
        path = self._resolve_path()
        return await fetch_table(self._session, self._base_url + path, self._body)


class BtcNamespace:
    """Bitcoin chain: ``client.btc.native_transfers()`` + ``client.btc.mined()``."""

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self._native = BitcoinNativeNamespace(session, base_url)

    def native_transfers(self) -> BitcoinNativeTransfersQuery:
        """Return a query builder for native BTC transfers (excludes coinbase). Network pre-set."""
        return self._native.transfers().network('BITCOIN')

    def mined(self) -> BtcMinedQuery:
        """Return a query builder for coinbase (block-reward) payouts. No sender column."""
        return BtcMinedQuery(self._session, self._base_url)

    async def flush_native_transfers(self, network: str | None = None) -> None:
        await self._native.flush(network=network)

    async def flush_native_transfers_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        await self._native.flush_aggregate(
            network=network, group_by=group_by, period=period,
        )

    async def compact_native_transfers(self, network: str | None = None) -> dict:
        return await self._native.compact(network=network)

    async def compact_native_transfers_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        return await self._native.compact_aggregate(
            network=network, group_by=group_by, period=period,
        )

    async def dedup_native_transfers(self, network: str | None = None,
                                       *, dry_run: bool = False) -> dict:
        return await self._native.dedup(network=network, dry_run=dry_run)
