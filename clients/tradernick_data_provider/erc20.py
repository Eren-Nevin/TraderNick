from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import CacheableQuery




# Self-type TypeVar for fluent-builder chaining. Equivalent to typing.Self
# (PEP 673) but the explicit self-type form is what jedi/IDE completion
# follows correctly through the mixin/base hierarchy.
_T = TypeVar("_T")

def _validate_tokens(tokens) -> list[str]:
    # A bare str passed as `tokens` iterates char-by-char downstream, producing
    # phantom single-letter token requests (e.g. tokens='AERO' becomes
    # ['A','E','R','O']). Reject the str case explicitly.
    if isinstance(tokens, str):
        raise TypeError(
            f"tokens must be a list of token symbols, not a single string. "
            f"Got {tokens!r} — wrap it in a list: [{tokens!r}]."
        )
    out = list(tokens)
    if not out:
        raise ValueError("tokens must be a non-empty list of token symbols")
    return out


class ERC20TransfersQuery(CacheableQuery):
    _PROTOCOL = "erc20_transfers"

    def __init__(self, session: httpx.AsyncClient, base_url: str, tokens: list[str]):
        super().__init__(session, base_url, {"tokens": _validate_tokens(tokens)})
        self._min_amount = None

    # Wallet-selection filters (sender/receiver/involving + label/entity/
    # category/groups + exclude_*) are inherited from _WalletFilters.
    def min_amount(self: _T, amount: float) -> _T:
        self._min_amount = amount
        return self

    def max_amount(self: _T, amount: float) -> _T:
        self._body["max_amount"] = amount
        return self

    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return "/evm/erc20_transfers/aggregate"
        elif self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/evm/erc20_transfers/read/min"
        return "/evm/erc20_transfers/read"

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


class TRC20TransfersQuery(CacheableQuery):
    _PROTOCOL = "trc20_transfers"

    def __init__(self, session: httpx.AsyncClient, base_url: str, tokens: list[str]):
        super().__init__(session, base_url, {"tokens": _validate_tokens(tokens)})
        self._min_amount = None

    # Wallet-selection filters inherited from _WalletFilters.
    def min_amount(self: _T, amount: float) -> _T:
        self._min_amount = amount
        return self

    def max_amount(self: _T, amount: float) -> _T:
        self._body["max_amount"] = amount
        return self

    def _resolve_path(self) -> str:
        if self._body.get("aggregate"):
            return "/tron/trc20_transfers/aggregate"
        elif self._min_amount is not None:
            self._body["min_amount"] = self._min_amount
            return "/tron/trc20_transfers/read/min"
        return "/tron/trc20_transfers/read"

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


class ERC20Namespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self, tokens: list[str]) -> ERC20TransfersQuery:
        return ERC20TransfersQuery(self._session, self._base_url, tokens)


class TRC20Namespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self, tokens: list[str]) -> TRC20TransfersQuery:
        return TRC20TransfersQuery(self._session, self._base_url, tokens)
