from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import CacheableQuery

if TYPE_CHECKING:
    from typing import Self


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
    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def max_amount(self, amount: float) -> Self:
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
    def min_amount(self, amount: float) -> Self:
        self._min_amount = amount
        return self

    def max_amount(self, amount: float) -> Self:
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

    async def flush(self, network: str | None = None, token: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        await self._session.post(self._base_url + "/evm/erc20_transfers/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/erc20_transfers/aggregate/flush", json=body)

    async def compact(self, network: str | None = None, token: str | None = None) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        resp = await self._session.post(self._base_url + "/evm/erc20_transfers/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def compact_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/evm/erc20_transfers/aggregate/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, network: str | None = None, token: str | None = None,
                    *, dry_run: bool = False) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        resp = await self._session.post(self._base_url + "/evm/erc20_transfers/dedup", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
        *, dry_run: bool = False,
    ) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/evm/erc20_transfers/aggregate/dedup", json=body)
        resp.raise_for_status()
        return resp.json()


class TRC20Namespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def transfers(self, tokens: list[str]) -> TRC20TransfersQuery:
        return TRC20TransfersQuery(self._session, self._base_url, tokens)

    async def flush(self, network: str | None = None, token: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        await self._session.post(self._base_url + "/tron/trc20_transfers/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/tron/trc20_transfers/aggregate/flush", json=body)

    async def compact(self, network: str | None = None, token: str | None = None) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        resp = await self._session.post(self._base_url + "/tron/trc20_transfers/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def compact_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/tron/trc20_transfers/aggregate/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, network: str | None = None, token: str | None = None,
                    *, dry_run: bool = False) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        resp = await self._session.post(self._base_url + "/tron/trc20_transfers/dedup", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup_aggregate(
        self,
        network: str | None = None,
        token: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
        *, dry_run: bool = False,
    ) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        if token is not None:
            body["token"] = token
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/tron/trc20_transfers/aggregate/dedup", json=body)
        resp.raise_for_status()
        return resp.json()
