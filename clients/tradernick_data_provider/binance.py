from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pyarrow as pa

from ._http import fetch_table
from ._query import CacheableQuery

if TYPE_CHECKING:
    from typing_extensions import Self


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


# Hyperliquid builders compose their chainables from capability mixins, since the
# endpoints differ in which filters make sense: tokens (all but transfers/vaults),
# wallets (all but ohlcv), window (ohlcv / position_history / realized_performance).
class _HLTokensMixin:
    _body: dict

    def tokens(self, *symbols: str | list[str]) -> Self:
        """Filter by HL token symbol(s). Varargs or a list —
        ``.tokens("BTC", "ETH")`` / ``.tokens(["BTC", "ETH"])`` / ``.tokens("BTC")``."""
        self._body["tokens"] = _flatten(symbols)
        return self


class _HLWalletsMixin:
    _body: dict

    def wallets(self, *addresses: str | list[str]) -> Self:
        """Filter by wallet address(es). Varargs or a list."""
        self._body["wallets"] = _flatten(addresses)
        return self

    def wallet_groups(self, *groups: str | list[str]) -> Self:
        """Filter by wallet **group** name(s) — the server resolves each group to
        its member addresses and matches like :meth:`wallets`. Varargs or a list;
        available wherever ``.wallets()`` is. Combines with ``.wallets()`` as a
        union (wallet in the list OR in any of the groups)."""
        self._body["wallet_groups"] = _flatten(groups)
        return self


class _HLWindowMixin:
    _body: dict

    def window(self, size: str) -> Self:
        """Bucket/snapshot size, e.g. ``'5m'`` / ``'1h'`` (ohlcv candles;
        position_history cadence; realized_performance windows, min 15m)."""
        self._body["window"] = size
        return self


class _HLBaseQuery(CacheableQuery):
    """Common Hyperliquid read builder — the always-available chainables. The
    per-endpoint capability mixins (tokens / wallets / window) are added by the
    concrete subclasses below."""

    def __init__(self, session: httpx.AsyncClient, base_url: str, path: str):
        super().__init__(session, base_url, {})
        self._hl_path = path

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

    def with_extra_cols(self, enabled: bool = True) -> Self:
        """``fills()`` only — include the columns it drops by default
        (``fee_token``, ``builder_fee``, ``crossed``, ``tid``, ``oid``, ``hash``)."""
        self._body["extra_cols"] = enabled
        return self

    async def _fetch_table(self) -> pa.Table:
        return await fetch_table(self._session, self._base_url + self._hl_path, self._body)


class _HLWalletQuery(_HLBaseQuery, _HLWalletsMixin):
    """Wallet-scoped, no token column: ``transfers`` / ``vaults``."""


class HyperliquidQuery(_HLBaseQuery, _HLTokensMixin, _HLWalletsMixin):
    """Token + wallet scoped: ``fills`` / ``trades`` / ``funding`` /
    ``sends`` / ``spot_transfers``."""


class _HLOhlcvQuery(_HLBaseQuery, _HLTokensMixin, _HLWindowMixin):
    """Token + window, NOT wallet-scoped (candles are market-wide): ``ohlcv``."""


class _HLPerfQuery(_HLOhlcvQuery, _HLWalletsMixin):
    """Token + wallet + window: ``position_history``."""


class _HLRealizedPerfQuery(_HLPerfQuery):
    """``realized_performance`` — adds ``.aggregate()``."""

    def aggregate(self, enabled: bool = True) -> Self:
        """Collapse the per-wallet rows into **per-(token, window)** group totals
        — SUM of every metric (pnl, fees, net_pnl, funding, volume, buy/sell
        volume, trade_count) across the selected wallets. Works in both snapshot
        and windowed modes. **Requires** ``.wallets()`` or ``.wallet_groups()``.
        The ``wallet`` column is dropped from the result."""
        self._body["aggregate"] = enabled
        return self


class HyperliquidNamespace:
    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    def fills(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/fills/read")

    def trades(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/trades/read")

    def ohlcv(self) -> _HLOhlcvQuery:
        """Candles (market-wide). Token-scoped + ``.window("1h")``; NOT wallet-scoped."""
        return _HLOhlcvQuery(self._session, self._base_url, "/hyperliquid/ohlcv/read")

    def funding(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/funding/read")

    def transfers(self) -> _HLWalletQuery:
        """Ledger transfers in/out of HL. Wallet-scoped (no ``.tokens()``)."""
        return _HLWalletQuery(self._session, self._base_url, "/hyperliquid/transfers/read")

    def vaults(self) -> _HLWalletQuery:
        """Vault deposits/withdrawals. Wallet-scoped (no ``.tokens()``)."""
        return _HLWalletQuery(self._session, self._base_url, "/hyperliquid/vaults/read")

    def sends(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/sends/read")

    def spot_transfers(self) -> HyperliquidQuery:
        return HyperliquidQuery(self._session, self._base_url, "/hyperliquid/spot_transfers/read")

    def realized_performance(self) -> _HLPerfQuery:
        """Realized PnL / fees / funding / volume per wallet-token. Requires
        ``tokens`` or ``wallets`` (or ``wallet_groups``).

        - **Snapshot mode** (no ``.window()``): raw DAILY absolute-cumulative
          rows (running totals from inception; ``time`` is start-aligned).
        - **Windowed mode** (``.window("15m"+)``): per-window *realized* (relative)
          metrics from fills+funding, stamped at the window start. Min 15m.

        Columns: time, wallet, token, pnl, fees, net_pnl, funding, volume,
        buy_volume, sell_volume, trade_count. ``.aggregate()`` sums across the
        selected wallets → per-(token, window) totals (drops ``wallet``)."""
        return _HLRealizedPerfQuery(self._session, self._base_url, "/hyperliquid/realized_performance/read")

    def position_history(self) -> _HLPerfQuery:
        """Carry-forward position snapshots per ``.window(...)``. Requires ``tokens`` or ``wallets``."""
        return _HLPerfQuery(self._session, self._base_url, "/hyperliquid/position_history/read")
