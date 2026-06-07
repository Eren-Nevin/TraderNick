"""Aerodrome namespace — TN-exclusive.

Two flavors of pools:

    client.evm.aerodrome.concentrated.swaps('WETH','USDC',100) → V3-like CL
    client.evm.aerodrome.basic.swaps('WETH','USDC',stable=False) → V2-like AMM

Both surfaces accept either canonical or reversed pair ordering — Aerodrome
pools' canonical (symbol0, symbol1) ordering is byte-address ordered, and
callers shouldn't have to know which is which.

Concentrated event set: swap / deposit / withdraw / collect (mirrors Uni V3).
Basic event set:        swap / deposit / withdraw / claim (mirrors Uni V2 +
Velodrome-style stable-vs-volatile pair flag).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ._query import EventQuery

if TYPE_CHECKING:
    from typing import Self


class _AeroEventQuery(EventQuery):
    pass


class AerodromeConcentratedNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str, symbol0: str, symbol1: str,
           tick_spacing: int | None) -> _AeroEventQuery:
        body: dict = {'event': event, 'symbol0': symbol0, 'symbol1': symbol1}
        if tick_spacing is not None:
            body['tick_spacing'] = int(tick_spacing)
        return _AeroEventQuery(
            self._session, self._base_url,
            '/evm/aerodrome/concentrated/read', body,
        )

    def swaps(self, symbol0: str, symbol1: str,
              tick_spacing: int | None = None) -> _AeroEventQuery:
        return self._q('swap', symbol0, symbol1, tick_spacing)

    def deposits(self, symbol0: str, symbol1: str,
                 tick_spacing: int | None = None) -> _AeroEventQuery:
        return self._q('deposit', symbol0, symbol1, tick_spacing)

    def withdrawals(self, symbol0: str, symbol1: str,
                    tick_spacing: int | None = None) -> _AeroEventQuery:
        return self._q('withdraw', symbol0, symbol1, tick_spacing)

    def collects(self, symbol0: str, symbol1: str,
                 tick_spacing: int | None = None) -> _AeroEventQuery:
        return self._q('collect', symbol0, symbol1, tick_spacing)


class AerodromeBasicNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str, symbol0: str, symbol1: str,
           stable: bool | None) -> _AeroEventQuery:
        body: dict = {'event': event, 'symbol0': symbol0, 'symbol1': symbol1}
        if stable is not None:
            body['stable'] = bool(stable)
        return _AeroEventQuery(
            self._session, self._base_url,
            '/evm/aerodrome/basic/read', body,
        )

    def swaps(self, symbol0: str, symbol1: str,
              stable: bool | None = None) -> _AeroEventQuery:
        return self._q('swap', symbol0, symbol1, stable)

    def deposits(self, symbol0: str, symbol1: str,
                 stable: bool | None = None) -> _AeroEventQuery:
        return self._q('deposit', symbol0, symbol1, stable)

    def withdrawals(self, symbol0: str, symbol1: str,
                    stable: bool | None = None) -> _AeroEventQuery:
        return self._q('withdraw', symbol0, symbol1, stable)

    def claims(self, symbol0: str, symbol1: str,
               stable: bool | None = None) -> _AeroEventQuery:
        return self._q('claim', symbol0, symbol1, stable)


class AerodromeNamespace:
    concentrated: AerodromeConcentratedNamespace
    basic: AerodromeBasicNamespace

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self.concentrated = AerodromeConcentratedNamespace(session, base_url)
        self.basic        = AerodromeBasicNamespace(session, base_url)
