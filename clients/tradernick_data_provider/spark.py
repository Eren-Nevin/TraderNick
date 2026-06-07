"""Spark namespace — TN-exclusive.

Spark's on-chain event surface mirrors AAVE V3 1:1 (six events, identical
column names). The Python surface is intentionally identical so existing
AAVE-targeting code can be redirected to Spark by changing one method
call:

    deposits = await client.evm.aave.deposits().network('ETH').time_range(s, u).as_polars()
    deposits = await client.evm.spark.deposits().network('ETH').time_range(s, u).as_polars()

Wire path: POST /evm/spark/read with `{event, network, since, until,
involving?, exclude_involving?}`.
"""
from __future__ import annotations

import httpx

from ._query import EventQuery


class SparkNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def _q(self, event: str) -> EventQuery:
        return EventQuery(
            self._session, self._base_url, '/evm/spark/read', {'event': event},
        )

    def deposits(self) -> EventQuery:     return self._q('deposit')
    def withdrawals(self) -> EventQuery:  return self._q('withdraw')
    def borrows(self) -> EventQuery:      return self._q('borrow')
    def repays(self) -> EventQuery:       return self._q('repay')
    def flashloans(self) -> EventQuery:   return self._q('flashloan')
    def liquidations(self) -> EventQuery: return self._q('liquidation')
