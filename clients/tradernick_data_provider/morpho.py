"""Morpho namespace — TN-exclusive.

Morpho's lending surface differs from AAVE: per-market lending keyed by
`market_id`, with `assets` + `shares` instead of a single `amount`, plus
distinct supply/withdraw and supply_collateral/withdraw_collateral pairs.

Method-to-event mapping:
    supplies()              → 'supply'
    withdrawals()           → 'withdraw'
    borrows()               → 'borrow'
    repays()                → 'repay'
    supply_collaterals()    → 'supply_collateral'
    withdraw_collaterals()  → 'withdraw_collateral'
    liquidations()          → 'liquidation'

Pass `.market_id('<hex>')` to filter to a specific Morpho market.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from ._query import EventQuery

if TYPE_CHECKING:
    from typing import Self


class MorphoEventQuery(EventQuery):
    def market_id(self, mid: str) -> Self:
        """Restrict to a single Morpho market (hex id)."""
        self._body['market_id'] = mid
        return self


class MorphoNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str) -> MorphoEventQuery:
        return MorphoEventQuery(
            self._session, self._base_url, '/evm/morpho/read', {'event': event},
        )

    def supplies(self) -> MorphoEventQuery:             return self._q('supply')
    def withdrawals(self) -> MorphoEventQuery:          return self._q('withdraw')
    def borrows(self) -> MorphoEventQuery:              return self._q('borrow')
    def repays(self) -> MorphoEventQuery:               return self._q('repay')
    def supply_collaterals(self) -> MorphoEventQuery:   return self._q('supply_collateral')
    def withdraw_collaterals(self) -> MorphoEventQuery: return self._q('withdraw_collateral')
    def liquidations(self) -> MorphoEventQuery:         return self._q('liquidation')
