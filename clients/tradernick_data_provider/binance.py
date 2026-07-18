from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import CacheableQuery

if TYPE_CHECKING:
    from typing import Self


def _flatten(args: tuple) -> list[str]:
    """Normalize varargs-or-list into a flat list of strings, so
    ``f("BTC", "ETH")``, ``f(["BTC", "ETH"])`` and ``f("BTC")`` all yield the
    same ``["BTC", ...]`` (a bare list no longer nests into ``[[...]]``)."""
    out: list[str] = []
    for a in args:
        out.extend(a if isinstance(a, (list, tuple)) else [a])
    return out


class RawTradesQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    def add_symbol(self, enabled: bool = True) -> Self:
        self._body["add_symbol"] = enabled
        return self

    def with_id(self) -> Self:
        """Include the trade id column in results (excluded by default)."""
        self._body["with_id"] = True
        return self

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(self._session, self._base_url + "/binance/raw_trades/read", self._body)


class OHLCVQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str, window: str):
        super().__init__(session, base_url, {"token": token, "window": window})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(self._session, self._base_url + "/binance/ohlcv/read", self._body)


class BookDepthQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/book_depth/read", self._body
        )


class OpenInterestQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/open_interest/read", self._body
        )


class FundingRateQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/funding_rate/read", self._body
        )


class LongShortRatiosQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/long_short_ratios/read", self._body
        )


# --- Binance SPOT ----------------------------------------------------------
# TN-exclusive: the spot market is a fully separate dataset from perp/futures.
# Same populated shapes as the perp ohlcv / raw_trades, so these mirror the
# perp query classes and only differ in the server path (/binance/spot/...).
class SpotOHLCVQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str, window: str):
        super().__init__(session, base_url, {"token": token, "window": window})

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/spot/ohlcv/read", self._body
        )


class SpotRawTradesQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, token: str):
        super().__init__(session, base_url, {"token": token})

    def add_symbol(self, enabled: bool = True) -> Self:
        self._body["add_symbol"] = enabled
        return self

    def with_id(self) -> Self:
        """Include the trade id column in results (excluded by default)."""
        self._body["with_id"] = True
        return self

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(
            self._session, self._base_url + "/binance/spot/raw_trades/read", self._body
        )


class BinanceSpotNamespace:
    """`client.binance.spot` — spot-market ohlcv + raw trades."""

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def ohlcv(self, token: str, window: str) -> SpotOHLCVQuery:
        return SpotOHLCVQuery(self._session, self._base_url, token, window)

    def raw_trades(self, token: str) -> SpotRawTradesQuery:
        return SpotRawTradesQuery(self._session, self._base_url, token)


class BinanceNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url
        self.spot = BinanceSpotNamespace(session, base_url)

    def raw_trades(self, token: str) -> RawTradesQuery:
        return RawTradesQuery(self._session, self._base_url, token)

    def ohlcv(self, token: str, window: str) -> OHLCVQuery:
        return OHLCVQuery(self._session, self._base_url, token, window)

    def book_depth(self, token: str) -> BookDepthQuery:
        return BookDepthQuery(self._session, self._base_url, token)

    def open_interest(self, token: str) -> OpenInterestQuery:
        return OpenInterestQuery(self._session, self._base_url, token)

    def funding_rate(self, token: str) -> FundingRateQuery:
        return FundingRateQuery(self._session, self._base_url, token)

    def long_short_ratios(self, token: str) -> LongShortRatiosQuery:
        return LongShortRatiosQuery(self._session, self._base_url, token)


class HyperliquidQuery(CacheableQuery):
    def __init__(self, session: httpx.AsyncClient, base_url: str, path: str):
        super().__init__(session, base_url, {})
        self._hl_path = path

    def tokens(self, *symbols: str | list[str]) -> Self:
        """Filter by HL token symbol(s). Accepts varargs or a list —
        ``.tokens("BTC", "ETH")``, ``.tokens(["BTC", "ETH"])``, and
        ``.tokens("BTC")`` all work."""
        self._body["tokens"] = _flatten(symbols)
        return self

    def wallets(self, *addresses: str | list[str]) -> Self:
        """Filter by wallet address(es). Accepts varargs or a list (same forms
        as :meth:`tokens`)."""
        self._body["wallets"] = _flatten(addresses)
        return self

    def window(self, size: str) -> Self:
        """Bucket size for trade_history/position_history/ohlcv (e.g. ``'5m'``, ``'1h'``)."""
        self._body["window"] = size
        return self

    def per_token(self, flag: bool = True) -> Self:
        self._body["per_token"] = flag
        return self

    def skip_hip3(self, flag: bool = True) -> Self:
        """Exclude HIP3 (stock/commodity) markets when ``True``; server default when omitted."""
        self._body["skip_hip3"] = flag
        return self

    def market_type(self, t: str) -> Self:
        self._body["market_type"] = t
        return self

    def limit(self, n: int) -> Self:
        self._body["limit"] = n
        return self

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(self._session, self._base_url + self._hl_path, self._body)


class HyperliquidNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def fills(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/fills/read")

    def trades(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/trades/read")

    def ohlcv(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/ohlcv/read")

    def funding(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/funding/read")

    def transfers(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/transfers/read")

    def vaults(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/vaults/read")

    def sends(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/sends/read")

    def spot_transfers(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/spot_transfers/read")

    def trade_history(self) -> HyperliquidQuery:
        """Pre-aggregated PnL/volume per wallet-token-window. Requires ``tokens`` or ``wallets``."""
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/trade_history/read")

    def position_history(self) -> HyperliquidQuery:
        """Carry-forward position snapshots per window. Requires ``tokens`` or ``wallets``."""
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/position_history/read")
