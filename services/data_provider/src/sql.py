"""SQL builders for the Horatio-parity read routes.

Each builder returns (sql, params) for `clickhouse_connect.query_arrow`.
All builders are read-only — they never mutate schema. Network names
follow Horatio's lowercase conventions ('ethereum', 'arbitrum', ...);
they're mapped here to TraderNick's uppercase `chain` literals
('ETH', 'ARB', ...).
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Network mapping — Horatio → TraderNick chain literal
# ---------------------------------------------------------------------------

NETWORK_TO_CHAIN = {
    'ethereum': 'ETH',
    'arbitrum': 'ARB',
    'optimism': 'OP',
    'base':     'BASE',
    'polygon':  'POLYGON',
    'bsc':      'BSC',
}


def chain_from_network(network: str) -> str | None:
    """Return the CH `chain` literal for a Horatio network name, or
    None when we don't have ingestion coverage. Callers must short-circuit
    to an empty result on None — that's the documented Horatio behavior
    for unsupported networks (no error, just zero rows)."""
    return NETWORK_TO_CHAIN.get(network.lower()) if network else None


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _ts_to_ch(s: str) -> str:
    """Strip Horatio's ISO 'Z' suffix to keep CH happy with naive UTC."""
    return s.replace('Z', '') if isinstance(s, str) else s


# ---------------------------------------------------------------------------
# AAVE event → table
# ---------------------------------------------------------------------------

AAVE_EVENT_TABLE = {
    'deposit':     'aave_deposits',
    'withdraw':    'aave_withdrawals',
    'borrow':      'aave_borrows',
    'repay':       'aave_repays',
    'flashloan':   'aave_flashloans',
    'liquidation': 'aave_liquidations',
}


# ---------------------------------------------------------------------------
# OHLCV window parsing
# ---------------------------------------------------------------------------

_WINDOW_RE = re.compile(r'^(\d+)([smhd])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def window_seconds(window: str) -> int:
    m = _WINDOW_RE.match(window or '')
    if not m:
        raise ValueError(f"Unsupported window: {window!r}. Use e.g. '1m', '5m', '1h', '1d'.")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def binance_ohlcv(token: str, window: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """OHLCV: 1m is a direct SELECT; larger windows aggregate on the fly."""
    secs = window_seconds(window)
    params = {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    if secs == 60:
        sql = """
            SELECT time, open, high, low, close, volume
            FROM tradernick.binance_ohlcv_1m
            WHERE token = {token:String}
              AND time >= toDateTime({since:String})
              AND time <  toDateTime({until:String})
            ORDER BY time
        """
    else:
        params['secs'] = secs
        sql = """
            SELECT
                toStartOfInterval(time, toIntervalSecond({secs:UInt32})) AS time,
                argMin(open,  time) AS open,
                max(high)                  AS high,
                min(low)                   AS low,
                argMax(close, time) AS close,
                sum(volume)                AS volume
            FROM tradernick.binance_ohlcv_1m
            WHERE token = {token:String}
              AND time >= toDateTime({since:String})
              AND time <  toDateTime({until:String})
            GROUP BY time
            ORDER BY time
        """
    return sql, params


def binance_funding_rate(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio's funding_rate returns `(time, value)` — we project from `rate`."""
    sql = """
        SELECT time, toFloat64(rate) AS value
        FROM tradernick.binance_funding_rate
        WHERE token = {token:String}
          AND time >= toDateTime({since:String})
          AND time <  toDateTime({until:String})
        ORDER BY time
    """
    return sql, {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }


def binance_raw_trades(token: str, since: str, until: str,
                       *, with_id: bool = False) -> tuple[str, dict[str, Any]]:
    """Horatio's raw_trades schema: (id, time, price, amount, buy). `add_symbol`
    appends `symbol` as a constant column server-side (handled in app.py)."""
    cols = "id, time, price, amount, buy" if with_id else "time, price, amount, buy"
    sql = f"""
        SELECT {cols}
        FROM tradernick.binance_raw_trades
        WHERE token = {{token:String}}
          AND time >= toDateTime64({{since:String}}, 3)
          AND time <  toDateTime64({{until:String}}, 3)
        ORDER BY time, id
    """
    return sql, {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }


def binance_book_depth(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape (time, value). Book depth has multiple `percentage` rows
    per snapshot; we sum across percentages to surface the aggregate per
    snapshot — closest 1:1 mirror of Horatio's `value` projection."""
    sql = """
        SELECT time, toFloat64(sum(value)) AS value
        FROM tradernick.binance_book_depth
        WHERE token = {token:String}
          AND time >= toDateTime64({since:String}, 3)
          AND time <  toDateTime64({until:String}, 3)
        GROUP BY time
        ORDER BY time
    """
    return sql, {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }


def binance_open_interest(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape (time, value). We surface `open_interest_value` (USD)
    to match the value-column convention."""
    sql = """
        SELECT time, toFloat64(open_interest_value) AS value
        FROM tradernick.binance_open_interest
        WHERE token = {token:String}
          AND time >= toDateTime({since:String})
          AND time <  toDateTime({until:String})
        ORDER BY time
    """
    return sql, {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }


def binance_long_short_ratios(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape (time, value). TN stores four ratio dimensions; we use
    `long_short_count_ratio` (top-level position-count ratio) as the headline
    value column to match the single-series Horatio contract."""
    sql = """
        SELECT time, toFloat64(long_short_count_ratio) AS value
        FROM tradernick.binance_long_short_ratios
        WHERE token = {token:String}
          AND time >= toDateTime({since:String})
          AND time <  toDateTime({until:String})
        ORDER BY time
    """
    return sql, {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }


def evm_aave(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
    eth_market_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """AAVE v3 events. Output shape: `(block_number, user, token, amount, time)`."""
    table = AAVE_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown AAVE event: {event!r}. Valid: {list(AAVE_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if eth_market_type:
        params['eth_market'] = eth_market_type
        where.append('eth_market = {eth_market:String}')
    if involving:
        params['involving'] = involving.lower()
        where.append('lower(user) = {involving:String}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append('lower(user) != {exclude_involving:String}')
    sql = f"""
        SELECT block_number, user, token, amount, time
        FROM tradernick.{table}
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def evm_erc20_transfers(
    network: str, tokens: list[str], since: str, until: str,
    *, sender: str | None = None,
    receiver: str | None = None,
    involving: str | None = None,
    exclude_sender: str | None = None,
    exclude_receiver: str | None = None,
    exclude_involving: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """ERC-20 transfers. Output shape: `(block_number, token, sender, receiver, amount, time)`."""
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'tokens': tokens,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'erc20'",
        'chain = {chain:String}',
        'token IN {tokens:Array(String)}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if sender:
        params['sender'] = sender.lower()
        where.append('lower(sender) = {sender:String}')
    if receiver:
        params['receiver'] = receiver.lower()
        where.append('lower(receiver) = {receiver:String}')
    if involving:
        params['involving'] = involving.lower()
        where.append('(lower(sender) = {involving:String} OR lower(receiver) = {involving:String})')
    if exclude_sender:
        params['exclude_sender'] = exclude_sender.lower()
        where.append('lower(sender) != {exclude_sender:String}')
    if exclude_receiver:
        params['exclude_receiver'] = exclude_receiver.lower()
        where.append('lower(receiver) != {exclude_receiver:String}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append('(lower(sender) != {exclude_involving:String} AND lower(receiver) != {exclude_involving:String})')
    if min_amount is not None:
        params['min_amount'] = float(min_amount)
        where.append('amount >= {min_amount:Float64}')
    if max_amount is not None:
        params['max_amount'] = float(max_amount)
        where.append('amount <= {max_amount:Float64}')
    sql = f"""
        SELECT block_number, token, sender, receiver, amount, time
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ---------------------------------------------------------------------------
# Transfers — unified `transfers` table, discriminated by (kind, chain).
# ---------------------------------------------------------------------------

def _transfers_filters(params: dict[str, Any], where: list[str], *,
                       sender: str | None = None,
                       receiver: str | None = None,
                       involving: str | None = None,
                       exclude_sender: str | None = None,
                       exclude_receiver: str | None = None,
                       exclude_involving: str | None = None,
                       min_amount: float | None = None,
                       max_amount: float | None = None) -> None:
    """Mutate `where`/`params` with the common transfer filters.
    Same casing rules as evm_erc20_transfers: addresses lowered, EVM-only."""
    if sender:
        params['sender'] = sender.lower()
        where.append('lower(sender) = {sender:String}')
    if receiver:
        params['receiver'] = receiver.lower()
        where.append('lower(receiver) = {receiver:String}')
    if involving:
        params['involving'] = involving.lower()
        where.append('(lower(sender) = {involving:String} OR lower(receiver) = {involving:String})')
    if exclude_sender:
        params['exclude_sender'] = exclude_sender.lower()
        where.append('lower(sender) != {exclude_sender:String}')
    if exclude_receiver:
        params['exclude_receiver'] = exclude_receiver.lower()
        where.append('lower(receiver) != {exclude_receiver:String}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append('(lower(sender) != {exclude_involving:String} AND lower(receiver) != {exclude_involving:String})')
    if min_amount is not None:
        params['min_amount'] = float(min_amount)
        where.append('amount >= {min_amount:Float64}')
    if max_amount is not None:
        params['max_amount'] = float(max_amount)
        where.append('amount <= {max_amount:Float64}')


def evm_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    """EVM native transfers — `transfers` filtered to kind='native', chain=<EVM>.
    Same output shape as Horatio: (block_number, token, sender, receiver, amount, time)."""
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'native'",
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT block_number, token, sender, receiver, amount, time
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# TRON: `network` maps to chain='TRON'. Both kind='tron_native' (TRX) and
# kind='trc20' (token transfers) live in the same table.

def tron_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    """TRON native (TRX) transfers — kind='tron_native'."""
    if (network or '').lower() != 'tron':
        return None, None
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'tron_native'",
        "chain = 'TRON'",
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT block_number, token, sender, receiver, amount, time
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def tron_trc20_transfers(
    network: str, tokens: list[str], since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    """TRON TRC-20 transfers — kind='trc20', token filtered to caller-supplied list."""
    if (network or '').lower() != 'tron':
        return None, None
    params: dict[str, Any] = {
        'tokens': tokens,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'trc20'",
        "chain = 'TRON'",
        'token IN {tokens:Array(String)}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT block_number, token, sender, receiver, amount, time
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def btc_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    """BTC native transfers — kind='btc', chain='BTC'."""
    if (network or '').lower() not in ('bitcoin', 'btc'):
        return None, None
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'btc'",
        "chain = 'BTC'",
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT block_number, token, sender, receiver, amount, time
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ---------------------------------------------------------------------------
# Hyperliquid
# ---------------------------------------------------------------------------

def _hl_token_wallet_filters(params: dict[str, Any], where: list[str], *,
                             tokens: list[str] | None = None,
                             wallets: list[str] | None = None) -> None:
    if tokens:
        params['tokens'] = list(tokens)
        where.append('token IN {tokens:Array(String)}')
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('lower(wallet) IN {wallets:Array(String)}')


def hl_ohlcv(since: str, until: str, *, tokens: list[str] | None = None,
             window: str | None = None) -> tuple[str, dict[str, Any]]:
    """HL OHLCV. Horatio's column is `window`, not `time` — keep that name so
    polars can rename to `time` on the client side per its convention."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if tokens:
        params['tokens'] = list(tokens)
        where.append('token IN {tokens:Array(String)}')

    if window and window != '1m':
        secs = window_seconds(window)
        params['secs'] = secs
        sql = f"""
            SELECT
                token,
                toStartOfInterval(time, toIntervalSecond({{secs:UInt32}})) AS window,
                argMin(open,  time) AS open,
                max(high)           AS high,
                min(low)            AS low,
                argMax(close, time) AS close,
                sum(volume)         AS volume,
                sum(buyer_taker_volume)  AS buyer_taker_volume,
                sum(seller_taker_volume) AS seller_taker_volume,
                toUInt32(sum(trade_count)) AS trade_count
            FROM tradernick.hl_ohlcv_1m
            WHERE {' AND '.join(where)}
            GROUP BY token, window
            ORDER BY window, token
        """
    else:
        sql = f"""
            SELECT
                token,
                time AS window,
                open, high, low, close, volume,
                buyer_taker_volume, seller_taker_volume, trade_count
            FROM tradernick.hl_ohlcv_1m
            WHERE {' AND '.join(where)}
            ORDER BY window, token
        """
    return sql, params


def hl_trades(since: str, until: str, *, tokens: list[str] | None = None,
              wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """HL public trades. Horatio columns: time, token, price, amount, buy, id,
    buyer_wallet, seller_wallet, block_number."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    if tokens:
        params['tokens'] = list(tokens)
        where.append('token IN {tokens:Array(String)}')
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('(lower(buyer_wallet) IN {wallets:Array(String)} '
                     'OR lower(seller_wallet) IN {wallets:Array(String)})')
    sql = f"""
        SELECT time, token, price, amount, buy, id,
               buyer_wallet, seller_wallet, block_number
        FROM tradernick.hl_trades
        WHERE {' AND '.join(where)}
        ORDER BY time, id
    """
    return sql, params


def hl_fills(since: str, until: str, *, tokens: list[str] | None = None,
             wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """HL fills. Horatio columns: block_number, block_time, time, wallet,
    token, price, size, side, dir, start_position, closed_pnl, fee, fee_token,
    builder_fee, crossed, tid, oid, hash."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    sql = f"""
        SELECT block_number, block_time, time, wallet, token, price, size,
               side, dir, start_position, closed_pnl, fee, fee_token,
               builder_fee, toBool(crossed) AS crossed, tid, oid, hash
        FROM tradernick.hl_fills
        WHERE {' AND '.join(where)}
        ORDER BY time, tid, wallet
    """
    return sql, params


def hl_funding(since: str, until: str, *, tokens: list[str] | None = None,
               wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """HL per-wallet funding. Horatio columns: time, token, wallet, rate,
    amount, position_amount, block_number."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    sql = f"""
        SELECT time, token, wallet, rate, amount, position_amount, block_number
        FROM tradernick.hl_funding
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_transfers(since: str, until: str, *,
                 wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """HL bridge in/out. Horatio columns: time, wallet, direction, amount,
    is_finalized, block_number."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('lower(wallet) IN {wallets:Array(String)}')
    sql = f"""
        SELECT time, wallet, direction, amount,
               toBool(is_finalized) AS is_finalized, block_number
        FROM tradernick.hl_transfers
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_vaults(since: str, until: str, *,
              wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """HL vault subscriptions. Horatio columns: time, vault, wallet, action,
    amount, commission, fee, block_number."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('lower(wallet) IN {wallets:Array(String)}')
    sql = f"""
        SELECT time, vault, wallet, action, amount, commission, fee, block_number
        FROM tradernick.hl_vaults
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_trade_history(since: str, until: str, *,
                     tokens: list[str] | None = None,
                     wallets: list[str] | None = None,
                     limit: int | None = None) -> tuple[str, dict[str, Any]]:
    """Pre-aggregated per-(wallet,token,bucket) trader performance.
    Horatio columns: time, wallet, token, pnl, fees, net_pnl, volume,
    buy_volume, sell_volume, trade_count."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    limit_clause = ''
    if limit is not None:
        params['limit'] = int(limit)
        limit_clause = 'LIMIT {limit:UInt32}'
    sql = f"""
        SELECT time, wallet, token, pnl, fees, net_pnl,
               volume, buy_volume, sell_volume, trade_count
        FROM tradernick.hl_trade_history
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet, token
        {limit_clause}
    """
    return sql, params


def hl_position_history(since: str, until: str, *,
                        tokens: list[str] | None = None,
                        wallets: list[str] | None = None,
                        window: str | None = None,
                        limit: int | None = None) -> tuple[str, dict[str, Any]]:
    """Position snapshots. Horatio columns: time, wallet, token, side, amount,
    avg_entry, opened_at, mark_price, size, unrealized_pnl, funding, fee,
    exact_avg_price. We read from the raw hl_position_history table; the
    `window` arg is accepted but only the source granularity (5m) is exposed
    in Phase 1 — coarser MVs are TN-internal and not in Horatio's contract."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    limit_clause = ''
    if limit is not None:
        params['limit'] = int(limit)
        limit_clause = 'LIMIT {limit:UInt32}'
    sql = f"""
        SELECT time, wallet, token, side, amount, avg_entry,
               toString(opened_at) AS opened_at,
               mark_price, size, unrealized_pnl, funding, fee,
               toBool(exact_avg_price) AS exact_avg_price
        FROM tradernick.hl_position_history
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet, token
        {limit_clause}
    """
    return sql, params


# ---------------------------------------------------------------------------
# Uniswap V3 — Horatio's surface is V3 only. v2/v4 stay TN-exclusive for now.
# ---------------------------------------------------------------------------

UNISWAP_EVENT_TABLE = {
    'swap':     'uniswap_swaps',
    'deposit':  'uniswap_deposits',
    'withdraw': 'uniswap_withdrawals',
    'collect':  'uniswap_collects',
}

# Horatio's `_EMPTY_UNISWAP` is (block_number, sender, recipient, amount0,
# amount1, time). Map TN's V3 columns to that shape per event:
#   swaps:        swapper -> sender,   recipient -> recipient, amount_sold/amount_bought -> amount0/amount1
#   deposits:     sender,                                       amount0/amount1
#   withdrawals:  owner -> sender,    pool_address -> recipient (placeholder), amount0/amount1
#   collects:     owner -> sender,    recipient,                amount0/amount1


def evm_uniswap(
    event: str, network: str, symbol0: str | None, symbol1: str | None,
    fee: int | None, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Uniswap V3 events. Shape: (block_number, sender, recipient, amount0, amount1, time)."""
    table = UNISWAP_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Uniswap event: {event!r}. Valid: {list(UNISWAP_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if symbol0:
        params['symbol0'] = symbol0
        where.append('symbol0 = {symbol0:String}')
    if symbol1:
        params['symbol1'] = symbol1
        where.append('symbol1 = {symbol1:String}')
    if fee is not None:
        params['fee'] = int(fee)
        where.append('fee_tier = {fee:UInt32}')

    if event == 'swap':
        sender_col = 'swapper'
        recipient_col = 'recipient'
        a0, a1 = 'amount_sold', 'amount_bought'
    elif event == 'deposit':
        sender_col = 'sender'
        recipient_col = 'owner'
        a0, a1 = 'amount0', 'amount1'
    elif event == 'withdraw':
        sender_col = 'owner'
        recipient_col = 'pool_address'
        a0, a1 = 'amount0', 'amount1'
    else:  # collect
        sender_col = 'owner'
        recipient_col = 'recipient'
        a0, a1 = 'amount0', 'amount1'

    if involving:
        params['involving'] = involving.lower()
        where.append(f'(lower({sender_col}) = {{involving:String}} '
                     f'OR lower({recipient_col}) = {{involving:String}})')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append(f'(lower({sender_col}) != {{exclude_involving:String}} '
                     f'AND lower({recipient_col}) != {{exclude_involving:String}})')

    sql = f"""
        SELECT block_number,
               {sender_col}    AS sender,
               {recipient_col} AS recipient,
               {a0}            AS amount0,
               {a1}            AS amount1,
               time
        FROM tradernick.{table}
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ---------------------------------------------------------------------------
# Lido (mainnet + L2)
# ---------------------------------------------------------------------------

LIDO_EVENT_TABLE = {
    'deposit':                'lido_deposits',
    'withdrawal_request':     'lido_withdrawal_requests',
    'withdrawal_claimed':     'lido_withdrawal_claims',
    'l2_deposit':             'lido_l2_deposits',
    'l2_withdrawal_request':  'lido_l2_withdrawal_requests',
}


def evm_lido(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Lido events. Horatio empty: (block_number, sender, minted_amount,
    minted_token, time). Per-event column projection picks the right
    actor + amount/token field."""
    table = LIDO_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Lido event: {event!r}. Valid: {list(LIDO_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]

    if event == 'deposit':
        actor, amount, token = 'sender', 'minted_amount', 'minted_token'
    elif event == 'withdrawal_request':
        actor, amount, token = 'requestor', 'burned_amount', 'burned_token'
    elif event == 'withdrawal_claimed':
        actor, amount, token = 'receiver', 'withdraw_amount', 'withdraw_token'
    elif event == 'l2_deposit':
        actor, amount, token = 'sender', 'minted_amount', 'minted_token'
    else:  # l2_withdrawal_request
        actor, amount, token = 'sender', 'burned_amount', 'burned_token'

    if involving:
        params['involving'] = involving.lower()
        where.append(f'lower({actor}) = {{involving:String}}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append(f'lower({actor}) != {{exclude_involving:String}}')

    sql = f"""
        SELECT block_number,
               {actor}  AS sender,
               {amount} AS minted_amount,
               {token}  AS minted_token,
               time
        FROM tradernick.{table}
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params
