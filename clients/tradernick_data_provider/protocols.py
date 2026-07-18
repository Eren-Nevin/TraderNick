from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import BaseQuery, CacheableQuery, EventQuery

if TYPE_CHECKING:
    from typing import Self


class AaveEventQuery(EventQuery):
    def eth_market_type(self, market_type: str) -> Self:
        self._body["eth_market_type"] = market_type
        return self


class AaveNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str) -> AaveEventQuery:
        return AaveEventQuery(self._session, self._base_url, "/evm/aave/read", {"event": event})

    def deposits(self) -> AaveEventQuery:
        return self._q("deposit")

    def withdrawals(self) -> AaveEventQuery:
        return self._q("withdraw")

    def borrows(self) -> AaveEventQuery:
        return self._q("borrow")

    def repays(self) -> AaveEventQuery:
        return self._q("repay")

    def flashloans(self) -> AaveEventQuery:
        return self._q("flashloan")

    def liquidations(self) -> AaveEventQuery:
        return self._q("liquidation")


class UniswapNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str, symbol0: str, symbol1: str, fee: int) -> EventQuery:
        return EventQuery(
            self._session,
            self._base_url,
            "/evm/uniswap/read",
            {"event": event, "symbol0": symbol0, "symbol1": symbol1, "fee": fee},
        )

    def swaps(self, symbol0: str, symbol1: str, fee: int) -> EventQuery:
        return self._q("swap", symbol0, symbol1, fee)

    def deposits(self, symbol0: str, symbol1: str, fee: int) -> EventQuery:
        return self._q("deposit", symbol0, symbol1, fee)

    def withdrawals(self, symbol0: str, symbol1: str, fee: int) -> EventQuery:
        return self._q("withdraw", symbol0, symbol1, fee)

    def collects(self, symbol0: str, symbol1: str, fee: int) -> EventQuery:
        return self._q("collect", symbol0, symbol1, fee)


class LidoNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str) -> EventQuery:
        return EventQuery(self._session, self._base_url, "/evm/lido/read", {"event": event})

    def deposits(self) -> EventQuery:
        return self._q("deposit")

    def withdrawal_requests(self) -> EventQuery:
        return self._q("withdrawal_request")

    def withdrawals_claimed(self) -> EventQuery:
        return self._q("withdrawal_claimed")

    def l2_deposits(self) -> EventQuery:
        return self._q("l2_deposit")

    def l2_withdrawal_requests(self) -> EventQuery:
        return self._q("l2_withdrawal_request")


# Stader and Threshold namespaces removed in 0.4.0 — TN doesn't ingest
# those upstreams, and the previous empty-stub responses gave callers a
# false impression the namespace was live. Re-add when ingestion lands.


class NativeTransfersQuery(CacheableQuery):
    _PROTOCOL = "native_transfers"

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        super().__init__(session, base_url, {})
        self._min_amount = None

    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def max_amount(self, amount: float) -> Self:
        self._body["max_amount"] = amount
        return self

    # Wallet-selection filters inherited from _WalletFilters.
    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return "/evm/native_transfers/aggregate"
        elif self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/evm/native_transfers/read/min"
        return "/evm/native_transfers/read"

    async def _fetch_single(self, network: str) -> pa.Table:
        # _resolve_path() may mutate self._body (e.g. injects min_amount when
        # routing to /read/min). It must run before we snapshot self._body.
        path = self._resolve_path()
        body = {**self._body, "network": network}
        body.pop("networks", None)
        return await fetch_table(self._session, self._base_url + path, body)

    async def _fetch_table(self) -> pa.Table:
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


class NativeNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self) -> NativeTransfersQuery:
        return NativeTransfersQuery(self._session, self._base_url)


class TronNativeTransfersQuery(CacheableQuery):
    _PROTOCOL = "tron_native_transfers"

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        super().__init__(session, base_url, {})
        self._min_amount = None

    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def max_amount(self, amount: float) -> Self:
        self._body["max_amount"] = amount
        return self

    # Wallet-selection filters inherited from _WalletFilters.
    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return "/tron/native_transfers/aggregate"
        elif self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/tron/native_transfers/read/min"
        return "/tron/native_transfers/read"

    async def _fetch_single(self, network: str) -> pa.Table:
        # _resolve_path() may mutate self._body (e.g. injects min_amount when
        # routing to /read/min). It must run before we snapshot self._body.
        path = self._resolve_path()
        body = {**self._body, "network": network}
        body.pop("networks", None)
        return await fetch_table(self._session, self._base_url + path, body)

    async def _fetch_table(self) -> pa.Table:
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


class TronNativeNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self) -> TronNativeTransfersQuery:
        return TronNativeTransfersQuery(self._session, self._base_url)


class BitcoinNativeTransfersQuery(CacheableQuery):
    _PROTOCOL = "bitcoin_native_transfers"

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        super().__init__(session, base_url, {})
        self._min_amount = None

    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def max_amount(self, amount: float) -> Self:
        self._body["max_amount"] = amount
        return self

    # Wallet-selection filters inherited from _WalletFilters.
    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return "/btc/native_transfers/aggregate"
        elif self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/btc/native_transfers/read/min"
        return "/btc/native_transfers/read"

    async def _fetch_single(self, network: str) -> pa.Table:
        # _resolve_path() may mutate self._body (e.g. injects min_amount when
        # routing to /read/min). It must run before we snapshot self._body.
        path = self._resolve_path()
        body = {**self._body, "network": network}
        body.pop("networks", None)
        return await fetch_table(self._session, self._base_url + path, body)

    async def _fetch_table(self) -> pa.Table:
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


class BitcoinNativeNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self) -> BitcoinNativeTransfersQuery:
        return BitcoinNativeTransfersQuery(self._session, self._base_url)
