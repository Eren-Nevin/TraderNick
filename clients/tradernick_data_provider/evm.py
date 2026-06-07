"""EVM chain namespace — groups ERC-20, native, and EVM-only protocol accessors.

Exposed as ``client.evm`` on :class:`DataProviderClient`. All sub-namespaces
target the server's ``/evm/...`` HTTP routes.
"""
from __future__ import annotations

import httpx

from .erc20 import ERC20Namespace, ERC20TransfersQuery
from .protocols import (
    AaveNamespace,
    LidoNamespace,
    NativeNamespace,
    NativeTransfersQuery,
    StaderNamespace,
    ThresholdNamespace,
    UniswapNamespace,
)


class EvmNamespace:
    """EVM chain: ``client.evm.{erc20,aave,uniswap,lido,stader,threshold}``
    plus ``client.evm.native_transfers()``."""

    erc20: ERC20Namespace
    aave: AaveNamespace
    uniswap: UniswapNamespace
    lido: LidoNamespace
    stader: StaderNamespace
    threshold: ThresholdNamespace

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self.erc20 = ERC20Namespace(session, base_url)
        self.aave = AaveNamespace(session, base_url)
        self.uniswap = UniswapNamespace(session, base_url)
        self.lido = LidoNamespace(session, base_url)
        self.stader = StaderNamespace(session, base_url)
        self.threshold = ThresholdNamespace(session, base_url)
        self._native = NativeNamespace(session, base_url)

    def native_transfers(self) -> NativeTransfersQuery:
        """Return a query builder for EVM native-token transfers."""
        return self._native.transfers()

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

    async def dedup_native_transfers_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
        *, dry_run: bool = False,
    ) -> dict:
        return await self._native.dedup_aggregate(
            network=network, group_by=group_by, period=period, dry_run=dry_run,
        )
