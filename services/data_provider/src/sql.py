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

def binance_ohlcv(token: str, window: str, since: str, until: str,
                  *, table: str = 'binance_ohlcv_1m') -> tuple[str, dict[str, Any]]:
    """Horatio populated shape:
       time(ms,UTC), token, open, close, high, low, volume,
       buyer_taker_volume, seller_taker_volume, trade_count(Int64).

    `table` selects the source: the default perp `binance_ohlcv_1m`, or the
    spot `binance_spot_ohlcv_1m` (identical schema) via `binance_spot_ohlcv`.
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
            FROM tradernick.{table} FINAL
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
            FROM tradernick.{table} FINAL
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
                       *, with_id: bool = False,
                       table: str = 'binance_raw_trades') -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(ms,UTC), token, amount, price, buy). When
    `with_id=True` an `id` column is included as the last position.

    `table` selects the source: the default perp `binance_raw_trades`, or the
    spot `binance_raw_spot_trades` (identical schema) via `binance_spot_raw_trades`.
    """
    extra = ', id' if with_id else ''
    sql = f"""
        SELECT {_time_ms()}, token, amount, price, buy{extra}
        FROM tradernick.{table} FINAL
        WHERE token = {{token:String}}
          AND time >= toDateTime64({{since:String}}, 3)
          AND time <  toDateTime64({{until:String}}, 3)
        ORDER BY time, id
    """
    return sql, {'token': token, 'since': _ts_to_ch(since), 'until': _ts_to_ch(until)}


# Binance SPOT — same builders, pointed at the spot tables. The spot schema is
# byte-identical to perp (see clickhouse/init/01_schema.sql), so the SQL and
# the empty-frame templates are shared; only the source table differs.
def binance_spot_ohlcv(token: str, window: str, since: str, until: str) -> tuple[str, dict[str, Any]]:
    return binance_ohlcv(token, window, since, until, table='binance_spot_ohlcv_1m')


def binance_spot_raw_trades(token: str, since: str, until: str,
                            *, with_id: bool = False) -> tuple[str, dict[str, Any]]:
    return binance_raw_trades(
        token, since, until, with_id=with_id, table='binance_raw_spot_trades',
    )


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
                       sender_label: str | None = None,
                       receiver_label: str | None = None,
                       involving_label: str | None = None,
                       exclude_sender_label: str | None = None,
                       exclude_receiver_label: str | None = None,
                       exclude_involving_label: str | None = None,
                       sender_category: str | None = None,
                       receiver_category: str | None = None,
                       involving_category: str | None = None,
                       exclude_sender_category: str | None = None,
                       exclude_receiver_category: str | None = None,
                       exclude_involving_category: str | None = None,
                       sender_groups: list[str] | None = None,
                       receiver_groups: list[str] | None = None,
                       involving_groups: list[str] | None = None,
                       exclude_sender_groups: list[str] | None = None,
                       exclude_receiver_groups: list[str] | None = None,
                       exclude_involving_groups: list[str] | None = None,
                       min_amount: float | None = None,
                       max_amount: float | None = None) -> None:
    """Mutate `where`/`params` with the wallet-selection filters the
    transfers tables support.

    Pushdown maps:

      - address  (`sender` / `receiver` / `involving`)
            → `lower(sender|receiver) = lower(addr)`
              Case-insensitive for EVM (the transfers row stores raw mixed
              case; `lower(sender)` evaluates per row). BTC/TRON addresses
              the user passes through unchanged via `lower()` which is a
              no-op for already-lowercase addresses.

      - label    (`*_label`)
            → `sender_entity = lower(label)` — interpreting Horatio's
              "label" as TN's entity tag (e.g. "Binance"). TN doesn't
              have a per-address label/nickname column; entity is the
              closest semantic. The materialized `sender_entity` column
              is pre-lowered.

      - category (`*_category`)
            → `has(sender_categories, lower(category))`. The
              `sender_categories` materialized column is pre-lowered
              `Array(LowCardinality(String))`, populated by the
              dictGet('tradernick.wallet_labels', 'categories_lower', addr)
              ALTER on the transfers schema.

    Skip indexes (set() on sender_categories / receiver_categories /
    sender_entity / receiver_entity) make these pushdown filters cheap
    even on the 971M-row transfers table — the test for the dashboard's
    exchange-flow rollup is the same pattern.
    """
    # Every wallet-selection filter is list-valued (match ANY of the values).
    # A bare string is accepted and wrapped. Empty → the clause is skipped.
    def _nl(v):
        if v is None:
            return None
        vals = v if isinstance(v, (list, tuple)) else [v]
        out = [str(x).lower() for x in vals if x is not None and str(x) != '']
        return out or None

    # ----- address (IN / NOT IN) ---------------------------------------------
    if (v := _nl(sender)):
        params['sender'] = v
        where.append('lower(sender) IN {sender:Array(String)}')
    if (v := _nl(receiver)):
        params['receiver'] = v
        where.append('lower(receiver) IN {receiver:Array(String)}')
    if (v := _nl(involving)):
        params['involving'] = v
        where.append('(lower(sender) IN {involving:Array(String)} OR lower(receiver) IN {involving:Array(String)})')
    if (v := _nl(exclude_sender)):
        params['exclude_sender'] = v
        where.append('lower(sender) NOT IN {exclude_sender:Array(String)}')
    if (v := _nl(exclude_receiver)):
        params['exclude_receiver'] = v
        where.append('lower(receiver) NOT IN {exclude_receiver:Array(String)}')
    if (v := _nl(exclude_involving)):
        params['exclude_involving'] = v
        where.append('(lower(sender) NOT IN {exclude_involving:Array(String)} AND lower(receiver) NOT IN {exclude_involving:Array(String)})')

    # ----- label (entity) — IN / NOT IN over the entity column ----------------
    if (v := _nl(sender_label)):
        params['sender_label'] = v
        where.append("coalesce(sender_entity, '') IN {sender_label:Array(String)}")
    if (v := _nl(receiver_label)):
        params['receiver_label'] = v
        where.append("coalesce(receiver_entity, '') IN {receiver_label:Array(String)}")
    if (v := _nl(involving_label)):
        params['involving_label'] = v
        where.append(
            "(coalesce(sender_entity, '') IN {involving_label:Array(String)} "
            "OR coalesce(receiver_entity, '') IN {involving_label:Array(String)})"
        )
    if (v := _nl(exclude_sender_label)):
        params['exclude_sender_label'] = v
        where.append("coalesce(sender_entity, '') NOT IN {exclude_sender_label:Array(String)}")
    if (v := _nl(exclude_receiver_label)):
        params['exclude_receiver_label'] = v
        where.append("coalesce(receiver_entity, '') NOT IN {exclude_receiver_label:Array(String)}")
    if (v := _nl(exclude_involving_label)):
        params['exclude_involving_label'] = v
        where.append(
            "(coalesce(sender_entity, '') NOT IN {exclude_involving_label:Array(String)} "
            "AND coalesce(receiver_entity, '') NOT IN {exclude_involving_label:Array(String)})"
        )

    # ----- category — hasAny / NOT hasAny over the categories array -----------
    if (v := _nl(sender_category)):
        params['sender_category'] = v
        where.append('hasAny(sender_categories, {sender_category:Array(String)})')
    if (v := _nl(receiver_category)):
        params['receiver_category'] = v
        where.append('hasAny(receiver_categories, {receiver_category:Array(String)})')
    if (v := _nl(involving_category)):
        params['involving_category'] = v
        where.append(
            '(hasAny(sender_categories, {involving_category:Array(String)}) '
            'OR hasAny(receiver_categories, {involving_category:Array(String)}))'
        )
    if (v := _nl(exclude_sender_category)):
        params['exclude_sender_category'] = v
        where.append('NOT hasAny(sender_categories, {exclude_sender_category:Array(String)})')
    if (v := _nl(exclude_receiver_category)):
        params['exclude_receiver_category'] = v
        where.append('NOT hasAny(receiver_categories, {exclude_receiver_category:Array(String)})')
    if (v := _nl(exclude_involving_category)):
        params['exclude_involving_category'] = v
        where.append(
            '(NOT hasAny(sender_categories, {exclude_involving_category:Array(String)}) '
            'AND NOT hasAny(receiver_categories, {exclude_involving_category:Array(String)}))'
        )

    # ----- group ----------------------------------------------------------
    # A "group" is a named wallet set from tradernick.wallet_groups /
    # wallet_pins (address↔group membership), scoped to user_id 'local'.
    # Resolved to member addresses at query time via an inline subquery — no
    # materialized column, so edits to a group take effect immediately. The
    # filter values are group NAMES (case-insensitive); each is list-valued
    # (match any of several groups). Semantics mirror the address filters:
    # involving = sender OR receiver, exclude = NOT IN.
    def _group_members(pname: str) -> str:
        return (
            'SELECT lower(address) FROM tradernick.wallet_pins FINAL '
            'WHERE user_id = {group_user_id:String} AND deleted = 0 '
            'AND group_id IN ('
            'SELECT group_id FROM tradernick.wallet_groups FINAL '
            'WHERE user_id = {group_user_id:String} AND deleted = 0 '
            'AND lower(name) IN {' + pname + ':Array(String)})'
        )

    _sg, _rg, _ig = _nl(sender_groups), _nl(receiver_groups), _nl(involving_groups)
    _xsg, _xrg, _xig = _nl(exclude_sender_groups), _nl(exclude_receiver_groups), _nl(exclude_involving_groups)
    if any((_sg, _rg, _ig, _xsg, _xrg, _xig)):
        params['group_user_id'] = 'local'
    if _sg:
        params['sender_groups'] = _sg
        where.append(f'lower(sender) IN ({_group_members("sender_groups")})')
    if _rg:
        params['receiver_groups'] = _rg
        where.append(f'lower(receiver) IN ({_group_members("receiver_groups")})')
    if _ig:
        params['involving_groups'] = _ig
        sub = _group_members("involving_groups")
        where.append(f'(lower(sender) IN ({sub}) OR lower(receiver) IN ({sub}))')
    if _xsg:
        params['exclude_sender_groups'] = _xsg
        where.append(f'lower(sender) NOT IN ({_group_members("exclude_sender_groups")})')
    if _xrg:
        params['exclude_receiver_groups'] = _xrg
        where.append(f'lower(receiver) NOT IN ({_group_members("exclude_receiver_groups")})')
    if _xig:
        params['exclude_involving_groups'] = _xig
        sub = _group_members("exclude_involving_groups")
        where.append(f'(lower(sender) NOT IN ({sub}) AND lower(receiver) NOT IN ({sub}))')

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
        FROM tradernick.transfers FINAL
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
        FROM tradernick.transfers FINAL
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
        FROM tradernick.transfers FINAL
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
        FROM tradernick.transfers FINAL
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
        FROM tradernick.transfers FINAL
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
            -- FINAL: hl_ohlcv_1m is a ReplacingMergeTree. The sum() columns
            -- below would double-count a pre-merge duplicate 1m row into the
            -- resampled bucket (drift up to ~2× until the background merge
            -- runs) — same reasoning as the binance_ohlcv_1m resample above.
            FROM tradernick.hl_ohlcv_1m FINAL
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
            FROM tradernick.hl_ohlcv_1m FINAL
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
        FROM tradernick.hl_trades FINAL
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
        FROM tradernick.hl_fills FINAL
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
        FROM tradernick.hl_funding FINAL
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
        FROM tradernick.hl_transfers FINAL
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
        FROM tradernick.hl_vaults FINAL
        WHERE {' AND '.join(where)}
        ORDER BY time, wallet
    """
    return sql, params


def hl_trade_history(since: str, until: str, *,
                     tokens: list[str] | None = None,
                     wallets: list[str] | None = None,
                     limit: int | None = None) -> tuple[str, dict[str, Any]]:
    """Horatio shape: (time(us,UTC), wallet, token, pnl, fees, net_pnl,
    volume, buy_volume, sell_volume, trade_count(Int64)).

    NOTE (2026-06 semantics change): rows are now DAILY snapshots whose metric
    columns are ABSOLUTE — cumulative from the wallet's inception, not per-bucket
    deltas. To get a window's realized value, diff two snapshots
    (snapshot(until) − snapshot(since)); for sub-day precision add the
    hl_fills / hl_funding tail since the last daily snapshot. This is a raw
    passthrough (no aggregation), so it faithfully returns those absolute daily
    rows — downstream Horatio consumers must apply the diff themselves."""
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
        FROM tradernick.hl_trade_history FINAL
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
        FROM tradernick.hl_position_history FINAL
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


# ===========================================================================
# Spark — TN-exclusive. Schema mirrors AAVE 1:1; we expose the same six
# events with identical projections so consumers can swap `evm.aave.*` for
# `evm.spark.*` without re-shaping their downstream code.
# ===========================================================================

SPARK_EVENT_TABLE = {
    'deposit':     'spark_deposits',
    'withdraw':    'spark_withdrawals',
    'borrow':      'spark_borrows',
    'repay':       'spark_repays',
    'flashloan':   'spark_flashloans',
    'liquidation': 'spark_liquidations',
}

# Spark schemas are byte-identical to AAVE — reuse the projection map.
_SPARK_PROJECTION = _AAVE_PROJECTION


def evm_spark(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = SPARK_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Spark event: {event!r}. Valid: {list(SPARK_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    spec = _SPARK_PROJECTION[event]
    actor_col = 'owner' if event == 'liquidation' else 'user'
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
# Morpho — TN-exclusive. Different model from AAVE/Spark: per-market lending
# keyed by `market_id`, with `assets` + `shares` instead of a single
# `amount`. Liquidations carry repaid/seized + bad-debt vectors.
# ===========================================================================

MORPHO_EVENT_TABLE = {
    'supply':            'morpho_supplies',
    'withdraw':          'morpho_withdrawals',
    'borrow':            'morpho_borrows',
    'repay':             'morpho_repays',
    'supply_collateral': 'morpho_supply_collaterals',
    'withdraw_collateral': 'morpho_withdraw_collaterals',
    'liquidation':       'morpho_liquidations',
}

_MORPHO_PROJECTION = {
    'supply': [
        'block_number', 'market_id', 'caller', 'on_behalf',
        'token', 'assets', 'shares', ('_TIME_MS', 'time'),
    ],
    'withdraw': [
        'block_number', 'market_id', 'caller', 'on_behalf', 'receiver',
        'token', 'assets', 'shares', ('_TIME_MS', 'time'),
    ],
    'borrow': [
        'block_number', 'market_id', 'caller', 'on_behalf', 'receiver',
        'token', 'assets', 'shares', ('_TIME_MS', 'time'),
    ],
    'repay': [
        'block_number', 'market_id', 'caller', 'on_behalf',
        'token', 'assets', 'shares', ('_TIME_MS', 'time'),
    ],
    'supply_collateral': [
        'block_number', 'market_id', 'caller', 'on_behalf',
        'token', 'assets', ('_TIME_MS', 'time'),
    ],
    'withdraw_collateral': [
        'block_number', 'market_id', 'caller', 'on_behalf', 'receiver',
        'token', 'assets', ('_TIME_MS', 'time'),
    ],
    'liquidation': [
        'block_number', 'market_id', 'caller', 'borrower',
        'loan_token', 'collateral_token',
        'repaid_assets', 'repaid_shares', 'seized_assets',
        'bad_debt_assets', 'bad_debt_shares',
        ('_TIME_MS', 'time'),
    ],
}

# Per-event actor column for `involving` filter — joins against caller +
# (for variants with a receiver) the receiver column too.
_MORPHO_ACTOR_COLS = {
    'supply':              ['caller', 'on_behalf'],
    'withdraw':            ['caller', 'on_behalf', 'receiver'],
    'borrow':              ['caller', 'on_behalf', 'receiver'],
    'repay':               ['caller', 'on_behalf'],
    'supply_collateral':   ['caller', 'on_behalf'],
    'withdraw_collateral': ['caller', 'on_behalf', 'receiver'],
    'liquidation':         ['caller', 'borrower'],
}


def evm_morpho(
    event: str, network: str, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
    market_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = MORPHO_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Morpho event: {event!r}. Valid: {list(MORPHO_EVENT_TABLE)}")
    chain = chain_from_network(network)
    if chain is None:
        return None, None
    spec = _MORPHO_PROJECTION[event]
    actors = _MORPHO_ACTOR_COLS[event]
    params: dict[str, Any] = {
        'chain': chain,
        'since': _ts_to_ch(since), 'until': _ts_to_ch(until),
    }
    where = [
        'chain = {chain:String}',
        'time >= toDateTime({since:String})',
        'time <  toDateTime({until:String})',
    ]
    if market_id:
        params['market_id'] = market_id.lower()
        where.append('lower(market_id) = {market_id:String}')
    if involving:
        params['involving'] = involving.lower()
        clauses = [f'lower({c}) = {{involving:String}}' for c in actors]
        where.append('(' + ' OR '.join(clauses) + ')')
    if exclude_involving:
        params['exclude_involving'] = exclude_involving.lower()
        clauses = [f'lower({c}) != {{exclude_involving:String}}' for c in actors]
        where.append('(' + ' AND '.join(clauses) + ')')
    sql = f"""
        SELECT {_projection_clause(spec)}
        FROM tradernick.{table} FINAL
        WHERE {' AND '.join(where)}
        ORDER BY time
    """
    return sql, params


# ===========================================================================
# Aerodrome — TN-exclusive. Two flavors:
#  - concentrated: V3-like CL pools keyed by (symbol0, symbol1, tick_spacing)
#  - basic:        V2-like AMM pools keyed by (symbol0, symbol1) only
# Both expose swap / deposit / withdraw / collect (concentrated) or
# swap / deposit / withdraw / claim (basic). Pair ordering canonical-or-
# reversed both accepted, same as Uniswap.
# ===========================================================================

AERO_CONCENTRATED_EVENT_TABLE = {
    'swap':     'aero_concentrated_swaps',
    'deposit':  'aero_concentrated_deposits',
    'withdraw': 'aero_concentrated_withdrawals',
    'collect':  'aero_concentrated_collects',
}

AERO_BASIC_EVENT_TABLE = {
    'swap':     'aero_basic_swaps',
    'deposit':  'aero_basic_deposits',
    'withdraw': 'aero_basic_withdrawals',
    'claim':    'aero_basic_claims',
}


def _aero_pair_filter(params: dict, where: list,
                      symbol0: str | None, symbol1: str | None) -> None:
    """Accept either pair ordering, mirroring uniswap canonical-pair logic."""
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


def evm_aero_concentrated(
    event: str, network: str,
    symbol0: str | None, symbol1: str | None, tick_spacing: int | None,
    since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    table = AERO_CONCENTRATED_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Aerodrome (concentrated) event: {event!r}.")
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
    _aero_pair_filter(params, where, symbol0, symbol1)
    if tick_spacing is not None:
        params['ts'] = int(tick_spacing)
        where.append('tick_spacing = {ts:UInt32}')

    if event == 'swap':
        actor_col, recipient_col = 'swapper', 'recipient'
        spec = [
            'block_number', 'pool_address', 'swapper', 'recipient',
            ('token_sold',   'tokenSold'),
            ('token_bought', 'tokenBought'),
            ('amount_sold',   'amountSold'),
            ('amount_bought', 'amountBought'),
            'sqrt_based_price', 'liquidity', 'tick',
            ('_TIME_MS', 'time'),
        ]
    elif event == 'deposit':
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


def evm_aero_basic(
    event: str, network: str,
    symbol0: str | None, symbol1: str | None,
    stable: bool | None, since: str, until: str,
    *, involving: str | None = None,
    exclude_involving: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """V2-AMM Aerodrome events. `stable` filters by the pool's curve flavor
    (Velodrome-style stable vs volatile pair); pass None to include both."""
    table = AERO_BASIC_EVENT_TABLE.get(event)
    if not table:
        raise ValueError(f"Unknown Aerodrome (basic) event: {event!r}.")
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
    _aero_pair_filter(params, where, symbol0, symbol1)
    if stable is not None:
        params['stable'] = 1 if stable else 0
        where.append('stable = {stable:UInt8}')

    # Basic AMM events share a (sender/recipient/actor) shape but per-event
    # specifics — keep projections lean and conservative; the route returns
    # the columns CH already has under their natural names.
    if event == 'swap':
        actor_col, recipient_col = 'swapper', 'recipient'
        spec = [
            'block_number', 'pool_address', 'swapper', 'recipient',
            ('token_sold',   'tokenSold'),
            ('token_bought', 'tokenBought'),
            ('amount_sold',   'amountSold'),
            ('amount_bought', 'amountBought'),
            'stable', ('_TIME_MS', 'time'),
        ]
    elif event == 'deposit':
        # Basic deposit only carries `sender` — no owner column.
        actor_col, recipient_col = 'sender', 'sender'
        spec = [
            'block_number', 'pool_address', 'sender',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'stable', ('_TIME_MS', 'time'),
        ]
    elif event == 'withdraw':
        actor_col, recipient_col = 'owner', 'recipient'
        spec = [
            'block_number', 'pool_address', 'owner', 'recipient',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'stable', ('_TIME_MS', 'time'),
        ]
    else:  # claim — both sender + recipient present
        actor_col, recipient_col = 'sender', 'recipient'
        spec = [
            'block_number', 'pool_address', 'sender', 'recipient',
            'amount0', 'amount1',
            ('symbol0', 'token0'), ('symbol1', 'token1'),
            'stable', ('_TIME_MS', 'time'),
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
