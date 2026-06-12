"""TRON chain namespace — TRC-20 tokens and native TRX transfers.

Exposed as ``client.tron`` on :class:`DataProviderClient`. Targets the
server's ``/tron/...`` HTTP routes.
"""
from __future__ import annotations

import httpx

from .erc20 import TRC20Namespace, TRC20TransfersQuery
from .protocols import TronNativeNamespace, TronNativeTransfersQuery


class _AutoNetworkTRC20:
    """Thin wrapper around TRC20Namespace that auto-sets ``network='TRON'``."""

    def __init__(self, inner: TRC20Namespace):
        self._inner = inner

    def transfers(self, tokens: list[str]) -> TRC20TransfersQuery:
        return self._inner.transfers(tokens).network('TRON')

    async def flush(self, **kw):
        return await self._inner.flush(**kw)

    async def flush_aggregate(self, **kw):
        return await self._inner.flush_aggregate(**kw)

    async def compact(self, **kw):
        return await self._inner.compact(**kw)

    async def compact_aggregate(self, **kw):
        return await self._inner.compact_aggregate(**kw)

    async def dedup(self, **kw):
        return await self._inner.dedup(**kw)

    async def dedup_aggregate(self, **kw):
        return await self._inner.dedup_aggregate(**kw)


class TronNamespace:
    """TRON chain: ``client.tron.trc20`` + ``client.tron.native_transfers()``.

    Network is implicit — callers don't need to chain ``.network('TRON')``.
    """

    trc20: _AutoNetworkTRC20

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self.trc20 = _AutoNetworkTRC20(TRC20Namespace(session, base_url))
        self._native = TronNativeNamespace(session, base_url)

    def native_transfers(self) -> TronNativeTransfersQuery:
        """Return a query builder for native TRX transfers (network pre-set)."""
        return self._native.transfers().network('TRON')

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
