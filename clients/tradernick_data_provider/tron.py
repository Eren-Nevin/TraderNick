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
