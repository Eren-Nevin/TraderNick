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

    async def flush(self, network: str | None = None, event: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        await self._session.post(self._base_url + "/evm/aave/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/aave/aggregate/flush", json=body)

    async def compact(self, network: str | None = None, event: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        await self._session.post(self._base_url + "/evm/aave/compact", json=body)

    async def compact_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/aave/aggregate/compact", json=body)


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

    async def flush(
        self,
        network: str | None = None,
        event: str | None = None,
        symbol0: str | None = None,
        symbol1: str | None = None,
        fee: int | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if symbol0 is not None:
            body["symbol0"] = symbol0
        if symbol1 is not None:
            body["symbol1"] = symbol1
        if fee is not None:
            body["fee"] = fee
        await self._session.post(self._base_url + "/evm/uniswap/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        symbol0: str | None = None,
        symbol1: str | None = None,
        fee: int | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if symbol0 is not None:
            body["symbol0"] = symbol0
        if symbol1 is not None:
            body["symbol1"] = symbol1
        if fee is not None:
            body["fee"] = fee
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/uniswap/aggregate/flush", json=body)

    async def compact(
        self,
        network: str | None = None,
        event: str | None = None,
        symbol0: str | None = None,
        symbol1: str | None = None,
        fee: int | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if symbol0 is not None:
            body["symbol0"] = symbol0
        if symbol1 is not None:
            body["symbol1"] = symbol1
        if fee is not None:
            body["fee"] = fee
        await self._session.post(self._base_url + "/evm/uniswap/compact", json=body)

    async def compact_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        symbol0: str | None = None,
        symbol1: str | None = None,
        fee: int | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if symbol0 is not None:
            body["symbol0"] = symbol0
        if symbol1 is not None:
            body["symbol1"] = symbol1
        if fee is not None:
            body["fee"] = fee
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/uniswap/aggregate/compact", json=body)


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

    async def flush(self, network: str | None = None, event: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        await self._session.post(self._base_url + "/evm/lido/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/lido/aggregate/flush", json=body)

    async def compact(self, network: str | None = None, event: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        await self._session.post(self._base_url + "/evm/lido/compact", json=body)

    async def compact_aggregate(
        self,
        network: str | None = None,
        event: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if event is not None:
            body["event"] = event
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/lido/aggregate/compact", json=body)


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

    def sender(self, address: str) -> Self:
        self._body["sender"] = address
        return self

    def receiver(self, address: str) -> Self:
        self._body["receiver"] = address
        return self

    def sender_label(self, label: str) -> Self:
        self._body["sender_label"] = label
        return self

    def sender_category(self, category: str) -> Self:
        self._body["sender_category"] = category
        return self

    def receiver_label(self, label: str) -> Self:
        self._body["receiver_label"] = label
        return self

    def receiver_category(self, category: str) -> Self:
        self._body["receiver_category"] = category
        return self

    def exclude_sender(self, address: str) -> Self:
        self._body["exclude_sender"] = address
        return self

    def exclude_sender_label(self, label: str) -> Self:
        self._body["exclude_sender_label"] = label
        return self

    def exclude_sender_category(self, category: str) -> Self:
        self._body["exclude_sender_category"] = category
        return self

    def exclude_receiver(self, address: str) -> Self:
        self._body["exclude_receiver"] = address
        return self

    def exclude_receiver_label(self, label: str) -> Self:
        self._body["exclude_receiver_label"] = label
        return self

    def exclude_receiver_category(self, category: str) -> Self:
        self._body["exclude_receiver_category"] = category
        return self

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

    async def flush(self, network: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        await self._session.post(self._base_url + "/evm/native_transfers/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/evm/native_transfers/aggregate/flush", json=body)

    async def compact(self, network: str | None = None) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/evm/native_transfers/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def compact_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/evm/native_transfers/aggregate/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, network: str | None = None, *, dry_run: bool = False) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/evm/native_transfers/dedup", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
        *, dry_run: bool = False,
    ) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/evm/native_transfers/aggregate/dedup", json=body)
        resp.raise_for_status()
        return resp.json()


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

    def sender(self, address: str) -> Self:
        self._body["sender"] = address
        return self

    def receiver(self, address: str) -> Self:
        self._body["receiver"] = address
        return self

    def sender_label(self, label: str) -> Self:
        self._body["sender_label"] = label
        return self

    def sender_category(self, category: str) -> Self:
        self._body["sender_category"] = category
        return self

    def receiver_label(self, label: str) -> Self:
        self._body["receiver_label"] = label
        return self

    def receiver_category(self, category: str) -> Self:
        self._body["receiver_category"] = category
        return self

    def exclude_sender(self, address: str) -> Self:
        self._body["exclude_sender"] = address
        return self

    def exclude_sender_label(self, label: str) -> Self:
        self._body["exclude_sender_label"] = label
        return self

    def exclude_sender_category(self, category: str) -> Self:
        self._body["exclude_sender_category"] = category
        return self

    def exclude_receiver(self, address: str) -> Self:
        self._body["exclude_receiver"] = address
        return self

    def exclude_receiver_label(self, label: str) -> Self:
        self._body["exclude_receiver_label"] = label
        return self

    def exclude_receiver_category(self, category: str) -> Self:
        self._body["exclude_receiver_category"] = category
        return self

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

    async def flush(self, network: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        await self._session.post(self._base_url + "/tron/native_transfers/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/tron/native_transfers/aggregate/flush", json=body)

    async def compact(self, network: str | None = None) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/tron/native_transfers/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def compact_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/tron/native_transfers/aggregate/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, network: str | None = None, *, dry_run: bool = False) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/tron/native_transfers/dedup", json=body)
        resp.raise_for_status()
        return resp.json()


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

    def sender(self, address: str) -> Self:
        self._body["sender"] = address
        return self

    def receiver(self, address: str) -> Self:
        self._body["receiver"] = address
        return self

    def sender_label(self, label: str) -> Self:
        self._body["sender_label"] = label
        return self

    def sender_category(self, category: str) -> Self:
        self._body["sender_category"] = category
        return self

    def receiver_label(self, label: str) -> Self:
        self._body["receiver_label"] = label
        return self

    def receiver_category(self, category: str) -> Self:
        self._body["receiver_category"] = category
        return self

    def exclude_sender(self, address: str) -> Self:
        self._body["exclude_sender"] = address
        return self

    def exclude_sender_label(self, label: str) -> Self:
        self._body["exclude_sender_label"] = label
        return self

    def exclude_sender_category(self, category: str) -> Self:
        self._body["exclude_sender_category"] = category
        return self

    def exclude_receiver(self, address: str) -> Self:
        self._body["exclude_receiver"] = address
        return self

    def exclude_receiver_label(self, label: str) -> Self:
        self._body["exclude_receiver_label"] = label
        return self

    def exclude_receiver_category(self, category: str) -> Self:
        self._body["exclude_receiver_category"] = category
        return self

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

    async def flush(self, network: str | None = None) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        await self._session.post(self._base_url + "/btc/native_transfers/flush", json=body)

    async def flush_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> None:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        await self._session.post(self._base_url + "/btc/native_transfers/aggregate/flush", json=body)

    async def compact(self, network: str | None = None) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/btc/native_transfers/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def compact_aggregate(
        self,
        network: str | None = None,
        group_by: str | None = None,
        period: str | None = None,
    ) -> dict:
        body = {}
        if network is not None:
            body["network"] = network
        if group_by is not None:
            body["group_by"] = group_by
        if period is not None:
            body["period"] = period
        resp = await self._session.post(self._base_url + "/btc/native_transfers/aggregate/compact", json=body)
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, network: str | None = None, *, dry_run: bool = False) -> dict:
        body: dict = {'dry_run': bool(dry_run)}
        if network is not None:
            body["network"] = network
        resp = await self._session.post(self._base_url + "/btc/native_transfers/dedup", json=body)
        resp.raise_for_status()
        return resp.json()


class CacheNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    async def flush(self) -> None:
        await self._session.post(self._base_url + "/cache/flush", json={})

    async def compact(self) -> dict:
        resp = await self._session.post(self._base_url + "/cache/compact", json={})
        resp.raise_for_status()
        return resp.json()

    async def dedup(self, *, dry_run: bool = False) -> dict:
        """Trigger dedup across every cached partition. Returns a {job_id, status}
        handle. Use ``client.jobs.wait(handle['job_id'])`` to block until complete.
        """
        resp = await self._session.post(
            self._base_url + "/cache/dedup", json={'dry_run': bool(dry_run)},
        )
        resp.raise_for_status()
        return resp.json()

    async def migrate_time(self) -> dict:
        resp = await self._session.post(self._base_url + "/cache/migrate-time", json={})
        resp.raise_for_status()
        return resp.json()
