"""SQL builders for the Horatio-parity read routes.

Each builder returns (sql, params) for `clickhouse_connect.query_arrow`.
All builders are read-only — they never mutate schema. Network names follow
Horatio's lowercase conventions ('ethereum', 'arbitrum', ...) but the
uppercase chain literal aliases ('ETH', 'ARB', ...) also pass through so a
caller using either case gets the same result. Empty results return
(None, None); the route handler turns that into an empty-schema response,
which is Horatio's documented "unsupported network" behavior.

Column projections were validated against Horatio's populated response
shapes (not its declared empty schemas — Horatio's empty schemas are
narrower than the populated ones). See `tests/` or run the parity probe
in `scripts/` to re-validate against a live Horatio.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Network mapping. Horatio's client accepts 'ethereum' (lowercase), but its
# own API surface also routes uppercase chain literals like 'ETH'. We accept
# both so a parity test that ran against Horatio with `.network('ETH')` also
# works against TN unchanged.
# ---------------------------------------------------------------------------

NETWORK_TO_CHAIN = {
    'ethereum': 'ETH', 'eth': 'ETH',
    'arbitrum': 'ARB', 'arb': 'ARB',
    'optimism': 'OP',  'op':  'OP',
    'base':     'BASE','base_chain': 'BASE',
    'polygon':  'POLYGON',
    'bsc':      'BSC',
    'avalanche': 'AVAX', 'avax': 'AVAX',
}


def chain_from_network(network: str) -> str | None:
    if not network:
        return None
    key = network.lower()
    if key in NETWORK_TO_CHAIN:
        return NETWORK_TO_CHAIN[key]
    # Pass-through for already-uppercase chain literals not in the alias map
    # (e.g. a hypothetical 'POLY' that maps to itself). Keeps the door open
    # without baking every literal into the map.
    if network.upper() in set(NETWORK_TO_CHAIN.values()):
        return network.upper()
    return None


# ---------------------------------------------------------------------------
# Time projection helpers — used by every builder so the response's `time`
# column comes back as a known polars Datetime precision via Arrow. CH
# DateTime serializes as `timestamp[s]` which polars infers as Int32; the
# explicit `toDateTime64(time, 3|6, 'UTC')` wrap forces `timestamp[ms|us, UTC]`.
# ---------------------------------------------------------------------------

def _time_ms(col: str = 'time') -> str:
    return f"toDateTime64({col}, 3, 'UTC') AS {col}"


def _time_us(col: str = 'time') -> str:
    return f"toDateTime64({col}, 6, 'UTC') AS {col}"


def _ts_to_ch(s: str) -> str:
    """Strip Horatio's ISO 'Z' suffix to keep CH happy with naive UTC."""
    return s.replace('Z', '') if isinstance(s, str) else s


# ---------------------------------------------------------------------------
# Window parsing — shared across OHLCV builders
# ---------------------------------------------------------------------------

_WINDOW_RE = re.compile(r'^(\d+)([smhd])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def window_seconds(window: str) -> int:
    m = _WINDOW_RE.match(window or '')
    if not m:
        raise ValueError(f"Unsupported window: {window!r}. Use e.g. '1m', '5m', '1h', '1d'.")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


# ===========================================================================
# Binance
# ===========================================================================

def binance_ohlcv(token: str, window: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio populated shape:
       time(ms,UTC), token, open, close, high, low, volume,
       buyer_taker_volume, seller_taker_volume, trade_count(Int64).
    """
    secs = window_seconds(window)
    params: dict[str, Any] = {
        'token': token,
        'since': _ts_to_ch(since),
        'until': _ts_to_ch(until),
    }
    if secs == 60:
        sql = f"""
            SELECT
                {_time_ms()}, token, open, close, high, low, volume,
                buyer_taker_volume, seller_taker_volume,
                toInt64(trade_count) AS trade_count
            FROM tradernick.binance_ohlcv_1m FINAL
            WHERE token = {{token:String}}
              AND time >= toDateTime({{since:String}})
              AND time <  toDateTime({{until:String}})
            ORDER BY time
        """
    else:
        params['secs'] = secs
        # Wrap bucketed `toStartOfInterval` in toDateTime64(_, 3) so arrow
        # output is timestamp[ms, UTC]. Aggregate from `FINAL` so a pre-merge
        # duplicate 1m row doesn't double-count into the bucket — without
        # this, sum(volume) for the bucket can drift by ~2× until the
        # background ReplacingMT merge runs.
        sql = f"""
            SELECT
                toDateTime64(
                    toStartOfInterval(time, toIntervalSecond({{secs:UInt32}})),
                    3, 'UTC'
                ) AS time,
                {{token:String}}            AS token,
                argMin(open,  time)         AS open,
                argMax(close, time)         AS close,
                max(high)                   AS high,
                min(low)                    AS low,
                sum(volume)                 AS volume,
                sum(buyer_taker_volume)     AS buyer_taker_volume,
                sum(seller_taker_volume)    AS seller_taker_volume,
                toInt64(sum(trade_count))   AS trade_count
            FROM tradernick.binance_ohlcv_1m FINAL
            WHERE token = {{token:String}}
              AND time >= toDateTime({{since:String}})
              AND time <  toDateTime({{until:String}})
            GROUP BY time
            ORDER BY time
        """
    return sql, params


def binance_funding_rate(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, rate).

    Uses FINAL because backfills + the live sweep occasionally double-insert
    the same (token, time); ReplacingMT collapses them on merge but ad-hoc
    queries see both halves until merge runs. funding_rate is small (~3
    rows/token/day) so FINAL cost is negligible — same applies to
    open_interest, long_short_ratios."""
    sql = f"""
        SELECT {_time_ms()}, token, toFloat64(rate) AS rate
        FROM tradernick.binance_funding_rate FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime({{since:String}})
          AND time <  toDateTime({{until:String}})
        ORDER BY time
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


def binance_raw_trades(token: str, since: str, until: str,
                       *, with_id: bool = False) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, amount, price, buy). When
    `with_id=True` an `id` column is included as the last position."""
    extra = ', id' if with_id else ''
    sql = f"""
        SELECT {_time_ms()}, token, amount, price, buy{extra}
        FROM tradernick.binance_raw_trades FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime64({{since:String}}, 3)
          AND time <  toDateTime64({{until:String}}, 3)
        ORDER BY time, id
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


def binance_book_depth(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, percentage, depth, value).
    Multi-row per snapshot — no aggregation. FINAL collapses re-ingests."""
    sql = f"""
        SELECT {_time_ms()}, token, percentage, depth, value
        FROM tradernick.binance_book_depth FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime64({{since:String}}, 3)
          AND time <  toDateTime64({{until:String}}, 3)
        ORDER BY time, percentage
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


def binance_open_interest(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, open_interest, open_interest_value)."""
    sql = f"""
        SELECT {_time_ms()}, token, open_interest, open_interest_value
        FROM tradernick.binance_open_interest FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime({{since:String}})
          AND time <  toDateTime({{until:String}})
        ORDER BY time
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


def binance_long_short_ratios(token: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, top_trader_count_ratio,
    top_trader_vol_ratio, long_short_count_ratio, taker_long_short_vol_ratio).
    Cast Float32 → Float64 to match Horatio's polars dtypes."""
    sql = f"""
        SELECT
            {_time_ms()},
            token,
            toFloat64(top_trader_count_ratio)     AS top_trader_count_ratio,
            toFloat64(top_trader_vol_ratio)       AS top_trader_vol_ratio,
            toFloat64(long_short_count_ratio)     AS long_short_count_ratio,
            toFloat64(taker_long_short_vol_ratio) AS taker_long_short_vol_ratio
        FROM tradernick.binance_long_short_ratios FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime({{since:String}})
          AND time <  toDateTime({{until:String}})
        ORDER BY time
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


# ===========================================================================
# AAVE — per-event populated shapes (different per event)
# ===========================================================================

AAVE_EVENT_TABLE = {
    'deposit':     'aave_deposits',
    'withdraw':    'aave_withdrawals',
    'borrow':      'aave_borrows',
    'repay':       'aave_repays',
    'flashloan':   'aave_flashloans',
    'liquidation': 'aave_liquidations',
}

# Per-event column projection. Each entry is a list of either:
#   - "name"                  (column straight from CH)
#   - ("expr_or_col", "alias")  (CH expression with output alias)
# The output uses `time` last in every event (Horatio's convention).
_AAVE_PROJECTION = {
    'deposit': [
        'block_number', 'user', 'token', 'amount',
        'on_behalf_of', 'referral_code',
        ('_TIME_MS', 'time'),
    ],
    'withdraw': [
        'block_number', 'user', 'token', 'amount',
        'recipient',
        ('_TIME_MS', 'time'),
    ],
    'borrow': [
        'block_number', 'user', 'token', 'amount',
        'on_behalf_of', 'interest_rate_mode', 'borrow_rate',
        'referral_code',
        ('_TIME_MS', 'time'),
    ],
    'repay': [
        'block_number', 'user', 'token', 'amount',
        'repayer', ('toBool(use_a_tokens)', 'use_a_tokens'),
        ('_TIME_MS', 'time'),
    ],
    'flashloan': [
        'block_number', 'user', 'token', 'amount',
        'target', 'interest_rate_mode', 'premium', 'referral_code',
        ('_TIME_MS', 'time'),
    ],
    'liquidation': [
        'block_number', 'owner', 'liquidator', 'debt_token', 'collateral_token',
        'debt_to_cover', 'liquidated_collateral_amount',
        ('toBool(receive_a_token)', 'receive_a_token'),
        ('_TIME_MS', 'time'),
    ],
}


def _projection_clause(spec: list) -> str:
    parts: list[str] = []
    for item in spec:
        if isinstance(item, str):
            parts.append(item)
            continue
        expr, alias = item
        if expr == '_TIME_MS':
            parts.append(_time_ms(alias))
        elif expr == '_TIME_US':
            parts.append(_time_us(alias))
        else:
            parts.append(f'{expr} AS {alias}')
    return ', '.join(parts)


def evm_aave(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
    eth_market_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = AAVE_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown AAVE event: {event!r}. Valid: {list(AAVE_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    spec = _AAVE_PROJECTION[event]
    actor_col = 'owner' if event == 'liquidation' else 'user'
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
        where.append(f'lower({actor_col}) = {{involving:String}}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append(f'lower({actor_col}) != {{exclude_involving:String}}')
    sql = f"""
        SELECT {_projection_clause(spec)}
        FROM tradernick.{table} FINAL
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ===========================================================================
# Transfers — unified `transfers` table, discriminated by (kind, chain).
# Horatio shape: (block_number, token, sender, receiver, amount, time(ms,UTC)).
# `transfers` is the 971M-row table — FINAL is intentionally NOT used here
# per memory `transfers_streams_final_dropped` (65× slower on benchmarks
# with no correctness gain). The ReplacingMT merge collapses duplicates in
# the background; backfills idempotent re-runs converge.
# ===========================================================================

def _transfers_filters(params: dict[str, Any], where: list[str], *,
                       sender: str | None = None,
                       receiver: str | None = None,
                       involving: str | None = None,
                       exclude_sender: str | None = None,
                       exclude_receiver: str | None = None,
                       exclude_involving: str | None = None,
                       min_amount: float | None = None,
                       max_amount: float | None = None) -> None:
    """Mutate `where`/`params` with the common transfer filters."""
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


_TRANSFER_PROJECTION = f'block_number, token, sender, receiver, amount, {_time_ms()}'


def evm_erc20_transfers(
    network: str, tokens: list[str], since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain, 'tokens': tokens,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'erc20'",
        'chain = {chain:String}',
        'token IN {tokens:Array(String)}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT {_TRANSFER_PROJECTION}
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def evm_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
    }
    where = [
        "kind = 'native'",
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT {_TRANSFER_PROJECTION}
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def tron_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    if (network or '').lower() != 'tron':
        return None, None
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        "kind = 'tron_native'",
        "chain = 'TRON'",
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT {_TRANSFER_PROJECTION}
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def tron_trc20_transfers(
    network: str, tokens: list[str], since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    if (network or '').lower() != 'tron':
        return None, None
    params: dict[str, Any] = {
        'tokens': tokens,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
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
        SELECT {_TRANSFER_PROJECTION}
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


def btc_native_transfers(
    network: str, since: str, until: str, **filters: Any,
) -> tuple[str, dict[str, Any]]:
    if (network or '').lower() not in ('bitcoin', 'btc'):
        return None, None
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        "kind = 'btc'",
        "chain = 'BTC'",
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    _transfers_filters(params, where, **filters)
    sql = f"""
        SELECT {_TRANSFER_PROJECTION}
        FROM tradernick.transfers
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ===========================================================================
# Hyperliquid — `time` precision is microseconds (datetime[us, UTC]) for
# row-level events; the bucketed MV-backed endpoints stay at ms.
# ===========================================================================

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
    """Horatio shape: (time(us,UTC), token, open, close, high, low, volume,
    buyer_taker_volume, seller_taker_volume, trade_count). Column is `time`
    in populated frames (the empty schema's `window` name is Horatio-specific
    to its DataFrame layer, not the wire format)."""
    params: dict[str, Any] = {
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
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
                toDateTime64(
                    toStartOfInterval(time, toIntervalSecond({{secs:UInt32}})),
                    6, 'UTC'
                ) AS time,
                token,
                argMin(open,  time) AS open,
                argMax(close, time) AS close,
                max(high)           AS high,
                min(low)            AS low,
                sum(volume)         AS volume,
                sum(buyer_taker_volume)    AS buyer_taker_volume,
                sum(seller_taker_volume)   AS seller_taker_volume,
                toInt64(sum(trade_count))  AS trade_count
            FROM tradernick.hl_ohlcv_1m
            WHERE {' AND '.join(where)}
            GROUP BY time, token
            ORDER BY time, token
        """
    else:
        sql = f"""
            SELECT
                {_time_us()}, token, open, close, high, low, volume,
                buyer_taker_volume, seller_taker_volume,
                toInt64(trade_count) AS trade_count
            FROM tradernick.hl_ohlcv_1m
            WHERE {' AND '.join(where)}
            ORDER BY time, token
        """
    return sql, params


def hl_trades(since: str, until: str, *, tokens: list[str] | None = None,
              wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), token, price, amount, buy, id,
    buyer_wallet, seller_wallet, block_number)."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
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
        SELECT {_time_us()}, token, price, amount, buy, id,
               buyer_wallet, seller_wallet, block_number
        FROM tradernick.hl_trades
        WHERE {' AND '.join(where)}
        ORDER BY time, id
    """
    return sql, params


def hl_fills(since: str, until: str, *, tokens: list[str] | None = None,
             wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (block_number, block_time, time(us,UTC), wallet, token,
    price, size, side, dir, start_position, closed_pnl, fee, fee_token,
    builder_fee, crossed, tid, oid, hash). block_time also us-precision."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    sql = f"""
        SELECT block_number,
               {_time_us('block_time')},
               {_time_us()},
               wallet, token, price, size,
               side, dir, start_position, closed_pnl, fee, fee_token,
               builder_fee, toBool(crossed) AS crossed, tid, oid, hash
        FROM tradernick.hl_fills
        WHERE {' AND '.join(where)}
        ORDER BY time, tid, wallet
    """
    return sql, params


def hl_funding(since: str, until: str, *, tokens: list[str] | None = None,
               wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), token, wallet, rate, amount,
    position_amount, block_number)."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    _hl_token_wallet_filters(params, where, tokens=tokens, wallets=wallets)
    sql = f"""
        SELECT {_time_us()}, token, wallet, rate, amount,
               position_amount, block_number
        FROM tradernick.hl_funding
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_transfers(since: str, until: str, *,
                 wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), wallet, direction, amount, is_finalized,
    block_number)."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('lower(wallet) IN {wallets:Array(String)}')
    sql = f"""
        SELECT {_time_us()}, wallet, direction, amount,
               toBool(is_finalized) AS is_finalized, block_number
        FROM tradernick.hl_transfers
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_vaults(since: str, until: str, *,
              wallets: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), vault, wallet, action, amount,
    commission, fee, block_number)."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
    where = [
        'time >= toDateTime64({since:String}, 3)',
        'time <  toDateTime64({until:String}, 3)',
    ]
    if wallets:
        params['wallets'] = [w.lower() for w in wallets]
        where.append('lower(wallet) IN {wallets:Array(String)}')
    sql = f"""
        SELECT {_time_us()}, vault, wallet, action, amount,
               commission, fee, block_number
        FROM tradernick.hl_vaults
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_trade_history(since: str, until: str, *,
                     tokens: list[str] | None = None,
                     wallets: list[str] | None = None,
                     limit: int | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), wallet, token, pnl, fees, net_pnl,
    volume, buy_volume, sell_volume, trade_count(Int64))."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
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
        SELECT {_time_us()}, wallet, token, pnl, fees, net_pnl,
               volume, buy_volume, sell_volume,
               toInt64(trade_count) AS trade_count
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
    """Horatio shape: (time(us,UTC), wallet, token, side, amount, avg_entry,
    opened_at(string!), mark_price, size, unrealized_pnl, funding, fee,
    exact_avg_price(bool)). `opened_at` is a string per Horatio."""
    params: dict[str, Any] = {'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}
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
        SELECT {_time_us()}, wallet, token, side, amount, avg_entry,
               toString(opened_at) AS opened_at,
               mark_price, size, unrealized_pnl, funding, fee,
               toBool(exact_avg_price) AS exact_avg_price
        FROM tradernick.hl_position_history
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet, token
        {limit_clause}
    """
    return sql, params


# ===========================================================================
# Uniswap V3 — Horatio uses camelCase column names on swap rows
# (tokenSold/tokenBought/amountSold/amountBought) but snake_case on
# deposit/withdraw/collect. Mirror this verbatim.
# ===========================================================================

UNISWAP_EVENT_TABLE = {
    'swap':     'uniswap_swaps',
    'deposit':  'uniswap_deposits',
    'withdraw': 'uniswap_withdrawals',
    'collect':  'uniswap_collects',
}


_UNISWAP_SWAP_PROJECTION = [
    'block_number', 'pool_address', 'swapper', 'recipient',
    ('token_sold',   'tokenSold'),
    ('token_bought', 'tokenBought'),
    ('amount_sold',   'amountSold'),
    ('amount_bought', 'amountBought'),
    'sqrt_based_price', 'liquidity', 'tick',
    ('_TIME_MS', 'time'),
]

_UNISWAP_LP_PROJECTION = lambda actor_col: [
    'block_number', 'pool_address',
    ('actor', 'sender'),   # sender for deposits, owner for withdraw/collect
    'owner', 'amount0', 'amount1',
    ('symbol0', 'token0'), ('symbol1', 'token1'),
    'tick_lower', 'tick_upper', 'price_lower', 'price_upper',
    ('_TIME_MS', 'time'),
]


def evm_uniswap(
    event: str, network: str, symbol0: str | None, symbol1: str | None,
    fee: int | None, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = UNISWAP_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Uniswap event: {event!r}. Valid: {list(UNISWAP_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    # Uniswap pool naming is canonical — DeFiStream stores token pairs in
    # the order Uniswap itself ordered them (which is byte-address ordered,
    # NOT alphabetical). Callers don't know that ordering, so we accept
    # either (symbol0, symbol1) or (symbol1, symbol0) and match both.
    if symbol0 and symbol1:
        params['s0'] = symbol0
        params['s1'] = symbol1
        where.append(
            '((symbol0 = {s0:String} AND symbol1 = {s1:String}) '
            'OR (symbol0 = {s1:String} AND symbol1 = {s0:String}))'
        )
    elif symbol0:
        params['s0'] = symbol0
        where.append('(symbol0 = {s0:String} OR symbol1 = {s0:String})')
    elif symbol1:
        params['s1'] = symbol1
        where.append('(symbol0 = {s1:String} OR symbol1 = {s1:String})')
    if fee is not None:
        params['fee'] = int(fee)
        where.append('fee_tier = {fee:UInt32}')

    if event == 'swap':
        actor_col, recipient_col = 'swapper', 'recipient'
        spec = _UNISWAP_SWAP_PROJECTION
    elif event == 'deposit':
        # Horatio uses `sender` here as the deposit-actor column. CH stores
        # the deposit-side action under `sender`, so we project it as-is.
        actor_col, recipient_col = 'sender', 'owner'
        spec = [
            'block_number', 'pool_address', 'sender', 'owner',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'tick_lower', 'tick_upper', 'price_lower', 'price_upper',
            ('_TIME_MS', 'time'),
        ]
    elif event == 'withdraw':
        actor_col, recipient_col = 'owner', 'pool_address'
        spec = [
            'block_number', 'pool_address', 'owner',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'tick_lower', 'tick_upper', 'price_lower', 'price_upper',
            ('_TIME_MS', 'time'),
        ]
    else:  # collect
        actor_col, recipient_col = 'owner', 'recipient'
        spec = [
            'block_number', 'pool_address', 'owner', 'recipient',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'tick_lower', 'tick_upper', 'price_lower', 'price_upper',
            ('_TIME_MS', 'time'),
        ]

    if involving:
        params['involving'] = involving.lower()
        where.append(f'(lower({actor_col}) = {{involving:String}} '
                     f'OR lower({recipient_col}) = {{involving:String}})')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append(f'(lower({actor_col}) != {{exclude_involving:String}} '
                     f'AND lower({recipient_col}) != {{exclude_involving:String}})')

    sql = f"""
        SELECT {_projection_clause(spec)}
        FROM tradernick.{table} FINAL
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ===========================================================================
# Lido — per-event projections
# ===========================================================================

LIDO_EVENT_TABLE = {
    'deposit':                'lido_deposits',
    'withdrawal_request':     'lido_withdrawal_requests',
    'withdrawal_claimed':     'lido_withdrawal_claims',
    'l2_deposit':             'lido_l2_deposits',
    'l2_withdrawal_request':  'lido_l2_withdrawal_requests',
}

_LIDO_PROJECTION = {
    'deposit': [
        'block_number', 'sender', 'referral',
        'minted_amount', 'minted_token', ('_TIME_MS', 'time'),
    ],
    'withdrawal_request': [
        'block_number', 'request_id', 'requestor', 'owner',
        'burned_amount', 'burned_token', ('_TIME_MS', 'time'),
    ],
    'withdrawal_claimed': [
        'block_number', 'request_id', 'receiver', 'owner',
        'withdraw_amount', 'withdraw_token', 'burned_token',
        ('_TIME_MS', 'time'),
    ],
    # L2 deposit / l2_withdrawal_request — TN-only schemas (Horatio also
    # exposes these but its exact populated shape isn't documented; we
    # mirror the row-level columns and skip TN-specific extras).
    'l2_deposit': [
        'block_number', 'sender', 'receiver',
        'minted_amount', 'minted_token', ('_TIME_MS', 'time'),
    ],
    'l2_withdrawal_request': [
        'block_number', 'sender', 'receiver',
        'burned_amount', 'burned_token', ('_TIME_MS', 'time'),
    ],
}


def evm_lido(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = LIDO_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Lido event: {event!r}. Valid: {list(LIDO_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    spec = _LIDO_PROJECTION[event]
    actor_map = {
        'deposit':              'sender',
        'withdrawal_request':   'requestor',
        'withdrawal_claimed':   'receiver',
        'l2_deposit':           'sender',
        'l2_withdrawal_request': 'sender',
    }
    actor = actor_map[event]
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if involving:
        params['involving'] = involving.lower()
        where.append(f'lower({actor}) = {{involving:String}}')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        where.append(f'lower({actor}) != {{exclude_involving:String}}')
    sql = f"""
        SELECT {_projection_clause(spec)}
        FROM tradernick.{table} FINAL
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params
