"""EVM chain namespace — groups ERC-20, native, and EVM-only protocol accessors.

Exposed as ``client.evm`` on :class:`DataProviderClient`. All sub-namespaces
target the server's ``/evm/...`` HTTP routes.
"""
from __future__ import annotations

import httpx

from .aerodrome import AerodromeNamespace
from .erc20 import ERC20Namespace, ERC20TransfersQuery
from .morpho import MorphoNamespace
from .protocols import (
    AaveNamespace,
    LidoNamespace,
    NativeNamespace,
    NativeTransfersQuery,
    UniswapNamespace,
)
from .spark import SparkNamespace


class EvmNamespace:
    """EVM chain: ``client.evm.{erc20, aave, uniswap, lido, spark, morpho,
    aerodrome}`` plus ``client.evm.native_transfers()``.

    Spark / Morpho / Aerodrome are TN-exclusive — they're not in Horatio's
    surface but live under the same ``evm.*`` namespace so the rest of the
    API stays consistent. Existing horatio-compatible code is unaffected
    (the new namespaces are additive).

    Stader / Threshold were dropped in 0.4.0: TN doesn't ingest those
    upstreams, and the empty-stub responses gave callers a false impression
    the namespaces were live. Re-add when ingestion picks them up."""

    erc20: ERC20Namespace
    aave: AaveNamespace
    uniswap: UniswapNamespace
    lido: LidoNamespace
    spark: SparkNamespace
    morpho: MorphoNamespace
    aerodrome: AerodromeNamespace

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self.erc20 = ERC20Namespace(session, base_url)
        self.aave = AaveNamespace(session, base_url)
        self.uniswap = UniswapNamespace(session, base_url)
        self.lido = LidoNamespace(session, base_url)
        self.spark = SparkNamespace(session, base_url)
        self.morpho = MorphoNamespace(session, base_url)
        self.aerodrome = AerodromeNamespace(session, base_url)
        self._native = NativeNamespace(session, base_url)

    def native_transfers(self) -> NativeTransfersQuery:
        """Return a query builder for EVM native-token transfers."""
        return self._native.transfers()
