"""Hyperliquid aggregate endpoint. 8 events on HL (a perp DEX).

The shape of HL data is unique among our protocols — every event carries
a wallet identity. The aggregate endpoint accepts optional `wallet=` and
`wallet_category=` filters that constrain the aggregation to a single
wallet or a labelled wallet category (CEX / Smart-Money / Bridge / …)
sourced from the existing tradernick.wallet_labels dictionary.

A dedicated /hyperliquid/wallets/leaderboard endpoint returns the
already-pre-aggregated trader performance from hl_trade_history for the
table-chart kind on the dashboard.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sanic import Blueprint, response

from clickhouse import client
from positions import latest_snapshot_bucket, mark_price, positions_at
from routes.ohlcv import INTERVAL_SECONDS, _parse_iso
from throttle import throttled
from wallets.smart_selector import SmartSelector, HIP3_EXCLUDE
from wallets import cache as wallets_cache

log = logging.getLogger(__name__)

bp = Blueprint("hyperliquid")

# Event → (table, amount-expression, value-expression, optional wallet_col,
#          optional agg_func — defaults to "sum").
#
# wallet_col is the column on which to apply the wallet filter; some events
# (trades, vaults) have a different column name than "wallet".
#
# agg_func overrides the default sum() aggregation. Used by `funding` to
# average the rate column instead of summing the per-wallet amount — the
# sum of per-wallet amount is mathematically always ~0 (zero-sum transfer
# between longs and shorts), so it makes a useless chart. The avg(rate)
# gives the canonical funding-rate metric.
# 6-tuple per event: (table, amount_expr, value_expr, wallet_col, agg_func, extra_where).
# extra_where is appended to the WHERE clause for this event only — used by
# `fills` to filter to taker-side rows only (one trade emits two fill rows
# on HL: one for the taker with crossed=1 and one for the maker; summing
# all fills double-counts volume).
_EVENT_TABLES = {
    # OHLCV is special-cased — has window-bucketed shape, no aggregation.
    "ohlcv":            ("tradernick.hl_ohlcv_1m",        "volume",            "volume",          None,    "sum",  ""),
    # Trades: amount = total volume per bucket (sum), value = sum(price*amount)
    "trades":           ("tradernick.hl_trades",          "amount",            "price*amount",    None,    "sum",  ""),
    # Fills: amount = sum(size), value = sum(price*size). Filter to crossed=1
    # so we only count the taker side of each match (the maker side is the
    # mirror image and would double the volume).
    "fills":            ("tradernick.hl_fills",           "size",              "price*size",      "wallet","sum",  "crossed = 1"),
    # Funding: chart plots the funding RATE (avg per bucket). Positive rate
    # = longs paying shorts; negative = shorts paying longs. HL fires
    # funding hourly so `rate` is the hourly funding rate at that event.
    "funding":          ("tradernick.hl_funding",         "rate",              "rate",            "wallet","avg",  ""),
    # position_history deferred — see note in clickhouse.py HL_EVENTS.
    # Trade history: already pre-aggregated. amount = sum(volume), value = sum(net_pnl).
    "trade_history":    ("tradernick.hl_trade_history",   "volume",            "net_pnl",         "wallet","sum",  ""),
    "transfers":        ("tradernick.hl_transfers",       "amount",            "amount",          "wallet","sum",  ""),
    "vaults":           ("tradernick.hl_vaults",          "amount",            "amount",          "wallet","sum",  ""),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/hyperliquid/aggregate")
@throttled("heavy")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    # trade_history is now DAILY + ABSOLUTE (cumulative-from-inception)
    # snapshots — the generic sum()-per-bucket path would sum cumulative
    # curves and is meaningless. Use the purpose-built endpoints instead, which
    # apply snapshot-diff: /hyperliquid/wallet_pnl (per-wallet realized curve)
    # or /hyperliquid/wallets/leaderboard (window totals).
    if event == "trade_history":
        return response.json(
            {"error": "trade_history is daily/absolute now; use "
                      "/hyperliquid/wallet_pnl or /hyperliquid/wallets/leaderboard"},
            status=400)
    table, amount_expr, value_expr, wallet_col, agg_func, extra_where = _EVENT_TABLES[event]

    token = request.args.get("token")            # optional; if absent, sums across tokens
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    # Optional wallet filters. wallet = an EVM address (lowercased before
    # lookup since wallet_labels dict was loaded both-cases for 0x-prefixed).
    wallet = request.args.get("wallet")
    wallet_category = request.args.get("wallet_category")

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    where_parts = ["time >= {since:DateTime}", "time <  {until:DateTime}"]
    if extra_where:
        where_parts.append(extra_where)
    params: dict = {"seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit}

    if token:
        # Not all events have a token column (transfers, vaults). The dispatch
        # table covers six of eight; the two that don't simply ignore the filter.
        if event not in ("transfers", "vaults"):
            where_parts.append("token = {token:String}")
            params["token"] = token

    # Wallet filtering. Only applicable if the event table has a wallet column.
    if wallet_col is not None:
        if wallet:
            where_parts.append(f"lower({wallet_col}) = {{wallet:String}}")
            params["wallet"] = wallet.lower()
        elif wallet_category:
            # dictHas + dictGet against the wallet_labels dictionary loaded
            # in /flows work — labels are an Array(String) of categories.
            where_parts.append(
                f"has(dictGet('tradernick.wallet_labels', 'categories', lower({wallet_col})), {{category:String}})"
            )
            params["category"] = wallet_category

    where_sql = " AND ".join(where_parts)

    # FINAL forces the ReplacingMergeTree merge at query time, so
    # un-merged duplicate rows from force=false backfills don't double the
    # aggregates. ~10-30% query overhead on un-merged ranges, near-zero
    # once background merges have run.
    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            {agg_func}({amount_expr}) AS sum_amount,
            {agg_func}({value_expr})  AS sum_value_usd,
            count()                   AS count
        FROM {table} FINAL
        WHERE {where_sql}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {"time": int(r[0]), "sum_amount": float(r[1]), "sum_value_usd": float(r[2]), "count": int(r[3])}
        for r in rows.result_rows
    ]
    body = {"event": event, "interval": interval, "series": series}
    if token: body["token"] = token
    if wallet: body["wallet"] = wallet
    if wallet_category: body["wallet_category"] = wallet_category
    return response.json(body)


@bp.get("/hyperliquid/realized_pnl_split")
@throttled("heavy")
async def realized_pnl_split(request):
    """Realized PnL bucketed by direction (long vs short).

    Sourced from hl_fills (which carries `dir` and `closed_pnl` per fill)
    rather than hl_trade_history (which only stores the net per-wallet
    aggregate, losing the long/short split). Always filtered to
    crossed=1 so each match is counted once on the taker side — the
    same convention /hyperliquid/aggregate uses for the `fills` event.

    Returns time, long_pnl, short_pnl, total_pnl per bucket. The frontend
    picks which of those series to draw based on the user's "side"
    selector on the HL Realized PnL chart.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    wallet = request.args.get("wallet")
    wallet_category = request.args.get("wallet_category")

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if not token:
        # hl_fills ORDER BY starts with token — skipping the filter forces
        # a full table scan that's prohibitive on multi-day ranges.
        return response.json({"error": "missing token"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    where_parts = [
        "time >= {since:DateTime}",
        "time <  {until:DateTime}",
        "crossed = 1",
        "token = {token:String}",
    ]
    params: dict = {
        "seconds": seconds, "since": since_dt, "until": until_dt,
        "limit": limit, "token": token,
    }
    if wallet:
        where_parts.append("lower(wallet) = {wallet:String}")
        params["wallet"] = wallet.lower()
    elif wallet_category:
        where_parts.append(
            "has(dictGet('tradernick.wallet_labels', 'categories', lower(wallet)), {category:String})"
        )
        params["category"] = wallet_category

    where_sql = " AND ".join(where_parts)
    # 'Long > Short' fills both close a long AND open a short — the
    # closed_pnl belongs to the long side. Same for 'Short > Long' on
    # the short side. 'Open Long' / 'Open Short' carry closed_pnl=0 so
    # they fall out of both branches naturally; we still sum them into
    # total_pnl via the unconditional sum() for symmetry / robustness.
    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sumIf(closed_pnl, dir IN ('Close Long', 'Long > Short'))  AS long_pnl,
            sumIf(closed_pnl, dir IN ('Close Short', 'Short > Long')) AS short_pnl,
            sum(closed_pnl)                                           AS total_pnl,
            count()                                                   AS count
        FROM tradernick.hl_fills FINAL
        WHERE {where_sql}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {
            "time": int(r[0]),
            "long_pnl": float(r[1]),
            "short_pnl": float(r[2]),
            "total_pnl": float(r[3]),
            "count": int(r[4]),
        }
        for r in rows.result_rows
    ]
    body = {"interval": interval, "token": token, "series": series}
    if wallet: body["wallet"] = wallet
    if wallet_category: body["wallet_category"] = wallet_category
    return response.json(body)


@bp.get("/hyperliquid/streams")
async def streams(_request):
    """Distinct (event, token) tuples with row counts. Cached 60s.
    Powers the per-chart token selector on the /hyperliquid page."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _a, _v, _w, _af, _ew) in _EVENT_TABLES.items():
        # transfers + vaults have no token dimension — skip them in streams.
        if ev in ("transfers", "vaults"):
            continue
        rows = await ch.query(f"""
            SELECT token, count() AS rows
            FROM {table} FINAL
            WHERE token != ''
            GROUP BY token
            ORDER BY rows DESC
        """)
        for tok, n in rows.result_rows:
            out.append({"event": ev, "token": tok, "rows": int(n)})
    # position_history is intentionally not in _EVENT_TABLES (state, not
    # flow — see /hyperliquid/unrealized_pnl), but the chart picker still
    # needs to know which tokens have snapshots available.
    rows = await ch.query("""
        SELECT token, count() AS rows
        FROM tradernick.hl_position_history FINAL
        WHERE token != ''
        GROUP BY token
        ORDER BY rows DESC
    """)
    for tok, n in rows.result_rows:
        out.append({"event": "position_history", "token": tok, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})


@bp.get("/hyperliquid/wallets/leaderboard")
@throttled("heavy")
async def leaderboard(request):
    """Top-N traders by PnL or volume, optionally filtered to a single
    token, over a [since, until] window. Read from the pre-aggregated
    hl_trade_history table (small, fast)."""
    token = request.args.get("token")
    since = request.args.get("since")
    until = request.args.get("until")
    order_by = request.args.get("order_by", "net_pnl")
    limit = int(request.args.get("limit", "50"))

    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if order_by not in ("net_pnl", "pnl", "volume", "trade_count"):
        return response.json({"error": "order_by must be net_pnl|pnl|volume|trade_count"}, status=400)

    since_dt = _parse_iso(since); until_dt = _parse_iso(until)
    params: dict = {"since": since_dt, "until": until_dt, "limit": limit}
    if token:
        # ── TOKEN-scoped: snapshot-diff per (wallet, token) straight from the
        # source (the token-leading projection prunes to one token's rows). The
        # token-less rollup can't serve a single token.
        params["token"] = token
        th_tok = "AND token = {token:String}"
        win_cte = f"""
        win AS (
            SELECT wallet,
                sum(e_np - s_np) AS net_pnl,
                sum(e_p  - s_p)  AS pnl,
                sum(e_f  - s_f)  AS fees,
                sum(e_v  - s_v)  AS volume,
                sum(e_bv - s_bv) AS buy_volume,
                sum(e_sv - s_sv) AS sell_volume,
                sum(e_tc - s_tc) AS trade_count
            FROM (
                SELECT wallet, token,
                    argMaxIf(net_pnl, time, time <= toStartOfDay({{until:DateTime}})) AS e_np,
                    argMaxIf(net_pnl, time, time <= {{since:DateTime}})               AS s_np,
                    argMaxIf(pnl, time, time <= toStartOfDay({{until:DateTime}}))      AS e_p,
                    argMaxIf(pnl, time, time <= {{since:DateTime}})                    AS s_p,
                    argMaxIf(fees, time, time <= toStartOfDay({{until:DateTime}}))     AS e_f,
                    argMaxIf(fees, time, time <= {{since:DateTime}})                   AS s_f,
                    argMaxIf(volume, time, time <= toStartOfDay({{until:DateTime}}))   AS e_v,
                    argMaxIf(volume, time, time <= {{since:DateTime}})                 AS s_v,
                    argMaxIf(buy_volume, time, time <= toStartOfDay({{until:DateTime}})) AS e_bv,
                    argMaxIf(buy_volume, time, time <= {{since:DateTime}})             AS s_bv,
                    argMaxIf(sell_volume, time, time <= toStartOfDay({{until:DateTime}})) AS e_sv,
                    argMaxIf(sell_volume, time, time <= {{since:DateTime}})            AS s_sv,
                    argMaxIf(trade_count, time, time <= toStartOfDay({{until:DateTime}})) AS e_tc,
                    argMaxIf(trade_count, time, time <= {{since:DateTime}})            AS s_tc
                FROM tradernick.hl_trade_history FINAL
                WHERE time <= {{until:DateTime}} {th_tok}
                GROUP BY wallet, token
            )
            GROUP BY wallet
        )"""
        tail_tok = th_tok
    else:
        # ── GLOBAL (all tokens): read the pre-aggregated per-(day,wallet)
        # rollup (hl_trade_history_wallet_daily, token dimension summed away,
        # HIP3 excluded). Window value = snapshot(until_day) − snapshot(since_day)
        # via two single-partition reads at the latest day ≤ each bound (dense-
        # to-now snapshots mean that one day carries every ever-traded wallet).
        # Far cheaper than the all-token per-(wallet,token) argMaxIf scan.
        win_cte = """
        ta_e AS (
            SELECT wallet,
                sumMerge(net_pnl_state)     AS net_pnl, sumMerge(pnl_state)  AS pnl,
                sumMerge(fees_state)        AS fees,    sumMerge(volume_state) AS volume,
                sumMerge(buy_volume_state)  AS buy_volume, sumMerge(sell_volume_state) AS sell_volume,
                sumMerge(trade_count_state) AS trade_count
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= toDate({until:DateTime}))
            GROUP BY wallet
        ),
        ta_s AS (
            SELECT wallet,
                sumMerge(net_pnl_state)     AS net_pnl, sumMerge(pnl_state)  AS pnl,
                sumMerge(fees_state)        AS fees,    sumMerge(volume_state) AS volume,
                sumMerge(buy_volume_state)  AS buy_volume, sumMerge(sell_volume_state) AS sell_volume,
                sumMerge(trade_count_state) AS trade_count
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= toDate({since:DateTime}))
            GROUP BY wallet
        ),
        win AS (
            SELECT e.wallet AS wallet,
                e.net_pnl     - coalesce(s.net_pnl, 0)     AS net_pnl,
                e.pnl         - coalesce(s.pnl, 0)         AS pnl,
                e.fees        - coalesce(s.fees, 0)        AS fees,
                e.volume      - coalesce(s.volume, 0)      AS volume,
                e.buy_volume  - coalesce(s.buy_volume, 0)  AS buy_volume,
                e.sell_volume - coalesce(s.sell_volume, 0) AS sell_volume,
                e.trade_count - coalesce(s.trade_count, 0) AS trade_count
            FROM ta_e e LEFT JOIN ta_s s ON s.wallet = e.wallet
        )"""
        # Current-day tail HIP3-excluded to match the rollup's HIP3 exclusion.
        tail_tok = "AND position(token, ':') = 0"
    sql = f"""
        WITH
        {win_cte},
        tail AS (
            SELECT wallet, sum(closed_pnl) AS t_pnl, sum(fee) AS t_fee
            FROM tradernick.hl_fills FINAL
            WHERE time > toStartOfDay({{until:DateTime}}) AND time <= {{until:DateTime}} {tail_tok}
            GROUP BY wallet
        )
        SELECT
            w.wallet AS wallet,
            w.net_pnl + coalesce(t.t_pnl, 0) - coalesce(t.t_fee, 0) AS net_pnl,
            w.pnl + coalesce(t.t_pnl, 0) AS pnl,
            w.fees + coalesce(t.t_fee, 0) AS fees,
            w.volume AS volume,
            w.buy_volume AS buy_volume,
            w.sell_volume AS sell_volume,
            w.trade_count AS trade_count,
            -- Surface wallet labels (Array(String)) for the badge on the
            -- table chart; empty array for unlabelled wallets.
            dictGet('tradernick.wallet_labels', 'categories', lower(w.wallet)) AS categories
        FROM win w
        LEFT JOIN tail t ON t.wallet = w.wallet
        ORDER BY {order_by} DESC
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters=params)
    leaders = [
        {
            "wallet": r[0],
            "net_pnl": float(r[1]),
            "pnl": float(r[2]),
            "fees": float(r[3]),
            "volume": float(r[4]),
            "buy_volume": float(r[5]),
            "sell_volume": float(r[6]),
            "trade_count": int(r[7]),
            "categories": list(r[8]) if r[8] else [],
        }
        for r in rows.result_rows
    ]
    return response.json({
        "order_by": order_by, "limit": limit,
        "token": token, "since": since, "until": until,
        "leaders": leaders,
    })


def _build_smart_wallet_selection(request, include_avg_oi: bool = False,
                                  lookback_override=None, snapshot_override=None,
                                  membership_override=None):
    """Parse the smart-wallet finder's filter params and build the shared
    wallet-SELECTION SQL: the CTE chain that computes each wallet's window
    metrics, plus the FROM/JOIN/WHERE that keeps only wallets passing every
    min_*/max_* guard.

    Returns a dict with `cte_block` (CTE definitions to follow `WITH`),
    `from_where_block` (FROM…JOIN…WHERE), `oi_token_select`, `order_col`,
    `params`, and `echo` (the parsed values, for the response). Raises
    ValueError(msg) on a bad param so the caller can 400.

    Reused by /smart_wallet_metrics (ranked table, top-N) and /smart_wallet_oi
    (OI aggregated over EVERY passing wallet — possibly far more than the table
    shows). Keeping selection in one place guarantees the chart plots exactly
    the set the table counts.
    """
    token = request.args.get("token")
    if token in ("", "__all__", "__global__", "all"):
        token = None
    lookback = int(lookback_override) if lookback_override is not None \
        else int(request.args.get("lookback", "7"))
    if lookback not in (1, 3, 7, 14, 30, 90, 150):
        raise ValueError("lookback must be 1|3|7|14|30|90|150")
    metric = request.args.get("metric", "sharpe")
    if metric not in ("sharpe",):
        raise ValueError("unsupported metric")
    order_by = request.args.get("order_by", metric)
    ORDER_COLS = {
        "sharpe": "metric", "volume": "volume", "realized": "realized_pnl",
        "unrealized": "unrealized_pnl", "oi_usd": "oi_usd",
    }
    if order_by not in ORDER_COLS:
        raise ValueError("bad order_by")
    limit = min(int(request.args.get("limit", "100")), 1000)
    min_days = max(int(request.args.get("min_days", "3")), 1)
    try:
        min_volume = float(request.args.get("min_volume", "0"))
    except ValueError:
        min_volume = 0.0
    try:
        min_realized = float(request.args.get("min_realized", "0"))
    except ValueError:
        min_realized = 0.0
    # Total PnL = realized + (current) unrealized; unrealized alone. Default to a
    # huge negative so "no floor" never excludes loss-making wallets.
    NO_MIN = -1e18
    try:
        min_unrealized = float(request.args.get("min_unrealized", str(NO_MIN)))
    except ValueError:
        min_unrealized = NO_MIN
    try:
        min_total_pnl = float(request.args.get("min_total_pnl", str(NO_MIN)))
    except ValueError:
        min_total_pnl = NO_MIN
    try:
        min_oi = float(request.args.get("min_oi", "0"))
    except ValueError:
        min_oi = 0.0
    # Execution-quality filters. min_* default to 0 (no floor); max_* default to
    # a huge number (no ceiling). fee%/funding% are ratios to GROSS realized.
    NO_MAX = 1e12
    try:
        min_avg_trade_size = float(request.args.get("min_avg_trade_size", "0"))
    except ValueError:
        min_avg_trade_size = 0.0
    try:
        min_taker_pct = float(request.args.get("min_taker_pct", "0"))
    except ValueError:
        min_taker_pct = 0.0
    try:
        max_fee_pct = float(request.args.get("max_fee_pct", str(NO_MAX)))
    except ValueError:
        max_fee_pct = NO_MAX
    try:
        max_funding_pct = float(request.args.get("max_funding_pct", str(NO_MAX)))
    except ValueError:
        max_funding_pct = NO_MAX
    try:
        min_account_duration = int(request.args.get("min_account_duration", "0"))
    except ValueError:
        min_account_duration = 0
    try:
        min_tokens = int(request.args.get("min_tokens", "0"))
    except ValueError:
        min_tokens = 0
    try:
        min_win_rate = float(request.args.get("min_win_rate", "0"))
    except ValueError:
        min_win_rate = 0.0
    try:
        max_trades_per_day = float(request.args.get("max_trades_per_day", str(NO_MAX)))
    except ValueError:
        max_trades_per_day = NO_MAX
    try:
        min_trades_per_day = float(request.args.get("min_trades_per_day", "0"))
    except ValueError:
        min_trades_per_day = 0.0
    # Min annualized Sharpe — same ANNUALIZED (×√365), OI-un-normalized Sharpe
    # the table ranks by. Sharpe can be negative, so the no-floor default is a
    # large NEGATIVE sentinel (the frontend only sends this when the user sets
    # it).
    try:
        min_annualized_sharpe = float(request.args.get("min_annualized_sharpe", str(-NO_MAX)))
    except ValueError:
        min_annualized_sharpe = -NO_MAX
    # Market-share guards. Units are 0.01% (a "permyriad": 30 ⇒ 0.30% share),
    # so a share fraction f maps to 10000·f. OI share = the wallet's
    # window-AVERAGE OI as a fraction of the market's average total OI; volume
    # share = the wallet's window volume as a fraction of total window volume.
    # Both denominators are the full (pre-filter) totals over the same scope
    # (that token, or global). min_* default 0 (no floor), max_* NO_MAX.
    try:
        min_avg_oi_share = float(request.args.get("min_avg_oi_share", "0"))
    except ValueError:
        min_avg_oi_share = 0.0
    try:
        max_avg_oi_share = float(request.args.get("max_avg_oi_share", str(NO_MAX)))
    except ValueError:
        max_avg_oi_share = NO_MAX

    # Window-average OI guards: avg OI over the WHOLE lookback (Σ OI·buckets ÷
    # buckets), in USD. *_avg_oi = current scope (the token, or global). The
    # *_avg_global_oi[_share] variants always use GLOBAL (all-tokens) OI — only
    # meaningful in token mode; in global mode they equal the plain ones.
    def _flt(name, default):
        try:
            return float(request.args.get(name, str(default)))
        except (TypeError, ValueError):
            return default
    min_avg_oi = _flt("min_avg_oi", 0.0)
    max_avg_oi = _flt("max_avg_oi", NO_MAX)
    min_avg_global_oi = _flt("min_avg_global_oi", 0.0)
    max_avg_global_oi = _flt("max_avg_global_oi", NO_MAX)
    min_avg_global_oi_share = _flt("min_avg_global_oi_share", 0.0)
    max_avg_global_oi_share = _flt("max_avg_global_oi_share", NO_MAX)
    try:
        min_volume_share = float(request.args.get("min_volume_share", "0"))
    except ValueError:
        min_volume_share = 0.0
    try:
        max_volume_share = float(request.args.get("max_volume_share", str(NO_MAX)))
    except ValueError:
        max_volume_share = NO_MAX

    snap_arg = snapshot_override or request.args.get("snapshot")
    if snap_arg:
        try:
            e_dt = _parse_iso(snap_arg).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            raise ValueError("invalid snapshot")
        e_dt = e_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        e_dt = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end_day = e_dt.date()
    start_day = end_day - timedelta(days=lookback)

    # In TOKEN scope every wallet's distinct-token count is trivially 1 (we only
    # look at that token), so a min_tokens >= 2 guard — perfectly sensible
    # globally ("wallets that trade several tokens") — would wrongly exclude
    # EVERY wallet. The criterion is meaningless per-token, so ignore it here.
    if token is not None:
        min_tokens = 0

    params: dict = {
        "until": e_dt, "end_day": end_day, "start_day": start_day,
        "min_days": min_days, "min_volume": min_volume,
        "min_realized": min_realized, "min_oi": min_oi, "limit": limit,
        "min_unrealized": min_unrealized, "min_total_pnl": min_total_pnl,
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_annualized_sharpe": min_annualized_sharpe,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_volume_share": min_volume_share, "max_volume_share": max_volume_share,
        "lookback": lookback,
    }
    first_seen = """
        first_seen AS (
            SELECT wallet, min(day) AS first_day
            FROM tradernick.hl_trade_history_wallet_daily
            GROUP BY wallet
        )"""
    tok_pred = "AND token = {token:String}" if token else ""
    taker_agg = f"""
        taker_agg AS (
            SELECT wallet,
                sumMerge(taker_buy_vol_usd_state) + sumMerge(taker_sell_vol_usd_state) AS taker_vol,
                sumMerge(vol_usd_state) AS total_vol,
                uniqExact(token) AS n_tokens
            FROM tradernick.hl_fills_vol_daily
            WHERE day > {{start_day:Date}} AND day <= {{end_day:Date}} {tok_pred}
            GROUP BY wallet
        )"""
    funding_agg = f"""
        funding_agg AS (
            SELECT wallet, sumMerge(funding_pnl_state) AS funding
            FROM tradernick.hl_funding_daily
            WHERE day > {{start_day:Date}} AND day <= {{end_day:Date}} {tok_pred}
            GROUP BY wallet
        )"""

    if token is None:
        win_cte = """
        ta_e AS (
            SELECT wallet, sumMerge(volume_state) AS volume, sumMerge(pnl_state) AS realized,
                   sumMerge(trade_count_state) AS trades, sumMerge(fees_state) AS fees
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= {end_day:Date})
            GROUP BY wallet
        ),
        ta_s AS (
            SELECT wallet, sumMerge(volume_state) AS volume, sumMerge(pnl_state) AS realized,
                   sumMerge(trade_count_state) AS trades, sumMerge(fees_state) AS fees
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= {start_day:Date})
            GROUP BY wallet
        ),
        win AS (
            SELECT e.wallet AS wallet,
                e.volume   - coalesce(s.volume, 0)   AS volume,
                e.realized - coalesce(s.realized, 0) AS realized,
                e.trades   - coalesce(s.trades, 0)   AS trades,
                e.fees     - coalesce(s.fees, 0)     AS fees
            FROM ta_e e LEFT JOIN ta_s s ON s.wallet = e.wallet
        )"""
        real_daily = """
        real_daily AS (
            SELECT day AS d, wallet,
                sumMerge(pnl_state)         AS cum_pnl,
                sumMerge(trade_count_state) AS cum_tc
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day >= {start_day:Date} AND day <= {end_day:Date}
            GROUP BY day, wallet
        )"""
        eod_daily = f"""
        eod_daily AS (
            SELECT day AS d, wallet, sum(eod) AS un
            FROM (
                SELECT day, wallet, token, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE day >= {{start_day:Date}} - 2 AND day <= {{end_day:Date}} {HIP3_EXCLUDE}
                GROUP BY day, wallet, token, side
            )
            GROUP BY day, wallet
        )"""
        unreal_now = f"""
        unreal_now AS (
            SELECT wallet, sum(eod) AS unrealized
            FROM (
                SELECT wallet, token, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE day = (SELECT max(day) FROM tradernick.hl_position_history_eod_wallet WHERE day <= {{end_day:Date}})
                      {HIP3_EXCLUDE}
                GROUP BY wallet, token, side
            )
            GROUP BY wallet
        )"""
        oi_now = """
        oi_now AS (
            SELECT wallet,
                argMaxIfMerge(last_total_oi_usd_state)   AS oi_usd,
                argMaxIfMerge(last_total_oi_token_state) AS oi_token
            FROM tradernick.hl_position_history_oi_wallet_daily
            WHERE day = (SELECT max(day) FROM tradernick.hl_position_history_oi_wallet_daily WHERE day <= {end_day:Date})
            GROUP BY wallet
        )"""
        oi_token_select = "NULL AS oi_token"
    else:
        params["token"] = token
        win_cte = """
        win AS (
            SELECT wallet,
                argMaxIf(volume, time, toDate(time) <= {end_day:Date})
                  - argMaxIf(volume, time, toDate(time) <= {start_day:Date}) AS volume,
                argMaxIf(pnl, time, toDate(time) <= {end_day:Date})
                  - argMaxIf(pnl, time, toDate(time) <= {start_day:Date})    AS realized,
                argMaxIf(trade_count, time, toDate(time) <= {end_day:Date})
                  - argMaxIf(trade_count, time, toDate(time) <= {start_day:Date}) AS trades,
                argMaxIf(fees, time, toDate(time) <= {end_day:Date})
                  - argMaxIf(fees, time, toDate(time) <= {start_day:Date})   AS fees
            FROM tradernick.hl_trade_history FINAL
            WHERE token = {token:String} AND toDate(time) <= {end_day:Date}
            GROUP BY wallet
        )"""
        real_daily = """
        real_daily AS (
            SELECT toDate(time) AS d, wallet,
                argMax(pnl, time)         AS cum_pnl,
                argMax(trade_count, time) AS cum_tc
            FROM tradernick.hl_trade_history FINAL
            WHERE token = {token:String}
              AND toDate(time) >= {start_day:Date} AND toDate(time) <= {end_day:Date}
            GROUP BY d, wallet
        )"""
        eod_daily = """
        eod_daily AS (
            SELECT day AS d, wallet, sum(eod) AS un
            FROM (
                SELECT day, wallet, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE token = {token:String}
                  AND day >= {start_day:Date} - 2 AND day <= {end_day:Date}
                GROUP BY day, wallet, side
            )
            GROUP BY day, wallet
        )"""
        unreal_now = """
        unreal_now AS (
            SELECT wallet, sum(eod) AS unrealized
            FROM (
                SELECT wallet, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE token = {token:String}
                  AND day = (SELECT max(day) FROM tradernick.hl_position_history_eod_wallet
                             WHERE day <= {end_day:Date} AND token = {token:String})
                GROUP BY wallet, side
            )
            GROUP BY wallet
        )"""
        oi_now = """
        oi_now AS (
            SELECT wallet, sum(abs(amount)) AS oi_token, sum(abs(size_usd)) AS oi_usd
            FROM (
                SELECT wallet, side, argMax(amt, bucket) AS amount, argMax(sz, bucket) AS size_usd
                FROM (
                    SELECT wallet, bucket, side,
                        argMaxMerge(amount_state) AS amt, argMaxMerge(size_state) AS sz
                    FROM tradernick.hl_position_history_1h
                    WHERE token = {token:String}
                      AND bucket <= {until:DateTime} AND bucket > {until:DateTime} - INTERVAL 3 DAY
                    GROUP BY wallet, bucket, side
                )
                GROUP BY wallet, side
            )
            WHERE amount != 0
            GROUP BY wallet
        )"""
        oi_token_select = "coalesce(oi.oi_token, 0) AS oi_token"

    # avg-OI over the window needs an extra (potentially heavy) scan, so build
    # its CTE only when something needs it: the avg-OI-share guard, OR the
    # caller asking for the avg_oi column (include_avg_oi). Global reads the
    # per-(day,wallet) rollup (sum of OI·buckets over the window); token scope
    # sums abs(size_usd) across the window's hourly buckets for that token. The
    # share denominator (sum over ALL wallets) cancels each wallet's identical
    # bucket-count divisor, so Σwallet / Σall is exactly avg(wallet OI) /
    # avg(total OI); the displayed avg_oi is the time-average oi_sum/(days·24).
    # Global oi_window reads the per-day rollup (cheap), so the avg_oi column
    # can always build it. Token oi_window is a full-window hourly scan of one
    # token (seconds → tens of seconds on a busy token), so we DON'T build it
    # just for the column — only when the OI-share guard is active (the user has
    # opted into that cost). Hence token-scope avg_oi shows only when filtering
    # on OI share; otherwise it's NULL (the column renders "—").
    oi_share_active = (min_avg_oi_share > 0) or (max_avg_oi_share < NO_MAX)
    avg_oi_val_active = (min_avg_oi > 0) or (max_avg_oi < NO_MAX)
    cur_active = oi_share_active or avg_oi_val_active
    g_val_active = (min_avg_global_oi > 0) or (max_avg_global_oi < NO_MAX)
    g_share_active = (min_avg_global_oi_share > 0) or (max_avg_global_oi_share < NO_MAX)
    global_active = g_val_active or g_share_active
    guards_path = (membership_override is None)
    build_oi_window = cur_active or (include_avg_oi and token is None) \
        or (global_active and token is None and guards_path)
    # Separate GLOBAL (all-tokens) window only when a token is selected AND a
    # global-OI guard is active AND we're applying guards (not the membership
    # ranking path). In global mode the global guards reuse oi_window (gw == ow).
    build_global_window = (token is not None) and global_active and guards_path
    if token is None:
        oi_window_cte = """
        oi_window AS (
            SELECT wallet, sumMerge(s_total_oi_usd_state) AS oi_sum
            FROM tradernick.hl_position_history_oi_wallet_daily
            WHERE day > {start_day:Date} AND day <= {end_day:Date}
            GROUP BY wallet
        )"""
    else:
        oi_window_cte = """
        oi_window AS (
            SELECT wallet, sum(bkt_oi) AS oi_sum
            FROM (
                SELECT wallet, bucket, sum(abs(sz)) AS bkt_oi
                FROM (
                    SELECT wallet, bucket, side, argMaxMerge(size_state) AS sz
                    FROM tradernick.hl_position_history_1h
                    WHERE token = {token:String}
                      AND bucket > {start_day:Date} AND bucket <= {until:DateTime}
                    GROUP BY wallet, bucket, side
                )
                GROUP BY wallet, bucket
            )
            GROUP BY wallet
        )"""

    # GLOBAL (all-tokens) window — cheap per-day rollup, aliased separately so the
    # *_avg_global_oi[_share] guards can use it alongside a token-scoped oi_window.
    oi_window_global_cte = """
        oi_window_global AS (
            SELECT wallet, sumMerge(s_total_oi_usd_state) AS oi_sum
            FROM tradernick.hl_position_history_oi_wallet_daily
            WHERE day > {start_day:Date} AND day <= {end_day:Date}
            GROUP BY wallet
        )"""

    cte_block = f"""
        {win_cte},
        {real_daily},
        real_delta AS (
            SELECT d, wallet,
                cum_pnl - lagInFrame(cum_pnl, 1, 0) OVER w AS d_real,
                cum_tc  - lagInFrame(cum_tc,  1, 0) OVER w AS d_tc
            FROM real_daily
            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        ),
        {eod_daily},
        eod_delta AS (
            SELECT d, wallet, un - lagInFrame(un, 1, 0) OVER w AS d_un
            FROM eod_daily
            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        ),
        daily_series AS (
            SELECT rd.d AS d, rd.wallet AS wallet,
                rd.d_real + coalesce(ed.d_un, 0) AS daily_total,
                rd.d_tc AS d_tc
            FROM real_delta rd
            LEFT JOIN eod_delta ed ON ed.wallet = rd.wallet AND ed.d = rd.d
            WHERE rd.d > {{start_day:Date}}
        ),
        sharpe_agg AS (
            SELECT wallet,
                countIf(d_tc > 0)      AS n_days,
                avg(daily_total)       AS mean_pnl,
                stddevPop(daily_total) AS sd_pnl,
                if(countIf(d_tc > 0) >= {{min_days:UInt32}} AND stddevPop(daily_total) > 0,
                   avg(daily_total) / stddevPop(daily_total) * sqrt(365), 0) AS sharpe,
                100 * countIf(d_tc > 0 AND daily_total > 0) / nullIf(countIf(d_tc > 0), 0) AS win_rate
            FROM daily_series
            GROUP BY wallet
        ),
        {unreal_now},
        {oi_now},
        {taker_agg},
        {funding_agg},
        {first_seen}"""
    if build_oi_window:
        cte_block += f",\n{oi_window_cte}"
    if build_global_window:
        cte_block += f",\n{oi_window_global_cte}"
    # Share denominators as SCALAR CTEs (WITH (…) AS x): ClickHouse evaluates
    # these once, whereas a `(SELECT … FROM win)` referenced inline in the WHERE
    # re-inlines (re-runs) the whole table CTE on every reference. vol_total is
    # always defined (volume-share guards are always present); oi_total only when
    # oi_window exists.
    cte_block += "\n        , (SELECT sum(volume) FROM win) AS vol_total"
    if build_oi_window:
        cte_block += "\n        , (SELECT sum(oi_sum) FROM oi_window) AS oi_total"
    if build_global_window:
        cte_block += "\n        , (SELECT sum(oi_sum) FROM oi_window_global) AS oi_total_global"

    # Share guards: volume-share is cheap (sum over the existing `win` CTE) so
    # it's always present (a no-op at the 0/NO_MAX defaults); OI-share joins the
    # conditionally-built oi_window. coalesce(…, 0) keeps a 0-share fallback when
    # a denominator is 0, so the defaults never exclude a wallet. The join is
    # added whenever oi_window exists (guards OR the avg_oi column need it).
    oi_share_join = (
        "\n        LEFT JOIN oi_window ow ON ow.wallet = w.wallet"
        if build_oi_window else "")
    global_join = (
        "\n        LEFT JOIN oi_window_global gw ON gw.wallet = w.wallet"
        if build_global_window else "")
    # avg OI (USD) over the whole lookback = Σ OI·buckets ÷ (days × 24).
    _avg_oi = "ow.oi_sum / nullIf(toFloat64({lookback:UInt32}) * 24, 0)"
    avg_oi_guard = (f"""
          AND coalesce({_avg_oi}, 0) >= {{min_avg_oi:Float64}}
          AND coalesce({_avg_oi}, 0) <= {{max_avg_oi:Float64}}""" if avg_oi_val_active else "")
    oi_share_guard = """
          AND coalesce(10000 * ow.oi_sum / nullIf(oi_total, 0), 0) >= {min_avg_oi_share:Float64}
          AND coalesce(10000 * ow.oi_sum / nullIf(oi_total, 0), 0) <= {max_avg_oi_share:Float64}""" if oi_share_active else ""
    # Global-OI guards: in token mode use the separate global window (gw /
    # oi_total_global); in global mode reuse the current window (ow / oi_total).
    _g = "ow" if token is None else "gw"
    _g_total = "oi_total" if token is None else "oi_total_global"
    _g_avg = f"{_g}.oi_sum / nullIf(toFloat64({{lookback:UInt32}}) * 24, 0)"
    global_val_guard = (f"""
          AND coalesce({_g_avg}, 0) >= {{min_avg_global_oi:Float64}}
          AND coalesce({_g_avg}, 0) <= {{max_avg_global_oi:Float64}}""" if g_val_active else "")
    global_share_guard = (f"""
          AND coalesce(10000 * {_g}.oi_sum / nullIf({_g_total}, 0), 0) >= {{min_avg_global_oi_share:Float64}}
          AND coalesce(10000 * {_g}.oi_sum / nullIf({_g_total}, 0), 0) <= {{max_avg_global_oi_share:Float64}}""" if g_share_active else "")

    _joins_block = """
        FROM win w
        LEFT JOIN sharpe_agg sa ON sa.wallet = w.wallet
        LEFT JOIN unreal_now u  ON u.wallet = w.wallet
        LEFT JOIN oi_now oi     ON oi.wallet = w.wallet
        LEFT JOIN taker_agg tk  ON tk.wallet = w.wallet
        LEFT JOIN funding_agg fn ON fn.wallet = w.wallet
        LEFT JOIN first_seen fseen ON fseen.wallet = w.wallet""" + oi_share_join + global_join
    _guards_block = """
        WHERE w.volume >= {min_volume:Float64}
          AND w.realized >= {min_realized:Float64}
          AND coalesce(u.unrealized, 0) >= {min_unrealized:Float64}
          AND w.realized + coalesce(u.unrealized, 0) >= {min_total_pnl:Float64}
          AND coalesce(oi.oi_usd, 0) >= {min_oi:Float64}
          AND coalesce(sa.n_days, 0) >= {min_days:UInt32}
          AND coalesce(w.volume / nullIf(w.trades, 0), 0) >= {min_avg_trade_size:Float64}
          AND coalesce(100 * tk.taker_vol / nullIf(tk.total_vol, 0), 0) >= {min_taker_pct:Float64}
          AND (w.realized <= 0 OR 100 * w.fees / w.realized <= {max_fee_pct:Float64})
          AND (w.realized <= 0 OR 100 * coalesce(fn.funding, 0) / w.realized <= {max_funding_pct:Float64})
          AND coalesce(tk.n_tokens, 0) >= {min_tokens:UInt32}
          AND coalesce(dateDiff('day', fseen.first_day, {end_day:Date}), 0) >= {min_account_duration:UInt32}
          AND coalesce(sa.win_rate, 0) >= {min_win_rate:Float64}
          AND coalesce(w.trades / nullIf(sa.n_days, 0), 0) <= {max_trades_per_day:Float64}
          AND coalesce(w.trades / nullIf(sa.n_days, 0), 0) >= {min_trades_per_day:Float64}
          AND coalesce(sa.sharpe, 0) >= {min_annualized_sharpe:Float64}
          AND coalesce(10000 * w.volume / nullIf(vol_total, 0), 0) >= {min_volume_share:Float64}
          AND coalesce(10000 * w.volume / nullIf(vol_total, 0), 0) <= {max_volume_share:Float64}""" + avg_oi_guard + oi_share_guard + global_val_guard + global_share_guard
    # Cutoff (union) table reuses the metric CTEs but selects a precomputed wallet
    # set instead of re-applying the guards; membership_override is the WHERE then.
    from_where_block = (
        _joins_block + "\n        WHERE " + membership_override
        if membership_override else _joins_block + _guards_block
    )

    echo = {
        "metric": metric, "order_by": order_by, "token": token,
        "lookback": lookback, "snapshot": end_day.isoformat(),
        "limit": limit, "min_days": min_days, "min_volume": min_volume,
        "min_realized": min_realized, "min_oi": min_oi,
        "min_unrealized": min_unrealized, "min_total_pnl": min_total_pnl,
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_annualized_sharpe": min_annualized_sharpe,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_volume_share": min_volume_share, "max_volume_share": max_volume_share,
    }
    # Window time-average OI (USD) for the avg_oi column: Σ OI·buckets over the
    # window ÷ the window's hourly buckets (days × 24). NULL when oi_window
    # wasn't built (e.g. the OI-chart path doesn't request the column).
    avg_oi_select = (
        "coalesce(ow.oi_sum, 0) / nullIf(toFloat64({lookback:UInt32}) * 24, 0) AS avg_oi"
        if build_oi_window else "NULL AS avg_oi")
    return {
        "cte_block": cte_block, "from_where_block": from_where_block,
        "oi_token_select": oi_token_select, "avg_oi_select": avg_oi_select,
        "order_col": ORDER_COLS[order_by],
        "params": params, "echo": echo,
    }


# ── Passing-wallet-set cache (ClickHouse table) ──────────────────────────
# The smart-wallet SELECTION (the CTE chain + min_*/max_* WHERE) is the slow
# (~5s) half of both /smart_wallet_metrics and /smart_wallet_oi. The resulting
# wallet SET is identical for the same filters, so we cache it in a CH table:
# the table view's fetch warms it (in the background), and the chart view
# (smart_wallet_oi) reads it via a sub-SELECT — no recompute, and no shipping a
# thousands-long IN-list over the wire (which blows ClickHouse's HTTP field
# cap). Keyed by the set-defining filters only (NOT metric/order_by/limit). A
# short freshness window lets today's still-ingesting day refresh; a 1-day TTL
# trims abandoned keys.
_SET_CACHE_TABLE = "tradernick.smart_wallet_set_cache"
_SET_CACHE_TTL = 300.0
# Set-defining filters (order/metric/limit excluded — they don't change WHICH
# wallets qualify, only the ranking/size of the returned table page).
_PASSING_KEY_FIELDS = (
    "token", "lookback", "snapshot", "min_days", "min_volume", "min_realized",
    "min_unrealized", "min_total_pnl",
    "min_oi", "min_avg_trade_size", "min_taker_pct", "max_fee_pct",
    "max_funding_pct", "min_account_duration", "min_tokens", "min_win_rate",
    "min_trades_per_day", "max_trades_per_day", "min_annualized_sharpe",
    "min_avg_oi_share", "max_avg_oi_share",
    "min_avg_oi", "max_avg_oi", "min_avg_global_oi", "max_avg_global_oi",
    "min_avg_global_oi_share", "max_avg_global_oi_share",
    "min_volume_share", "max_volume_share",
)
# In-process hint of when we last ensured a key was fresh, to skip the freshness
# round-trip on hot keys. The CH table is the source of truth.
_set_ensured: dict[str, float] = {}
# Single-flight: one in-flight materialisation per cache key. Concurrent callers
# (e.g. the table / chart / tokens views resolving the SAME set at once, or two
# tabs) await the first instead of each launching the same expensive INSERT.
# Without this they contend, exceed the client timeout, abort uncached, and
# retry forever — pinning ClickHouse.
_resolve_locks: dict[str, asyncio.Lock] = {}


def _resolve_lock(key: str) -> asyncio.Lock:
    lk = _resolve_locks.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _resolve_locks[key] = lk
    return lk


def _passing_key(sel: dict) -> str:
    e = sel["echo"]
    blob = json.dumps({k: e[k] for k in _PASSING_KEY_FIELDS}, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()


async def _ensure_set_table(ch) -> None:
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {_SET_CACHE_TABLE} (\n"
        "    sel_key     String,\n"
        "    wallet      String,\n"
        "    computed_at DateTime DEFAULT now()\n"
        ") ENGINE = ReplacingMergeTree(computed_at)\n"
        "ORDER BY (sel_key, wallet)\n"
        "TTL computed_at + INTERVAL 1 DAY"
    )


async def _resolve_passing(ch, sel: dict) -> str:
    """Ensure the selection's full wallet set is cached in CH and return its
    `sel_key`. The OI query then filters via `wallet IN (SELECT … WHERE sel_key)`.
    Computing the set IS the ~5s selection; a fresh cache hit is instant."""
    key = _passing_key(sel)
    async with _resolve_lock(key):
        now = time.time()
        if (last := _set_ensured.get(key)) and now - last < _SET_CACHE_TTL:
            return key
        await _ensure_set_table(ch)
        r = await ch.query(
            f"SELECT toUnixTimestamp(max(computed_at)) FROM {_SET_CACHE_TABLE} WHERE sel_key = {{k:String}}",
            parameters={"k": key},
        )
        mx = r.result_rows[0][0] if (r and r.result_rows) else 0
        if mx and now - float(mx) < _SET_CACHE_TTL:
            _set_ensured[key] = now
            return key
        # Recompute: materialise the current passing set as a new (timestamped) batch.
        p = dict(sel["params"])
        p["sk"] = key
        await ch.command(
            f"INSERT INTO {_SET_CACHE_TABLE} (sel_key, wallet, computed_at)\n"
            f"WITH {sel['cte_block']}\n"
            f"SELECT {{sk:String}} AS sel_key, w.wallet AS wallet, now() AS computed_at\n"
            f"{sel['from_where_block']}",
            parameters=p,
        )
        _set_ensured[key] = now
    return key


async def _warm_passing(ch, sel: dict) -> None:
    """Background cache warm — never raises into the request path."""
    try:
        await _resolve_passing(ch, sel)
    except Exception:  # noqa: BLE001
        logging.exception("passing-set warm failed")


# ── CUTOFF (union-over-lookbacks) smart-wallet selection ──────────────────
# The Fixed builder resolves ONE passing set for one lookback at a cutoff day.
# The Cutoff selection resolves the SAME criteria over SEVERAL lookback windows
# at one cutoff and UNIONs the passing wallets into a single STATIC set. Because
# the set never changes over time, the chart/token views sum OI over it with no
# per-day refiltering (fast) and there's no per-bucket wallet count.
_CUTOFF_LOOKBACKS = (1, 3, 7, 14, 30, 90)


def _parse_cutoff_lookbacks(request) -> list[int]:
    raw = request.args.get("lookbacks")
    if not raw:
        return list(_CUTOFF_LOOKBACKS)
    out: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = int(tok)
        except ValueError:
            continue
        if v in _CUTOFF_LOOKBACKS and v not in out:
            out.append(v)
    return out or list(_CUTOFF_LOOKBACKS)


def _cutoff_membership_sql(union_key: str, col: str = "w.wallet") -> str:
    """Predicate selecting the union set's latest batch. union_key is an md5 hex
    digest (safe to inline)."""
    return (
        f"{col} IN (SELECT wallet FROM {_SET_CACHE_TABLE} "
        f"WHERE sel_key = '{union_key}' AND computed_at = "
        f"(SELECT max(computed_at) FROM {_SET_CACHE_TABLE} WHERE sel_key = '{union_key}'))"
    )


async def _resolve_cutoff_passing(ch, request) -> tuple[str, int]:
    """Combine the per-lookback fixed passing sets at one cutoff into a static
    set. `combine` = 'union' (wallet passes ANY selected lookback; default) or
    'intersection' (passes EVERY selected lookback). Returns (sel_key,
    display_lookback) — display_lookback = max selected, for the table metrics."""
    lookbacks = _parse_cutoff_lookbacks(request)
    combine = request.args.get("combine", "union")
    if combine not in ("union", "intersection"):
        combine = "union"
    snapshot = request.args.get("snapshot")
    per_keys: list[str] = []
    base_echo = None
    for L in lookbacks:
        sel = _build_smart_wallet_selection(
            request, lookback_override=L, snapshot_override=snapshot)
        if base_echo is None:
            base_echo = sel["echo"]
        per_keys.append(await _resolve_passing(ch, sel))

    # Key = criteria (minus lookback) + cutoff + the lookback SET + combine mode.
    kf = {k: base_echo[k] for k in _PASSING_KEY_FIELDS if k != "lookback"}
    kf["cutoff_lookbacks"] = lookbacks
    kf["combine"] = combine
    union_key = "cut-" + hashlib.md5(
        json.dumps(kf, sort_keys=True, default=str).encode()).hexdigest()
    display_lb = max(lookbacks)

    async with _resolve_lock(union_key):
        now = time.time()
        if (last := _set_ensured.get(union_key)) and now - last < _SET_CACHE_TTL:
            return union_key, display_lb
        await _ensure_set_table(ch)
        r = await ch.query(
            f"SELECT toUnixTimestamp(max(computed_at)) FROM {_SET_CACHE_TABLE} WHERE sel_key = {{k:String}}",
            parameters={"k": union_key},
        )
        mx = r.result_rows[0][0] if (r and r.result_rows) else 0
        if mx and now - float(mx) < _SET_CACHE_TTL:
            _set_ensured[union_key] = now
            return union_key, display_lb

        keys_list = "(" + ",".join("'" + k + "'" for k in per_keys) + ")"
        # Intersection: wallet must appear in EVERY selected lookback's set (count
        # of distinct source keys = number of lookbacks). Union: any (no HAVING).
        having = (f"HAVING uniqExact(c.sel_key) = {len(lookbacks)}"
                  if combine == "intersection" else "")
        await ch.command(
            f"INSERT INTO {_SET_CACHE_TABLE} (sel_key, wallet, computed_at)\n"
            f"WITH latest AS (\n"
            f"    SELECT sel_key, max(computed_at) AS mc FROM {_SET_CACHE_TABLE}\n"
            f"    WHERE sel_key IN {keys_list} GROUP BY sel_key\n"
            f")\n"
            f"SELECT {{uk:String}} AS sel_key, c.wallet AS wallet, now() AS computed_at\n"
            f"FROM {_SET_CACHE_TABLE} c\n"
            f"INNER JOIN latest ON latest.sel_key = c.sel_key AND latest.mc = c.computed_at\n"
            f"GROUP BY c.wallet\n"
            f"{having}",
            parameters={"uk": union_key},
        )
        _set_ensured[union_key] = now
    return union_key, display_lb


# ── GROUP (pinned wallet-group) selection ─────────────────────────────────
# The wallet set IS a user-pinned group (tradernick.wallet_pins) — no criteria.
# Materialise the current membership into the shared set cache under a CONTENT-
# addressed key (group_id + sorted members), so a changed/emptied group gets a
# fresh key automatically and the OI/metrics/token queries reuse the same
# sel_key machinery. user_id is the single 'local' placeholder until real users.
_WALLET_PINS_USER = "local"


async def _resolve_group_passing(ch, request) -> str:
    group_id = request.args.get("group") or ""
    if not group_id:
        raise ValueError("missing group")
    await _ensure_set_table(ch)
    mem = await ch.query(
        "SELECT address FROM tradernick.wallet_pins FINAL "
        "WHERE user_id = {u:String} AND group_id = {g:String} AND deleted = 0 "
        "ORDER BY address",
        parameters={"u": _WALLET_PINS_USER, "g": group_id},
    )
    addrs = [r[0] for r in mem.result_rows]
    key = "grp-" + hashlib.md5((group_id + "|" + ",".join(addrs)).encode()).hexdigest()
    async with _resolve_lock(key):
        now = time.time()
        if (last := _set_ensured.get(key)) and now - last < _SET_CACHE_TTL:
            return key
        r = await ch.query(
            f"SELECT toUnixTimestamp(max(computed_at)) FROM {_SET_CACHE_TABLE} WHERE sel_key = {{k:String}}",
            parameters={"k": key},
        )
        mx = r.result_rows[0][0] if (r and r.result_rows) else 0
        if mx and now - float(mx) < _SET_CACHE_TTL:
            _set_ensured[key] = now
            return key
        # Empty group → no batch inserted; the queries then see 0 wallets (correct).
        if addrs:
            stamp = datetime.now(timezone.utc).replace(tzinfo=None)
            await ch.insert(
                _SET_CACHE_TABLE, [[key, a, stamp] for a in addrs],
                column_names=["sel_key", "wallet", "computed_at"],
            )
        _set_ensured[key] = now
    return key


# ── ROLLING smart-wallet selection ───────────────────────────────────────
# The Fixed builder computes ONE passing set for the single window
# [snapshot-lookback, snapshot]. The Rolling builder computes, for EVERY day D
# in [since, until], the set passing the SAME criteria over the trailing window
# [D-lookback, D]. The qualifying set differs per day.
#
# Correctness oracle: at D = the Fixed snapshot, the Rolling set MUST equal the
# Fixed set for the same lookback/criteria. We achieve this by mirroring every
# Fixed metric/guard, just windowed per-day:
#   - volume/realized/trades/fees trailing  = cum[D] - lagInFrame(cum, lookback, 0)
#     over the DENSE per-(day,wallet) Table A series (one row/day from inception,
#     so lag-by-lookback is exactly the value `lookback` calendar days back).
#   - Sharpe (annualized ×√365)             = mean/sd over the per-day daily_total
#     deltas in a ROWS-frame of `lookback` rows ending at D (= days D-lookback+1..D,
#     matching Fixed's `rd.d > start_day` window).
#   - taker%/funding/oi-share/volume-share trailing sums use a RANGE-frame on Date
#     (counts by calendar value, so it spans [D-lookback+1, D] even though the
#     fills/funding/eod tables are NOT dense), mirroring Fixed's
#     `day > start_day AND day <= end_day`.
#   - min_oi ("OI as of D")                 = day-D last OI from Table B.
#   - n_tokens / oi-share are GATED (only built when their guard is active) since
#     they are expensive, exactly like the Fixed builder.

_ROLLING_LOOKBACKS = (1, 3, 7, 14, 30)


def _build_rolling_selection(request, since_override=None, until_override=None):
    """Parse the SAME smart-wallet filter params as `_build_smart_wallet_selection`
    and build the CTE chain that emits a per-(day, wallet) PASSING relation over
    [since, until] — each day selecting wallets passing every guard over its own
    trailing [D-lookback, D] window.

    Returns a dict with `cte_block`, `passing_select` (the final SELECT body that
    materialises (day, wallet) rows), `params`, and `echo`. Raises ValueError on a
    bad param. Mirrors the Fixed builder's f-string-vs-CH-placeholder discipline:
    the `lookback` integer is substituted as a LITERAL into window frames /
    lagInFrame offsets (CH won't take a param there); everything else is a
    {name:Type} placeholder bound via `parameters=`.
    """
    token = request.args.get("token")
    if token in ("", "__all__", "__global__", "all"):
        token = None
    lookback = int(request.args.get("lookback", "7"))
    if lookback not in _ROLLING_LOOKBACKS:
        raise ValueError("lookback must be 1|3|7|14|30")

    NO_MAX = 1e12
    min_days = max(int(request.args.get("min_days", "3")), 1)

    def _f(name, default):
        try:
            return float(request.args.get(name, str(default)))
        except (TypeError, ValueError):
            return float(default)

    def _i(name, default):
        try:
            return int(request.args.get(name, str(default)))
        except (TypeError, ValueError):
            return int(default)

    min_volume = _f("min_volume", 0)
    min_realized = _f("min_realized", 0)
    # No-floor sentinel for the optional total/unrealized PnL criteria.
    _NO_MIN = -1e18
    min_unrealized = _f("min_unrealized", _NO_MIN)
    min_total_pnl = _f("min_total_pnl", _NO_MIN)
    min_oi = _f("min_oi", 0)
    min_avg_trade_size = _f("min_avg_trade_size", 0)
    min_taker_pct = _f("min_taker_pct", 0)
    max_fee_pct = _f("max_fee_pct", NO_MAX)
    max_funding_pct = _f("max_funding_pct", NO_MAX)
    min_account_duration = _i("min_account_duration", 0)
    min_tokens = _i("min_tokens", 0)
    min_win_rate = _f("min_win_rate", 0)
    max_trades_per_day = _f("max_trades_per_day", NO_MAX)
    min_trades_per_day = _f("min_trades_per_day", 0)
    min_annualized_sharpe = _f("min_annualized_sharpe", -NO_MAX)
    min_avg_oi_share = _f("min_avg_oi_share", 0)
    max_avg_oi_share = _f("max_avg_oi_share", NO_MAX)
    min_avg_oi = _f("min_avg_oi", 0)
    max_avg_oi = _f("max_avg_oi", NO_MAX)
    min_avg_global_oi = _f("min_avg_global_oi", 0)
    max_avg_global_oi = _f("max_avg_global_oi", NO_MAX)
    min_avg_global_oi_share = _f("min_avg_global_oi_share", 0)
    max_avg_global_oi_share = _f("max_avg_global_oi_share", NO_MAX)
    min_volume_share = _f("min_volume_share", 0)
    max_volume_share = _f("max_volume_share", NO_MAX)

    # Token scope makes n_tokens trivially 1 → ignore min_tokens (same as Fixed).
    if token is not None:
        min_tokens = 0

    since_arg = since_override or request.args.get("since")
    until_arg = until_override or request.args.get("until")
    if not since_arg or not until_arg:
        raise ValueError("missing since/until")
    # Parse the calendar date directly from the ISO string (date prefix) so the
    # day is timezone-independent — a date-only input like "2026-06-22" must map
    # to that exact day regardless of server/local TZ (a naive astimezone shift
    # would roll it back a day on a UTC+ host).
    try:
        since_day = datetime.fromisoformat(
            since_arg.replace("Z", "").split("T")[0].split(" ")[0]).date()
        until_day = datetime.fromisoformat(
            until_arg.replace("Z", "").split("T")[0].split(" ")[0]).date()
    except Exception:  # noqa: BLE001
        raise ValueError("invalid since/until")
    if until_day < since_day:
        raise ValueError("until before since")
    # Fetch from since-lookback so each day's trailing window is fully covered.
    fetch_start = since_day - timedelta(days=lookback + 1)
    # eod deltas need one extra preceding day (mirror Fixed's start_day - 2).
    eod_start = since_day - timedelta(days=lookback + 2)

    oi_share_active = (min_avg_oi_share > 0) or (max_avg_oi_share < NO_MAX)
    avg_oi_val_active = (min_avg_oi > 0) or (max_avg_oi < NO_MAX)
    cur_oi_active = oi_share_active or avg_oi_val_active     # needs oi_roll (current scope)
    g_val_active = (min_avg_global_oi > 0) or (max_avg_global_oi < NO_MAX)
    g_share_active = (min_avg_global_oi_share > 0) or (max_avg_global_oi_share < NO_MAX)
    global_oi_active = g_val_active or g_share_active
    # In token mode the global-OI guards need a SEPARATE global per-day window;
    # in global mode they reuse oi_roll/oi_total (the current window IS global),
    # so the current window must also build for global guards in global mode.
    build_cur_window = cur_oi_active or (global_oi_active and token is None)
    build_global_window = (token is not None) and global_oi_active
    tokens_active = min_tokens > 0

    params: dict = {
        "since_day": since_day, "until_day": until_day,
        "fetch_start": fetch_start, "eod_start": eod_start,
        "min_days": min_days, "min_volume": min_volume,
        "min_realized": min_realized, "min_oi": min_oi,
        "min_unrealized": min_unrealized, "min_total_pnl": min_total_pnl,
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_annualized_sharpe": min_annualized_sharpe,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_volume_share": min_volume_share, "max_volume_share": max_volume_share,
        "lookback": lookback,
    }
    if token is not None:
        params["token"] = token

    # Literal integers for window-frame / lagInFrame offsets (CH rejects params
    # there). `lb` = lookback (lagInFrame back-offset, dense Table A); `lbm1` =
    # lookback-1 (ROWS/RANGE trailing-frame width = `lookback` calendar days).
    lb = str(lookback)
    lbm1 = str(lookback - 1)
    tok_pred = "AND token = {token:String}" if token is not None else ""

    # ── Trailing volume/realized/trades/fees (dense Table A; global) ──
    if token is None:
        daily_cum = """
        daily_cum AS (
            SELECT day AS d, wallet,
                sumMerge(volume_state)      AS cum_vol,
                sumMerge(pnl_state)         AS cum_pnl,
                sumMerge(trade_count_state) AS cum_tc,
                sumMerge(fees_state)        AS cum_fees
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date}
            GROUP BY day, wallet
        )"""
    else:
        # Token scope: per-(day,wallet) cumulative argMax snapshots from the
        # source table. NOT guaranteed dense per wallet, so we can't use a
        # lagInFrame(lookback) row-offset; instead diff cum[D] against the last
        # snapshot <= D-lookback via a RANGE-excluding self-window. Simpler &
        # correct: take cum[D] minus cum as-of (D-lookback) using a second
        # series joined on day. We build a DENSE day spine per wallet via the
        # cumulative argMax carried forward (groupArray would be heavy); instead
        # reuse the same lag-by-calendar trick the fills path uses below.
        daily_cum = """
        daily_cum AS (
            SELECT d, wallet,
                max(cum_vol)  AS cum_vol,
                max(cum_pnl)  AS cum_pnl,
                max(cum_tc)   AS cum_tc,
                max(cum_fees) AS cum_fees
            FROM (
                SELECT toDate(time) AS d, wallet,
                    argMax(volume, time)      AS cum_vol,
                    argMax(pnl, time)         AS cum_pnl,
                    argMax(trade_count, time) AS cum_tc,
                    argMax(fees, time)        AS cum_fees
                FROM tradernick.hl_trade_history FINAL
                WHERE token = {token:String}
                  AND toDate(time) >= {fetch_start:Date} AND toDate(time) <= {until_day:Date}
                GROUP BY d, wallet
            )
            GROUP BY d, wallet
        )"""

    # For global the series is dense → lagInFrame by `lb` rows == `lb` days back.
    # For token it may be sparse → use a RANGE self-join to read cum as-of
    # (D - lookback). We unify by computing, per (d, wallet), the "base"
    # cumulative = the latest cum on a day <= d-lookback.
    if token is None:
        win_cte = (
            "        win AS (\n"
            "            SELECT d, wallet,\n"
            "                cum_vol  - lagInFrame(cum_vol,  " + lb + ", 0) OVER w AS volume,\n"
            "                cum_pnl  - lagInFrame(cum_pnl,  " + lb + ", 0) OVER w AS realized,\n"
            "                cum_tc   - lagInFrame(cum_tc,   " + lb + ", 0) OVER w AS trades,\n"
            "                cum_fees - lagInFrame(cum_fees, " + lb + ", 0) OVER w AS fees\n"
            "            FROM daily_cum\n"
            "            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC\n"
            "                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)\n"
            "        )")
    else:
        # base_cum[d] = the as-of cumulative snapshot on the latest day <=
        # d-lookback. The token series is SPARSE per wallet (only days it traded
        # that token), so we anchor a RANGE frame at d that ends `lookback` days
        # BEFORE d (rows with d' <= d-lookback) and take the LATEST such row's
        # value. anyLast(cum) OVER (… ORDER BY d ASC) == the value on the most
        # recent in-frame day — the true as-of snapshot. (max() would be WRONG
        # for realized pnl, which is NOT monotonic — losses pull it down.) A
        # wallet with no row that far back → base NULL → coalesce 0 → trailing =
        # cum[d] (all its activity is inside the window).
        win_cte = (
            "        win AS (\n"
            "            SELECT d, wallet,\n"
            "                cum_vol  - coalesce(base_vol, 0)  AS volume,\n"
            "                cum_pnl  - coalesce(base_pnl, 0)  AS realized,\n"
            "                cum_tc   - coalesce(base_tc, 0)   AS trades,\n"
            "                cum_fees - coalesce(base_fees, 0) AS fees\n"
            "            FROM (\n"
            "                SELECT d, wallet, cum_vol, cum_pnl, cum_tc, cum_fees,\n"
            "                    anyLast(cum_vol)  OVER b AS base_vol,\n"
            "                    anyLast(cum_pnl)  OVER b AS base_pnl,\n"
            "                    anyLast(cum_tc)   OVER b AS base_tc,\n"
            "                    anyLast(cum_fees) OVER b AS base_fees\n"
            "                FROM daily_cum\n"
            "                WINDOW b AS (PARTITION BY wallet ORDER BY d ASC\n"
            "                             RANGE BETWEEN UNBOUNDED PRECEDING AND " + lb + " PRECEDING)\n"
            "            )\n"
            "        )")

    # Sharpe: per-day daily_total deltas, windowed over the trailing `lookback`
    # rows (= calendar days, dense for global; token uses the same dense Table A
    # realized deltas via real_daily). Mirror Fixed's real_delta/eod_delta.
    if token is None:
        real_daily = """
        real_daily AS (
            SELECT day AS d, wallet,
                sumMerge(pnl_state)         AS cum_pnl,
                sumMerge(trade_count_state) AS cum_tc
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date}
            GROUP BY day, wallet
        )"""
        eod_daily = """
        eod_daily AS (
            SELECT day AS d, wallet, sum(eod) AS un
            FROM (
                SELECT day, wallet, token, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE day >= {eod_start:Date} AND day <= {until_day:Date}""" + HIP3_EXCLUDE + """
                GROUP BY day, wallet, token, side
            )
            GROUP BY day, wallet
        )"""
    else:
        real_daily = """
        real_daily AS (
            SELECT toDate(time) AS d, wallet,
                argMax(pnl, time)         AS cum_pnl,
                argMax(trade_count, time) AS cum_tc
            FROM tradernick.hl_trade_history FINAL
            WHERE token = {token:String}
              AND toDate(time) >= {fetch_start:Date} AND toDate(time) <= {until_day:Date}
            GROUP BY d, wallet
        )"""
        eod_daily = """
        eod_daily AS (
            SELECT day AS d, wallet, sum(eod) AS un
            FROM (
                SELECT day, wallet, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE token = {token:String}
                  AND day >= {eod_start:Date} AND day <= {until_day:Date}
                GROUP BY day, wallet, side
            )
            GROUP BY day, wallet
        )"""

    # min_oi ("OI as of day D"): day-D last total OI USD per (day, wallet).
    if token is None:
        oi_now = """
        oi_now AS (
            SELECT day AS d, wallet,
                argMaxIfMerge(last_total_oi_usd_state)   AS oi_usd
            FROM tradernick.hl_position_history_oi_wallet_daily
            WHERE day >= {since_day:Date} AND day <= {until_day:Date}
            GROUP BY day, wallet
        )"""
    else:
        # Token scope: last hourly OI of that token on day D (size_usd summed
        # over sides). Hourly scan limited to [since, until].
        oi_now = """
        oi_now AS (
            SELECT d, wallet, sum(abs(sz_last)) AS oi_usd
            FROM (
                SELECT toDate(bucket) AS d, wallet, side,
                    argMax(sz, bucket) AS sz_last
                FROM (
                    SELECT bucket, wallet, side, argMaxMerge(size_state) AS sz
                    FROM tradernick.hl_position_history_1h
                    WHERE token = {token:String}
                      AND bucket >= {since_day:Date} AND bucket < {until_day:Date} + 1
                    GROUP BY bucket, wallet, side
                )
                GROUP BY d, wallet, side
            )
            GROUP BY d, wallet
        )"""

    # The fills / funding / OI rollups are SPARSE per wallet (only days with
    # activity). A RANGE-window anchored on those rows would emit NO row for a
    # candidate day D on which the wallet had no fill/funding/OI entry — even
    # though its trailing window [D-lookback+1, D] still legitimately covers
    # earlier active days. That breaks the join in passing_select (NULL → a
    # wrongly-zeroed metric). So every trailing sum is anchored on the DENSE
    # candidate-day spine (the wallet's Table A days, which exist for every day),
    # via a range self-join: for each candidate (D, wallet) sum the metric rows
    # whose day ∈ (D-lookback, D]. This exactly mirrors the Fixed builder, which
    # sums `day > start_day AND day <= end_day` irrespective of per-day presence.
    #
    # `spine` = candidate (d, wallet) pairs over the admitted range. Trailing
    # sums only need to be defined for these, so we restrict the spine to
    # [since, until].
    spine = (
        "        spine AS (\n"
        "            SELECT d, wallet FROM win\n"
        "            WHERE d >= {since_day:Date} AND d <= {until_day:Date}\n"
        "        )")

    taker_daily = (
        "        taker_daily AS (\n"
        "            SELECT wallet, day AS d,\n"
        "                sumMerge(taker_buy_vol_usd_state) + sumMerge(taker_sell_vol_usd_state) AS d_taker,\n"
        "                sumMerge(vol_usd_state) AS d_total\n"
        "            FROM tradernick.hl_fills_vol_daily\n"
        "            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date} " + tok_pred + "\n"
        "            GROUP BY wallet, day\n"
        "        ),\n"
        "        taker_agg AS (\n"
        "            SELECT s.wallet AS wallet, s.d AS d,\n"
        "                sum(t.d_taker) AS taker_vol,\n"
        "                sum(t.d_total) AS total_vol\n"
        "            FROM spine s\n"
        "            INNER JOIN taker_daily t ON t.wallet = s.wallet\n"
        "            WHERE t.d > s.d - " + lb + " AND t.d <= s.d\n"
        "            GROUP BY s.wallet, s.d\n"
        "        )")

    funding_daily = (
        "        funding_daily AS (\n"
        "            SELECT wallet, day AS d, sumMerge(funding_pnl_state) AS d_funding\n"
        "            FROM tradernick.hl_funding_daily\n"
        "            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date} " + tok_pred + "\n"
        "            GROUP BY wallet, day\n"
        "        ),\n"
        "        funding_agg AS (\n"
        "            SELECT s.wallet AS wallet, s.d AS d, sum(f.d_funding) AS funding\n"
        "            FROM spine s\n"
        "            INNER JOIN funding_daily f ON f.wallet = s.wallet\n"
        "            WHERE f.d > s.d - " + lb + " AND f.d <= s.d\n"
        "            GROUP BY s.wallet, s.d\n"
        "        )")

    first_seen = """
        first_seen AS (
            SELECT wallet, min(day) AS first_day
            FROM tradernick.hl_trade_history_wallet_daily
            GROUP BY wallet
        )"""

    # ── assemble the CTE chain ──
    cte_parts = [
        daily_cum, win_cte, real_daily,
        """        real_delta AS (
            SELECT d, wallet,
                cum_pnl - lagInFrame(cum_pnl, 1, 0) OVER w AS d_real,
                cum_tc  - lagInFrame(cum_tc,  1, 0) OVER w AS d_tc
            FROM real_daily
            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )""",
        eod_daily,
        """        eod_delta AS (
            SELECT d, wallet, un - lagInFrame(un, 1, 0) OVER w AS d_un
            FROM eod_daily
            WINDOW w AS (PARTITION BY wallet ORDER BY d ASC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )""",
        """        daily_series AS (
            SELECT rd.d AS d, rd.wallet AS wallet,
                rd.d_real + coalesce(ed.d_un, 0) AS daily_total,
                rd.d_tc AS d_tc
            FROM real_delta rd
            LEFT JOIN eod_delta ed ON ed.wallet = rd.wallet AND ed.d = rd.d
        )""",
        # Rolling Sharpe over the trailing window ending at D. We frame by
        # CALENDAR day (RANGE on Date), not physical rows, so it spans exactly
        # [D-lookback+1, D] even when the daily_series is sparse (token scope —
        # only active trade days have rows). For the dense global series RANGE
        # and ROWS coincide. This matches Fixed, which aggregates every in-window
        # day that has a row (`rd.d > start_day AND rd.d <= end_day`).
        # daily_total/d_tc deltas at the FIRST fetched row are inflated by the
        # lag-0 default, but that row (d <= fetch_start) is `lookback` days before
        # since_day, so it never falls inside an admitted day's frame.
        "        sharpe_agg AS (\n"
        "            SELECT d, wallet,\n"
        "                countIf(d_tc > 0) OVER f AS n_days,\n"
        "                if(countIf(d_tc > 0) OVER f >= {min_days:UInt32}\n"
        "                     AND stddevPop(daily_total) OVER f > 0,\n"
        "                   avg(daily_total) OVER f / stddevPop(daily_total) OVER f * sqrt(365),\n"
        "                   0) AS sharpe,\n"
        "                100 * countIf(d_tc > 0 AND daily_total > 0) OVER f\n"
        "                    / nullIf(countIf(d_tc > 0) OVER f, 0) AS win_rate\n"
        "            FROM daily_series\n"
        "            WINDOW f AS (PARTITION BY wallet ORDER BY d ASC\n"
        "                         RANGE BETWEEN " + lbm1 + " PRECEDING AND CURRENT ROW)\n"
        "        )",
        oi_now,
        spine,
        taker_daily,
        funding_daily,
        first_seen,
    ]

    # Gated OI window (per-(day,wallet) trailing OI sum + per-day total). Needed
    # for both the avg-OI-value guard and the avg-OI-share guard. Same dense-spine
    # self-join as taker/funding so a candidate day with no OI row that day still
    # sees its earlier in-window OI.
    if build_cur_window:
        if token is None:
            cte_parts.append(
                "        oi_window AS (\n"
                "            SELECT wallet, day AS d, sumMerge(s_total_oi_usd_state) AS d_oi\n"
                "            FROM tradernick.hl_position_history_oi_wallet_daily\n"
                "            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date}\n"
                "            GROUP BY wallet, day\n"
                "        ),\n"
                "        oi_roll AS (\n"
                "            SELECT s.wallet AS wallet, s.d AS d, sum(o.d_oi) AS oi_sum\n"
                "            FROM spine s\n"
                "            INNER JOIN oi_window o ON o.wallet = s.wallet\n"
                "            WHERE o.d > s.d - " + lb + " AND o.d <= s.d\n"
                "            GROUP BY s.wallet, s.d\n"
                "        )")
        else:
            cte_parts.append(
                "        oi_window AS (\n"
                "            SELECT wallet, toDate(bucket) AS d, sum(abs(sz)) AS d_oi\n"
                "            FROM (\n"
                "                SELECT wallet, bucket, side, argMaxMerge(size_state) AS sz\n"
                "                FROM tradernick.hl_position_history_1h\n"
                "                WHERE token = {token:String}\n"
                "                  AND bucket >= {fetch_start:Date} AND bucket < {until_day:Date} + 1\n"
                "                GROUP BY wallet, bucket, side\n"
                "            )\n"
                "            GROUP BY wallet, d\n"
                "        ),\n"
                "        oi_roll AS (\n"
                "            SELECT s.wallet AS wallet, s.d AS d, sum(o.d_oi) AS oi_sum\n"
                "            FROM spine s\n"
                "            INNER JOIN oi_window o ON o.wallet = s.wallet\n"
                "            WHERE o.d > s.d - " + lb + " AND o.d <= s.d\n"
                "            GROUP BY s.wallet, s.d\n"
                "        )")
        # Per-day market OI total (denominator) across ALL wallets.
        cte_parts.append(
            "        oi_total AS (\n"
            "            SELECT d, sum(oi_sum) AS tot FROM oi_roll GROUP BY d\n"
            "        )")

    # Separate GLOBAL (all-tokens) trailing OI window for the *_avg_global_oi
    # guards when a token is selected (cheap per-day rollup; same shape as the
    # global oi_roll above).
    if build_global_window:
        cte_parts.append(
            "        oi_window_global AS (\n"
            "            SELECT wallet, day AS d, sumMerge(s_total_oi_usd_state) AS d_oi\n"
            "            FROM tradernick.hl_position_history_oi_wallet_daily\n"
            "            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date}\n"
            "            GROUP BY wallet, day\n"
            "        ),\n"
            "        oi_roll_global AS (\n"
            "            SELECT s.wallet AS wallet, s.d AS d, sum(o.d_oi) AS oi_sum\n"
            "            FROM spine s\n"
            "            INNER JOIN oi_window_global o ON o.wallet = s.wallet\n"
            "            WHERE o.d > s.d - " + lb + " AND o.d <= s.d\n"
            "            GROUP BY s.wallet, s.d\n"
            "        ),\n"
            "        oi_total_global AS (\n"
            "            SELECT d, sum(oi_sum) AS tot FROM oi_roll_global GROUP BY d\n"
            "        )")

    # n_tokens (distinct tokens over the trailing window) — gated (only when
    # min_tokens > 0). There is no rolling uniqExact window fn, so we range
    # self-join the per-day token set against the DENSE candidate spine: for each
    # candidate (D, wallet) count distinct tokens whose fill day ∈ (D-lookback, D].
    # Anchoring on `spine` (not tok_daily) is essential — a candidate day on
    # which the wallet has no fill still has earlier in-window tokens, mirroring
    # the Fixed builder's `uniqExact(token)` over `(start_day, end_day]`.
    if tokens_active:
        cte_parts.append(
            "        tok_daily AS (\n"
            "            SELECT DISTINCT wallet, day AS d, token\n"
            "            FROM tradernick.hl_fills_vol_daily\n"
            "            WHERE day >= {fetch_start:Date} AND day <= {until_day:Date}\n"
            "        ),\n"
            "        ntok AS (\n"
            "            SELECT s.wallet AS wallet, s.d AS d, uniqExact(b.token) AS n_tokens\n"
            "            FROM spine s\n"
            "            INNER JOIN tok_daily b ON b.wallet = s.wallet\n"
            "            WHERE b.d > s.d - " + lb + " AND b.d <= s.d\n"
            "            GROUP BY s.wallet, s.d\n"
            "        )")

    # Per-day volume total (denominator for volume-share) across ALL wallets.
    cte_parts.append(
        "        vol_total AS (\n"
        "            SELECT d, sum(volume) AS tot FROM win\n"
        "            WHERE d >= {since_day:Date} AND d <= {until_day:Date}\n"
        "            GROUP BY d\n"
        "        )")

    cte_block = ",\n".join(cte_parts)

    # ── FROM / JOIN / WHERE: keep (d, wallet) passing every guard, only for
    # admitted days [since, until]. Mirrors the Fixed from_where_block 1:1, with
    # every join also keyed on `d` (the per-day dimension).
    oi_share_join = (
        "\n        LEFT JOIN oi_roll ow  ON ow.wallet = w.wallet AND ow.d = w.d"
        "\n        LEFT JOIN oi_total ot ON ot.d = w.d" if build_cur_window else "")
    global_join = (
        "\n        LEFT JOIN oi_roll_global gw  ON gw.wallet = w.wallet AND gw.d = w.d"
        "\n        LEFT JOIN oi_total_global gt ON gt.d = w.d" if build_global_window else "")
    ntok_join = (
        "\n        LEFT JOIN ntok nt ON nt.wallet = w.wallet AND nt.d = w.d"
        if tokens_active else "")
    ntok_expr = "coalesce(nt.n_tokens, 0)" if tokens_active else "1"
    # avg OI (USD) over the trailing lookback = Σ daily OI ÷ (lookback days × 24).
    _avg_oi_r = "ow.oi_sum / nullIf(toFloat64(" + lb + ") * 24, 0)"
    avg_oi_guard = (
        "\n          AND coalesce(" + _avg_oi_r + ", 0) >= {min_avg_oi:Float64}"
        "\n          AND coalesce(" + _avg_oi_r + ", 0) <= {max_avg_oi:Float64}"
        if avg_oi_val_active else "")
    oi_share_guard = (
        "\n          AND coalesce(10000 * ow.oi_sum / nullIf(ot.tot, 0), 0) >= {min_avg_oi_share:Float64}"
        "\n          AND coalesce(10000 * ow.oi_sum / nullIf(ot.tot, 0), 0) <= {max_avg_oi_share:Float64}"
        if oi_share_active else "")
    # Global-OI guards: token mode uses gw/gt; global mode reuses ow/ot.
    _gr = "ow" if token is None else "gw"
    _gt = "ot" if token is None else "gt"
    _g_avg_r = _gr + ".oi_sum / nullIf(toFloat64(" + lb + ") * 24, 0)"
    global_val_guard = (
        "\n          AND coalesce(" + _g_avg_r + ", 0) >= {min_avg_global_oi:Float64}"
        "\n          AND coalesce(" + _g_avg_r + ", 0) <= {max_avg_global_oi:Float64}"
        if g_val_active else "")
    global_share_guard = (
        "\n          AND coalesce(10000 * " + _gr + ".oi_sum / nullIf(" + _gt + ".tot, 0), 0) >= {min_avg_global_oi_share:Float64}"
        "\n          AND coalesce(10000 * " + _gr + ".oi_sum / nullIf(" + _gt + ".tot, 0), 0) <= {max_avg_global_oi_share:Float64}"
        if g_share_active else "")

    passing_select = (
        "        SELECT w.d AS day, w.wallet AS wallet\n"
        "        FROM win w\n"
        "        LEFT JOIN sharpe_agg sa ON sa.wallet = w.wallet AND sa.d = w.d\n"
        "        LEFT JOIN oi_now oi     ON oi.wallet = w.wallet AND oi.d = w.d\n"
        "        LEFT JOIN taker_agg tk  ON tk.wallet = w.wallet AND tk.d = w.d\n"
        "        LEFT JOIN funding_agg fn ON fn.wallet = w.wallet AND fn.d = w.d\n"
        "        LEFT JOIN first_seen fseen ON fseen.wallet = w.wallet\n"
        "        LEFT JOIN eod_daily edl ON edl.wallet = w.wallet AND edl.d = w.d\n"
        "        LEFT JOIN vol_total vt ON vt.d = w.d" + oi_share_join + global_join + ntok_join + "\n"
        "        WHERE w.d >= {since_day:Date} AND w.d <= {until_day:Date}\n"
        "          AND w.volume >= {min_volume:Float64}\n"
        "          AND w.realized >= {min_realized:Float64}\n"
        "          AND coalesce(edl.un, 0) >= {min_unrealized:Float64}\n"
        "          AND w.realized + coalesce(edl.un, 0) >= {min_total_pnl:Float64}\n"
        "          AND coalesce(oi.oi_usd, 0) >= {min_oi:Float64}\n"
        "          AND coalesce(sa.n_days, 0) >= {min_days:UInt32}\n"
        "          AND coalesce(w.volume / nullIf(w.trades, 0), 0) >= {min_avg_trade_size:Float64}\n"
        "          AND coalesce(100 * tk.taker_vol / nullIf(tk.total_vol, 0), 0) >= {min_taker_pct:Float64}\n"
        "          AND (w.realized <= 0 OR 100 * w.fees / w.realized <= {max_fee_pct:Float64})\n"
        "          AND (w.realized <= 0 OR 100 * coalesce(fn.funding, 0) / w.realized <= {max_funding_pct:Float64})\n"
        "          AND " + ntok_expr + " >= {min_tokens:UInt32}\n"
        "          AND coalesce(dateDiff('day', fseen.first_day, w.d), 0) >= {min_account_duration:UInt32}\n"
        "          AND coalesce(sa.win_rate, 0) >= {min_win_rate:Float64}\n"
        "          AND coalesce(w.trades / nullIf(sa.n_days, 0), 0) <= {max_trades_per_day:Float64}\n"
        "          AND coalesce(w.trades / nullIf(sa.n_days, 0), 0) >= {min_trades_per_day:Float64}\n"
        "          AND coalesce(sa.sharpe, 0) >= {min_annualized_sharpe:Float64}\n"
        "          AND coalesce(10000 * w.volume / nullIf(vt.tot, 0), 0) >= {min_volume_share:Float64}\n"
        "          AND coalesce(10000 * w.volume / nullIf(vt.tot, 0), 0) <= {max_volume_share:Float64}"
        + avg_oi_guard + oi_share_guard + global_val_guard + global_share_guard)

    echo = {
        "token": token, "lookback": lookback,
        "since": since_day.isoformat(), "until": until_day.isoformat(),
        "min_days": min_days, "min_volume": min_volume,
        "min_realized": min_realized, "min_oi": min_oi,
        "min_unrealized": min_unrealized, "min_total_pnl": min_total_pnl,
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_annualized_sharpe": min_annualized_sharpe,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_avg_oi": min_avg_oi, "max_avg_oi": max_avg_oi,
        "min_avg_global_oi": min_avg_global_oi, "max_avg_global_oi": max_avg_global_oi,
        "min_avg_global_oi_share": min_avg_global_oi_share,
        "max_avg_global_oi_share": max_avg_global_oi_share,
        "min_volume_share": min_volume_share, "max_volume_share": max_volume_share,
    }
    return {
        "cte_block": cte_block, "passing_select": passing_select,
        "params": params, "echo": echo,
    }


# ── Rolling passing-set cache (per (day, wallet)) ────────────────────────
_ROLLING_SET_CACHE_TABLE = "tradernick.smart_wallet_rolling_set_cache"
_ROLLING_SET_CACHE_TTL = 300.0
# Set-defining fields: same criteria as Fixed PLUS the rolling range bounds.
_ROLLING_KEY_FIELDS = (
    "token", "lookback", "since", "until", "min_days", "min_volume",
    "min_realized", "min_unrealized", "min_total_pnl",
    "min_oi", "min_avg_trade_size", "min_taker_pct",
    "max_fee_pct", "max_funding_pct", "min_account_duration", "min_tokens",
    "min_win_rate", "min_trades_per_day", "max_trades_per_day",
    "min_annualized_sharpe", "min_avg_oi_share", "max_avg_oi_share",
    "min_avg_oi", "max_avg_oi", "min_avg_global_oi", "max_avg_global_oi",
    "min_avg_global_oi_share", "max_avg_global_oi_share",
    "min_volume_share", "max_volume_share",
)
_rolling_ensured: dict[str, float] = {}


def _rolling_key(sel: dict) -> str:
    e = sel["echo"]
    blob = json.dumps({k: e[k] for k in _ROLLING_KEY_FIELDS}, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()


async def _ensure_rolling_set_table(ch) -> None:
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {_ROLLING_SET_CACHE_TABLE} (\n"
        "    sel_key     String,\n"
        "    day         Date,\n"
        "    wallet      String,\n"
        "    computed_at DateTime DEFAULT now()\n"
        ") ENGINE = ReplacingMergeTree(computed_at)\n"
        "ORDER BY (sel_key, day, wallet)\n"
        "TTL computed_at + INTERVAL 1 DAY"
    )


async def _resolve_rolling_passing(ch, sel: dict) -> str:
    """Ensure the rolling per-(day, wallet) passing set is cached in CH and
    return its `sel_key`. The OI route reads it via a sub-SELECT joined on
    toDate(bucket) = day. Mirrors `_resolve_passing` (freshness + TTL + recompute
    as a new timestamped batch)."""
    key = _rolling_key(sel)
    async with _resolve_lock(key):
        now = time.time()
        # The rolling per-(day,wallet) set for a fixed day-range is deterministic.
        # A range ending BEFORE today is immutable, so reuse its cached rows for
        # their whole lifetime (the table's 1-day TTL) — a token change / pan must
        # never trigger the ~30s recompute. A range that INCLUDES today can still
        # shift as today's day keeps ingesting, so it refreshes on a 30-min window
        # (was a flat 5 min, which made any reload after 5 min pay the full
        # recompute — the reported "token change is slow").
        until_day = sel["echo"].get("until")
        today_iso = datetime.now(timezone.utc).date().isoformat()
        includes_today = (not until_day) or (str(until_day) >= today_iso)
        fresh_window = 1800.0 if includes_today else 86400.0
        if (last := _rolling_ensured.get(key)) and now - last < fresh_window:
            return key
        await _ensure_rolling_set_table(ch)
        r = await ch.query(
            f"SELECT toUnixTimestamp(max(computed_at)) FROM {_ROLLING_SET_CACHE_TABLE} WHERE sel_key = {{k:String}}",
            parameters={"k": key},
        )
        mx = r.result_rows[0][0] if (r and r.result_rows) else 0
        if mx and now - float(mx) < fresh_window:
            _rolling_ensured[key] = now
            return key
        p = dict(sel["params"])
        p["sk"] = key
        await ch.command(
            f"INSERT INTO {_ROLLING_SET_CACHE_TABLE} (sel_key, day, wallet, computed_at)\n"
            f"WITH {sel['cte_block']}\n"
            f"SELECT {{sk:String}} AS sel_key, day, wallet, now() AS computed_at\n"
            f"FROM (\n{sel['passing_select']}\n)",
            parameters=p,
        )
        _rolling_ensured[key] = now
    return key


@bp.get("/hyperliquid/smart_wallet_metrics")
@throttled("heavy")
async def smart_wallet_metrics(request):
    """Experimental smart-wallet finder table.

    For a single fixed window (one `lookback` ending at a `snapshot` day) it
    ranks wallets by the selected `metric` and returns, per wallet, the core
    columns plus the metric column:

      volume, realized_pnl, unrealized_pnl, oi_token, oi_usd, <metric>

    All figures are Hyperliquid-only. Scope is GLOBAL (all tokens) when `token`
    is absent/`__all__`, else restricted to that token. Global reads the fast
    per-(day,wallet) rollups (hl_trade_history_wallet_daily / Table A,
    hl_position_history_oi_wallet_daily / Table B); token scope reads the
    source tables.

    metric=sharpe is an ANNUALIZED (×√365), non-capital-normalized Sharpe:
    mean(daily_total_pnl) / stddevPop(daily_total_pnl) × √365 over ALL days in
    the window (including idle, non-trade days — an idle day's daily_total is its
    mark-to-market unrealized change), where
    daily_total_pnl[d] = Δrealized[d] + Δunrealized[d]. Counting all days avoids
    flattering wallets that look great on their few trade days but bleed on the
    days in between. `min_days` is still a minimum on ACTIVE (trade) days. Minus
    the /OI normalization vs the smart-money Sharpe.

    A wallet only enters the ranking if it has >= min_days active days AND >=
    min_volume window volume (the noise guard — both configurable). Top `limit`
    by the metric; the client re-sorts the returned set on any column.

    Query params:
      token     — token symbol; absent or '__all__' → global (all tokens)
      lookback  — window length in days (1|7|30|90|150; default 7)
      snapshot  — ISO date/datetime ending the window (default: start of today)
      metric    — ranking metric; only 'sharpe' for now (default 'sharpe')
      order_by  — sharpe|volume|realized|unrealized|oi_usd (default = metric)
      limit     — top-N cap (default 100, max 500)
      min_days  — min active days in window (noise guard; default 3)
      min_volume— min window volume USD (noise guard; default 0)
      min_realized— min window realized PnL USD (default 0 → profitable only)
      min_unrealized— min current unrealized PnL USD (default off / no floor)
      min_total_pnl— min realized+unrealized PnL USD (default off / no floor)
      min_oi    — min open interest USD as of the snapshot (default 0)
    """
    # Cutoff mode: rank the static union-over-lookbacks set (membership from the
    # cache) with per-wallet metrics over the longest selected lookback. Else the
    # regular single-window guarded selection.
    group = request.args.get("group")
    cutoff = request.args.get("cutoff") in ("1", "true", "yes")
    try:
        if group:
            # Group mode: stats for the pinned group's wallets over the request
            # lookback ending at the latest snapshot (no criteria guards).
            ch = await client()
            group_key = await _resolve_group_passing(ch, request)
            sel = _build_smart_wallet_selection(
                request, include_avg_oi=True,
                membership_override=_cutoff_membership_sql(group_key))
        elif cutoff:
            ch = await client()
            union_key, display_lb = await _resolve_cutoff_passing(ch, request)
            sel = _build_smart_wallet_selection(
                request, include_avg_oi=True,
                lookback_override=display_lb,
                snapshot_override=request.args.get("snapshot"),
                membership_override=_cutoff_membership_sql(union_key))
        else:
            sel = _build_smart_wallet_selection(request, include_avg_oi=True)
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    # `count() OVER () rides the filtered (pre-LIMIT) set, so total_found is the
    # FULL number of wallets passing every guard even though we only return the
    # top `limit` rows. The table shows the top-N but reports this total.
    sql = f"""
        WITH {sel['cte_block']}
        SELECT
            w.wallet AS wallet,
            w.volume AS volume,
            w.realized AS realized_pnl,
            coalesce(u.unrealized, 0) AS unrealized_pnl,
            {sel['oi_token_select']},
            coalesce(oi.oi_usd, 0) AS oi_usd,
            coalesce(sa.sharpe, 0) AS metric,
            coalesce(sa.n_days, 0) AS n_days,
            w.volume / nullIf(w.trades, 0) AS avg_trade_size,
            100 * coalesce(tk.taker_vol, 0) / nullIf(tk.total_vol, 0) AS taker_pct,
            100 * w.fees / nullIf(w.realized, 0) AS fee_pct,
            100 * coalesce(fn.funding, 0) / nullIf(w.realized, 0) AS funding_pct,
            coalesce(tk.n_tokens, 0) AS n_tokens,
            dateDiff('day', fseen.first_day, {{end_day:Date}}) AS account_age_days,
            coalesce(sa.win_rate, 0) AS win_rate,
            w.trades / nullIf(sa.n_days, 0) AS trades_per_day,
            dictGet('tradernick.wallet_labels', 'categories', lower(w.wallet)) AS categories,
            {sel['avg_oi_select']},
            count() OVER () AS total_found
        {sel['from_where_block']}
        ORDER BY {sel['order_col']} DESC
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters=sel["params"])
    wallets = [
        {
            "wallet": r[0],
            "volume": float(r[1]),
            "realized_pnl": float(r[2]),
            "unrealized_pnl": float(r[3]),
            "oi_token": (float(r[4]) if r[4] is not None else None),
            "oi_usd": float(r[5]),
            "metric": float(r[6]),
            "n_days": int(r[7]),
            "avg_trade_size": (float(r[8]) if r[8] is not None else None),
            "taker_pct": (float(r[9]) if r[9] is not None else None),
            "fee_pct": (float(r[10]) if r[10] is not None else None),
            "funding_pct": (float(r[11]) if r[11] is not None else None),
            "n_tokens": int(r[12]) if r[12] is not None else 0,
            "account_age_days": int(r[13]) if r[13] is not None else 0,
            "win_rate": (float(r[14]) if r[14] is not None else None),
            "trades_per_day": (float(r[15]) if r[15] is not None else None),
            "categories": list(r[16]) if r[16] else [],
            "avg_oi": (float(r[17]) if r[17] is not None else None),
        }
        for r in rows.result_rows
    ]
    total = int(rows.result_rows[0][18]) if rows.result_rows else 0
    # Warm the passing-set cache in the background so the chart view (which uses
    # the SAME selection) reuses it instead of recomputing. Fire-and-forget; if
    # the chart loads before this finishes it just computes (and caches) itself.
    asyncio.create_task(_warm_passing(ch, sel))
    return response.json({
        **sel["echo"],
        "total": total,
        "wallets": wallets,
    })


@bp.get("/hyperliquid/smart_oi")
@throttled("heavy")
async def smart_oi(request):
    """Per-bucket HL OI restricted to a smart-wallet leaderboard.

    Returns the same series shape as /oi_split (long_oi, short_oi, total_oi
    in token + USD) but filtered to wallets the SmartSelector picks each
    day. The selector takes a JSON `selector` param documented in
    services/data_server/src/wallets/smart_selector.py — criteria-based,
    one sort metric, rolling lookback. The selector module materialises
    a `smart_wallets` CTE that this route joins against per-day.

    Query params:
      token, interval, since, until, limit — same as /oi_split.
      selector — JSON {lookback,top_n,scope,sort_by,criteria:[…]}.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    # `filter` is the composable (possibly nested) form; `selector` is the
    # legacy flat form. from_json handles both — a flat object just has no refs.
    selector_raw = request.args.get("filter") or request.args.get("selector")

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    try:
        selector = SmartSelector.from_json(selector_raw, token=token)
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    # Same MV cascade as /oi_split — 1h MV for hourly+, 15m MV for 15m/30m,
    # raw table otherwise. Adds toDate(bucket) so we can join the per-day
    # `smart_wallets` array from the selector.
    if seconds >= 3600 and seconds % 3600 == 0:
        oi_source = "tradernick.hl_position_history_1h"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    elif seconds >= 900 and seconds % 900 == 0:
        oi_source = "tradernick.hl_position_history_15m"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    else:
        oi_source = "tradernick.hl_position_history"
        oi_time_col = "time"
        oi_amount_expr = "argMax(amount, time)"
        oi_size_expr   = "argMax(size,   time)"

    ch = await client()
    selector_cte_sql, smart_cte_name, selector_params = await wallets_cache.resolve(
        ch, selector, token, since_dt, until_dt)
    params: dict = {
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
        **selector_params,
    }

    sql = f"""
        {selector_cte_sql}
        SELECT
            toUnixTimestamp(bucket)                AS bucket,
            sumIf(latest_amount, side='long')      AS long_oi,
            sumIf(latest_amount, side='short')     AS short_oi,
            sum(latest_amount)                     AS total_oi,
            sumIf(latest_size,   side='long')      AS long_oi_value,
            sumIf(latest_size,   side='short')     AS short_oi_value,
            sum(latest_size)                       AS total_oi_value,
            -- Count of unique wallets that survived the criteria for the
            -- day this bucket falls into. Same value for every bucket in
            -- a day (the leaderboard is daily-granular). Surfaced so the
            -- chart can plot it as a "did the filter over-narrow" line.
            toUInt32(any(length(l.wallets)))       AS wallet_count
        FROM (
            SELECT
                toStartOfInterval({oi_time_col}, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                toDate({oi_time_col}) AS day,
                wallet, side,
                {oi_amount_expr} AS latest_amount,
                {oi_size_expr}   AS latest_size
            FROM {oi_source}
            WHERE token = {{token:String}}
              AND {oi_time_col} >= {{since:DateTime}}
              AND {oi_time_col} <  {{until:DateTime}}
            GROUP BY bucket, day, wallet, side
        ) p
        -- Equi-join on day; wallet membership check stays in WHERE because
        -- has(wallets, p.wallet) mixes columns from both sides and CH
        -- standard JOIN ON only accepts equi-joins.
        INNER JOIN {smart_cte_name} l ON l.day = p.day
        WHERE has(l.wallets, p.wallet)
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {
            "time": int(r[0]),
            "long_oi": float(r[1]),
            "short_oi": float(r[2]),
            "total_oi": float(r[3]),
            "long_oi_value": float(r[4]),
            "short_oi_value": float(r[5]),
            "total_oi_value": float(r[6]),
            "wallet_count": int(r[7]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "token": token,
        "interval": interval,
        "selector": selector.summary(),
        "series": series,
    })


@bp.get("/hyperliquid/smart_wallet_oi")
@throttled("heavy")
async def smart_wallet_oi(request):
    """Per-bucket HL OI aggregated over EVERY wallet the smart-wallet finder
    selects — not just the table's top-N. Reuses the exact selection from
    /smart_wallet_metrics (same query params) as a `passing` CTE, then sums OI
    for `oi_token` across [since, until) for wallets in that set. This is how the
    finder's Chart view plots the OI of all found wallets (possibly thousands)
    without ever shipping the address list to the client.

    Selection params (define the wallet set; identical to /smart_wallet_metrics):
      token (scope; absent = global), lookback, snapshot, and all min_*/max_*.
    OI params:
      oi_token — the token whose OI to plot (required; the table scope `token`
                 only filters WHICH wallets qualify, not which OI to sum).
      interval, since, until, limit — same as /smart_oi.
    Returns the /oi_split-shaped series + wallet_count.
    """
    oi_token = request.args.get("oi_token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    oi_limit = int(request.args.get("limit", "200000"))
    if not oi_token:
        return response.json({"error": "missing oi_token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    # Static set modes: `group` (a pinned wallet group) or `cutoff` (union over
    # lookbacks). Both skip per-day refilter + the wallet count. Otherwise the
    # regular single-window fixed selection.
    group = request.args.get("group")
    cutoff = request.args.get("cutoff") in ("1", "true", "yes")
    sel = None
    if not cutoff and not group:
        try:
            sel = _build_smart_wallet_selection(request)
        except ValueError as e:
            return response.json({"error": str(e)}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    oi_since_dt = _parse_iso(since)
    oi_until_dt = _parse_iso(until)
    # Same MV cascade as /smart_oi.
    if seconds >= 3600 and seconds % 3600 == 0:
        oi_source = "tradernick.hl_position_history_1h"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    elif seconds >= 900 and seconds % 900 == 0:
        oi_source = "tradernick.hl_position_history_15m"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    else:
        oi_source = "tradernick.hl_position_history"
        oi_time_col = "time"
        oi_amount_expr = "argMax(amount, time)"
        oi_size_expr   = "argMax(size,   time)"

    ch = await client()
    # Resolve the selected wallet set into the shared CH cache (warmed by the
    # table view) or compute it once. The OI query then filters via a sub-SELECT
    # against the cache — no selection CTE and no giant IN-list over the wire —
    # so a cache hit makes the chart / token-switching / panning fast.
    if group:
        try:
            sel_key = await _resolve_group_passing(ch, request)
        except ValueError as e:
            return response.json({"error": str(e)}, status=400)
    elif cutoff:
        try:
            sel_key, _ = await _resolve_cutoff_passing(ch, request)
        except ValueError as e:
            return response.json({"error": str(e)}, status=400)
    else:
        sel_key = await _resolve_passing(ch, sel)

    params = {
        "seconds": seconds, "oi_token": oi_token,
        "oi_since": oi_since_dt, "oi_until": oi_until_dt, "oi_limit": oi_limit,
        "sel_key": sel_key,
    }

    sql = f"""
        SELECT
            toUnixTimestamp(bucket)                AS bucket,
            sumIf(latest_amount, side='long')      AS long_oi,
            sumIf(latest_amount, side='short')     AS short_oi,
            sum(latest_amount)                     AS total_oi,
            sumIf(latest_size,   side='long')      AS long_oi_value,
            sumIf(latest_size,   side='short')     AS short_oi_value,
            sum(latest_size)                       AS total_oi_value,
            toUInt32(uniqExact(wallet))            AS wallet_count,
            toUInt32(uniqExactIf(wallet, side='long'  AND latest_amount > 0)) AS long_count,
            toUInt32(uniqExactIf(wallet, side='short' AND latest_amount > 0)) AS short_count
        FROM (
            SELECT
                toStartOfInterval({oi_time_col}, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                wallet, side,
                {oi_amount_expr} AS latest_amount,
                {oi_size_expr}   AS latest_size
            FROM {oi_source}
            WHERE token = {{oi_token:String}}
              AND wallet IN (
                  SELECT wallet FROM {_SET_CACHE_TABLE}
                  WHERE sel_key = {{sel_key:String}}
                    AND computed_at = (SELECT max(computed_at) FROM {_SET_CACHE_TABLE} WHERE sel_key = {{sel_key:String}})
              )
              AND {oi_time_col} >= {{oi_since:DateTime}}
              AND {oi_time_col} <  {{oi_until:DateTime}}
            GROUP BY bucket, wallet, side
        ) p
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{oi_limit:UInt32}}
    """

    rows = await ch.query(sql, parameters=params)
    series = [
        {
            "time": int(r[0]),
            "long_oi": float(r[1]),
            "short_oi": float(r[2]),
            "total_oi": float(r[3]),
            "long_oi_value": float(r[4]),
            "short_oi_value": float(r[5]),
            "total_oi_value": float(r[6]),
            "wallet_count": int(r[7]),
            "long_count": int(r[8]),
            "short_count": int(r[9]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "token": oi_token,
        "interval": interval,
        "series": series,
    })


@bp.get("/hyperliquid/smart_wallet_token_list")
@throttled("heavy")
async def smart_wallet_token_list(request):
    """Per-token long/short OI summed across the FILTERED wallet set, at the
    latest position snapshot plus 24h-ago and 7d-ago, with absolute OI deltas and
    HL-perp 24h/7d price change. Returns every token the cohort holds (in any of
    the 3 snapshots); the client sorts in place. Powers the Smart Wallets
    (Dynamic) "Token List" view.

    Uses the SAME rolling per-day cohort as the chart (/smart_wallet_oi_rolling):
    each snapshot bucket is joined to the wallets that qualified over the trailing
    lookback ending THAT bucket's day, so the "now" OI matches the chart's latest
    point and the 24h/7d values match the chart's historical points. OI is summed
    over the hourly position rollup (hl_position_history_1h, argMax-state)."""
    ch = await client()

    raw_rows = await ch.query("SELECT max(bucket) FROM tradernick.hl_position_history_1h")
    if not raw_rows.result_rows or raw_rows.result_rows[0][0] is None:
        return response.json({"tokens": []})
    raw_now = raw_rows.result_rows[0][0]

    # Static set (fixed-set join on every bucket; t_now = the latest bucket):
    # `group` (a pinned wallet group) or `cutoff` (union over lookbacks). Else the
    # Dynamic rolling per-day cohort.
    group = request.args.get("group")
    cutoff = request.args.get("cutoff") in ("1", "true", "yes")
    static = bool(group) or cutoff
    static_key = None
    sel_key = None
    if static:
        try:
            static_key = (await _resolve_group_passing(ch, request)) if group \
                else (await _resolve_cutoff_passing(ch, request))[0]
        except ValueError as e:
            return response.json({"error": str(e)}, status=400)
        t_now = raw_now
    else:
        # Resolve the rolling per-day cohort over a generous trailing window —
        # wide enough that the 7d snapshot stays covered after "now" is pulled
        # back to the cohort's latest covered day (request carries no since/until).
        try:
            sel = _build_rolling_selection(
                request,
                since_override=(raw_now - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                until_override=raw_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except ValueError as e:
            return response.json({"error": str(e)}, status=400)
        sel_key = await _resolve_rolling_passing(ch, sel)
        # The rolling cohort is per COMPLETE day, so it lags position_history by
        # ~1 day. Snap "now" to the latest bucket the cohort covers — exactly the
        # chart's latest point (the chart drops uncovered current-day buckets).
        mday_rows = await ch.query(
            f"SELECT max(day) FROM {_ROLLING_SET_CACHE_TABLE} WHERE sel_key = {{k:String}} "
            f"AND computed_at = (SELECT max(computed_at) FROM {_ROLLING_SET_CACHE_TABLE} WHERE sel_key = {{k:String}})",
            parameters={"k": sel_key},
        )
        if not mday_rows.result_rows or mday_rows.result_rows[0][0] is None:
            return response.json({"tokens": []})
        rolling_max_day = mday_rows.result_rows[0][0]
        tnow_rows = await ch.query(
            "SELECT max(bucket) FROM tradernick.hl_position_history_1h WHERE toDate(bucket) = {d:Date}",
            parameters={"d": rolling_max_day},
        )
        if not tnow_rows.result_rows or tnow_rows.result_rows[0][0] is None:
            return response.json({"tokens": []})
        t_now = tnow_rows.result_rows[0][0]
    t_1h = t_now - timedelta(hours=1)
    t_4h = t_now - timedelta(hours=4)
    t_24h = t_now - timedelta(hours=24)
    t_7d = t_now - timedelta(days=7)

    params = {"t_now": t_now, "t_1h": t_1h, "t_4h": t_4h, "t_24h": t_24h, "t_7d": t_7d}
    if sel_key is not None:
        params["sel_key"] = sel_key

    # Inner: latest position per (token, side, wallet, bucket) (argMaxMerge over
    # the hour's state), carrying the bucket's day. INNER JOIN the rolling per-day
    # passing set on (wallet, day) so each snapshot is summed over the cohort that
    # qualified AS OF that bucket's day — identical to the chart. Outer: sum per
    # token, split by side × snapshot, in token units (amt) and USD (sz).
    def _oi_cols(b):
        return (
            f"sumIf(amt, side='long'  AND b='{b}') AS long_{b}_t,"
            f"sumIf(amt, side='short' AND b='{b}') AS short_{b}_t,"
            f"sumIf(sz,  side='long'  AND b='{b}') AS long_{b}_u,"
            f"sumIf(sz,  side='short' AND b='{b}') AS short_{b}_u"
        )
    oi_select = ",\n            ".join(_oi_cols(b) for b in ("now", "1h", "4h", "24h", "7d"))
    _multiif = (
        "multiIf(bucket = {t_now:DateTime}, 'now',\n"
        "                    bucket = {t_1h:DateTime},  '1h',\n"
        "                    bucket = {t_4h:DateTime},  '4h',\n"
        "                    bucket = {t_24h:DateTime}, '24h',\n"
        "                    bucket = {t_7d:DateTime},  '7d', '') AS b"
    )
    _bucket_in = ("bucket IN ({t_now:DateTime}, {t_1h:DateTime}, {t_4h:DateTime}, "
                  "{t_24h:DateTime}, {t_7d:DateTime})")
    if static:
        # Static set (group or cutoff): filter to the membership on every bucket
        # — no per-day join, no WITH. Same outer aggregation (incl. counts).
        with_block = ""
        inner_block = f"""
            SELECT
                token, side, wallet,
                {_multiif},
                argMaxMerge(amount_state) AS amt,
                argMaxMerge(size_state)   AS sz
            FROM tradernick.hl_position_history_1h
            WHERE {_bucket_in}
              AND {_cutoff_membership_sql(static_key, col="wallet")}
            GROUP BY token, side, wallet, bucket"""
    else:
        with_block = f"""WITH pset AS (
            SELECT day, wallet FROM {_ROLLING_SET_CACHE_TABLE}
            WHERE sel_key = {{sel_key:String}}
              AND computed_at = (SELECT max(computed_at) FROM {_ROLLING_SET_CACHE_TABLE} WHERE sel_key = {{sel_key:String}})
        )
        """
        inner_block = f"""
            SELECT p.token AS token, p.side AS side, p.wallet AS wallet,
                   p.b AS b, p.amt AS amt, p.sz AS sz
            FROM (
                SELECT
                    token, side, wallet, toDate(bucket) AS day,
                    {_multiif},
                    argMaxMerge(amount_state) AS amt,
                    argMaxMerge(size_state)   AS sz
                FROM tradernick.hl_position_history_1h
                WHERE {_bucket_in}
                GROUP BY token, side, wallet, bucket
            ) p
            INNER JOIN pset s ON s.wallet = p.wallet AND s.day = p.day"""
    oi_rows = await ch.query(
        f"""
        {with_block}SELECT
            token,
            {oi_select},
            toUInt32(uniqExactIf(wallet, side='long'  AND b='now' AND amt > 0)) AS long_count,
            toUInt32(uniqExactIf(wallet, side='short' AND b='now' AND amt > 0)) AS short_count
        FROM ({inner_block}
        )
        GROUP BY token
        """,
        parameters=params,
    )

    # HL-perp price now / 24h-ago / 7d-ago (relative to the snapshot times).
    price_rows = await ch.query(
        """
        SELECT
            token,
            argMaxIf(close, time, time <= {t_now:DateTime})  AS p_now,
            argMaxIf(close, time, time <= {t_24h:DateTime})  AS p_24h,
            argMaxIf(close, time, time <= {t_7d:DateTime})   AS p_7d
        FROM tradernick.hl_ohlcv_1m
        WHERE time >= {t_7d:DateTime} - INTERVAL 1 DAY AND time <= {t_now:DateTime}
        GROUP BY token
        """,
        parameters={"t_now": t_now, "t_24h": t_24h, "t_7d": t_7d},
    )
    price = {r[0]: (float(r[1]), float(r[2]), float(r[3])) for r in price_rows.result_rows}

    # SELECT order is long_t, short_t, long_u, short_u per snapshot — match it.
    col_order = [
        f"{side}_{b}_{u}"
        for b in ("now", "1h", "4h", "24h", "7d")
        for u in ("t", "u")
        for side in ("long", "short")
    ]
    out = []
    for r in oi_rows.result_rows:
        token = r[0]
        v = {c: float(x) for c, x in zip(col_order, r[1:])}
        p_now, p_24h, p_7d = price.get(token, (0.0, 0.0, 0.0))
        # Count columns follow the 20 OI columns (uniqExactIf at the 'now' snapshot).
        n_oi = len(col_order)
        row = {
            "token": token,
            "long_oi_token": v["long_now_t"], "long_oi_usd": v["long_now_u"],
            "short_oi_token": v["short_now_t"], "short_oi_usd": v["short_now_u"],
            "long_count": int(r[1 + n_oi]), "short_count": int(r[2 + n_oi]),
            "pct_24h": ((p_now - p_24h) / p_24h * 100.0) if p_24h > 0 else None,
            "pct_7d": ((p_now - p_7d) / p_7d * 100.0) if p_7d > 0 else None,
        }
        # Per-side OI change (now − ago) for each window, token + USD.
        for win in ("1h", "4h", "24", "7d"):
            b = "24h" if win == "24" else win
            for side in ("long", "short"):
                row[f"{side}_chg{win}_token"] = v[f"{side}_now_t"] - v[f"{side}_{b}_t"]
                row[f"{side}_chg{win}_usd"] = v[f"{side}_now_u"] - v[f"{side}_{b}_u"]
        out.append(row)
    return response.json({"tokens": out, "wallet_set": sel_key})


@bp.get("/hyperliquid/smart_wallet_top_oi")
@throttled("heavy")
async def smart_wallet_top_oi(request):
    """Top-N wallets by OI (position notional) for ONE token at ONE snapshot,
    among the widget's filtered set. Powers the chart-click / token-select dialog
    across all Smart Wallets widgets. Same selection params as the chart; `time`
    = unix seconds of the clicked bucket (default = latest); `rolling=1` for the
    Dynamic widget (per-day cohort)."""
    oi_token = request.args.get("oi_token")
    if not oi_token:
        return response.json({"error": "missing oi_token"}, status=400)
    try:
        n = max(1, min(int(request.args.get("n", "10")), 100))
    except ValueError:
        n = 10
    ch = await client()

    # Snap requested time to the hourly bucket; default to the latest bucket.
    tdt = None
    time_arg = request.args.get("time")
    if time_arg:
        try:
            tdt = datetime.fromtimestamp(int(float(time_arg)), tz=timezone.utc).replace(
                tzinfo=None, minute=0, second=0, microsecond=0)
        except (ValueError, OSError):
            tdt = None
    if tdt is None:
        r0 = await ch.query("SELECT max(bucket) FROM tradernick.hl_position_history_1h")
        if not r0.result_rows or r0.result_rows[0][0] is None:
            return response.json({"wallets": [], "positions": {}, "token": oi_token})
        tdt = r0.result_rows[0][0]

    group = request.args.get("group")
    cutoff = request.args.get("cutoff") in ("1", "true", "yes")
    rolling = request.args.get("rolling") in ("1", "true", "yes")
    try:
        if group:
            member = _cutoff_membership_sql(await _resolve_group_passing(ch, request), col="wallet")
        elif cutoff:
            member = _cutoff_membership_sql((await _resolve_cutoff_passing(ch, request))[0], col="wallet")
        elif rolling:
            rsel = _build_rolling_selection(
                request,
                since_override=(tdt - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                until_override=tdt.strftime("%Y-%m-%dT%H:%M:%SZ"))
            rk = await _resolve_rolling_passing(ch, rsel)
            member = (f"wallet IN (SELECT wallet FROM {_ROLLING_SET_CACHE_TABLE} "
                      f"WHERE sel_key = '{rk}' AND day = toDate({{t:DateTime}}) AND computed_at = "
                      f"(SELECT max(computed_at) FROM {_ROLLING_SET_CACHE_TABLE} WHERE sel_key = '{rk}'))")
        else:
            sel = _build_smart_wallet_selection(request)
            member = _cutoff_membership_sql(await _resolve_passing(ch, sel), col="wallet")
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    rows = await ch.query(
        f"""
        SELECT wallet, any(side) AS side,
               argMaxMerge(amount_state) AS amt, argMaxMerge(size_state) AS sz,
               any(dictGet('tradernick.wallet_labels', 'categories', lower(wallet))) AS categories
        FROM tradernick.hl_position_history_1h
        WHERE token = {{tok:String}} AND bucket = {{t:DateTime}}
          AND {member}
        GROUP BY wallet
        HAVING sz > 0
        ORDER BY sz DESC
        LIMIT {{n:UInt32}}
        """,
        parameters={"tok": oi_token, "t": tdt, "n": n},
    )
    wallets: list[str] = []
    positions: dict = {}
    categories: dict = {}
    for w, side, amt, sz, cats in rows.result_rows:
        wallets.append(w)
        positions[w] = {"side": side, "amount": float(amt), "size_usd": float(sz), "unrealized": 0.0}
        categories[w] = list(cats) if cats else []
    return response.json({
        "wallets": wallets, "positions": positions, "categories": categories,
        "token": oi_token, "day": tdt.strftime("%Y-%m-%d"),
    })


_BACKTRACK_LB = {
    "15m": timedelta(minutes=15), "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1), "4h": timedelta(hours=4),
    "1d": timedelta(days=1), "7d": timedelta(days=7),
}


@bp.get("/hyperliquid/position_change_wallets")
@throttled("heavy")
async def position_change_wallets(request):
    """Backtracker: top-N wallets by |position change| in ONE token over a lookback
    ending at the clicked bar. Net change = each wallet's SIGNED open position at T
    vs at T-lookback — two 15-min snapshots from the RAW hl_position_history (not the
    _15m rollup: the raw is ~30m fresher, so recent bars actually have data — the
    rollup lags 30-60m). O(wallets holding the token), independent of lookback.
    Params: token, time (unix secs of the clicked bar), lookback (15m|1h|4h|1d|7d),
    n (default 100, cap 200). Returns signed amounts (long +, short −) + the old
    position's unrealized PnL; the dialog derives change type / pct client-side."""
    token = request.args.get("token")
    if not token:
        return response.json({"error": "missing token"}, status=400)
    lb = request.args.get("lookback", "1h")
    none_mode = lb == "none"
    if none_mode:
        # 'None' → ignore the lookback and use the clicked bar's OWN window
        # [T, T+interval) instead of [T-lookback, T). Needs the chart interval.
        iv = request.args.get("interval", "15m")
        if iv not in _BACKTRACK_LB:
            return response.json({"error": f"interval must be one of {list(_BACKTRACK_LB)}"}, status=400)
    elif lb not in _BACKTRACK_LB:
        return response.json({"error": f"lookback must be one of {list(_BACKTRACK_LB)} or 'none'"}, status=400)
    try:
        n = max(1, min(int(request.args.get("n", "100")), 200))
    except ValueError:
        n = 100
    time_arg = request.args.get("time")
    if not time_arg:
        return response.json({"error": "missing time"}, status=400)
    try:
        secs = int(float(time_arg))
    except ValueError:
        return response.json({"error": "bad time"}, status=400)
    # Snap the clicked bar time DOWN to the 15-min grid.
    t0 = datetime.fromtimestamp(secs, tz=timezone.utc).replace(
        tzinfo=None, second=0, microsecond=0)
    t0 = t0.replace(minute=(t0.minute // 15) * 15)
    if none_mode:
        t_prev, t_end = t0, t0 + _BACKTRACK_LB[iv]   # the bar itself: [T, T+interval)
    else:
        t_end, t_prev = t0, t0 - _BACKTRACK_LB[lb]   # run-up to the bar: [T-lookback, T)

    ch = await client()
    # Optional group filter (group=<id>): restrict to that wallet group's members
    # — the "only selected group" toggle in the Backtracker dialog. Same membership
    # resolution as group_fill_pressure. Empty/absent → all wallets.
    mem = ""
    if request.args.get("group"):
        try:
            mem = " AND " + _cutoff_membership_sql(
                await _resolve_group_passing(ch, request), col="wallet")
        except ValueError:
            mem = ""
    # Mark price at T (last close ≤ T) to value change / notional in USD.
    price = await mark_price(ch, token, t_end)

    # Freshest position snapshot ≤ t_end. If it's BEFORE t_end, the raw hasn't
    # published the clicked bar's snapshot yet (its ~25m DeFiStream lag) — the exact
    # diff would come back empty, so reconstruct the "after" side from fills
    # (hl_fills ~2m) via positions.positions_at so the most-recent bar still works.
    te_bucket = await latest_snapshot_bucket(ch, token, t_end)
    reconstruct = te_bucket is None or te_bucket < t_end

    if not reconstruct:
        # Exact snapshot diff: UNION the two snapshots tagged e(nd)/s(tart), sum the
        # signed position per wallet. (UNION not FULL JOIN — CH fills a missing key
        # with '' not NULL.)
        rows = await ch.query(
            """
            SELECT wallet,
                   sum(if(tag = 'e', a, 0)) AS amt_new,
                   sum(if(tag = 's', a, 0)) AS amt_old,
                   sum(if(tag = 'e', u, 0)) AS usd_new,
                   sum(if(tag = 's', u, 0)) AS usd_old,
                   sum(if(tag = 's', p, 0)) AS unrealized_old,
                   any(dictGet('tradernick.wallet_labels', 'categories', lower(wallet))) AS categories
            FROM (
                SELECT wallet, 'e' AS tag,
                       argMax(amount, time) * if(side = 'long', 1, -1) AS a,
                       argMax(size,   time) * if(side = 'long', 1, -1) AS u,
                       0.0 AS p
                FROM tradernick.hl_position_history
                WHERE token = {tok:String}
                  AND time >= {te:DateTime} AND time < {te:DateTime} + INTERVAL 900 SECOND""" + mem + """
                GROUP BY wallet, side
                UNION ALL
                SELECT wallet, 's' AS tag,
                       argMax(amount, time) * if(side = 'long', 1, -1) AS a,
                       argMax(size,   time) * if(side = 'long', 1, -1) AS u,
                       argMax(unrealized_pnl, time) AS p
                FROM tradernick.hl_position_history
                WHERE token = {tok:String}
                  AND time >= {tp:DateTime} AND time < {tp:DateTime} + INTERVAL 900 SECOND""" + mem + """
                GROUP BY wallet, side
            )
            GROUP BY wallet
            HAVING abs(amt_new - amt_old) > 1e-9
            ORDER BY abs(amt_new - amt_old) DESC
            LIMIT {n:UInt32}
            """,
            parameters={"tok": token, "te": t_end, "tp": t_prev, "n": n},
        )
        base_rows = list(rows.result_rows)
    else:
        # Reconstruct: candidates = wallets that TRADED the token over [t_prev, t_end)
        # (position change equals net fills), ranked by |Δamount|. Then positions_at
        # gives each one's old (t_prev snapshot) + new (t_prev snapshot + fills).
        cand = await ch.query(
            """
            SELECT wallet,
                   sum(if(side = 'B', size, -size)) AS d_amt,
                   any(dictGet('tradernick.wallet_labels', 'categories', lower(wallet))) AS categories
            FROM tradernick.hl_fills FINAL
            WHERE token = {tok:String} AND time >= {tp:DateTime} AND time < {te:DateTime}""" + mem + """
            GROUP BY wallet
            HAVING abs(d_amt) > 1e-9
            ORDER BY abs(d_amt) DESC
            LIMIT {n:UInt32}
            """,
            parameters={"tok": token, "tp": t_prev, "te": t_end, "n": n},
        )
        cwallets = [r[0] for r in cand.result_rows]
        pos = (await positions_at(ch, token=token, at_time=t_end, base_bucket=t_prev,
                                  wallets=cwallets, price=price)) if cwallets else {}
        base_rows = []
        for (w, d_amt, cats) in cand.result_rows:
            p = pos.get(w) or {}
            ao = p.get("base_amount", 0.0)
            an = p.get("amount", ao + float(d_amt))
            base_rows.append((w, an, ao, p.get("size_usd", 0.0),
                              p.get("base_size_usd", 0.0), p.get("base_unrealized", 0.0), cats))

    wallets = [r[0] for r in base_rows]
    # Total OI per wallet at the freshest snapshot bucket (= t_end normally, the last
    # available bucket when reconstructing) — total open notional across ALL tokens.
    oi_bucket = te_bucket or t_end
    acct: dict[str, float] = {}
    if wallets:
        ar = await ch.query(
            """
            SELECT wallet, sum(v) AS av FROM (
                SELECT wallet, argMax(size, time) AS v
                FROM tradernick.hl_position_history
                WHERE time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                  AND wallet IN {ws:Array(String)}
                GROUP BY wallet, token, side
            ) GROUP BY wallet
            """,
            parameters={"b": oi_bucket, "ws": wallets},
        )
        acct = {w: float(v) for w, v in ar.result_rows}

    # Gross fills + realized PnL per wallet over [t_prev, t_end): buy/sell $ (both
    # legs of round-trips) + realized PnL = Σ closing-fill closed_pnl.
    gross: dict[str, tuple[float, float, float]] = {}
    if wallets:
        gr = await ch.query(
            """
            SELECT wallet,
                   sumIf(size * price, side = 'B') AS gbuy,
                   sumIf(size * price, side = 'A') AS gsell,
                   sum(closed_pnl)                 AS pnl
            FROM tradernick.hl_fills FINAL
            WHERE token = {tok:String} AND time >= {tp:DateTime} AND time < {te:DateTime}
              AND wallet IN {ws:Array(String)}
            GROUP BY wallet
            """,
            parameters={"tok": token, "tp": t_prev, "te": t_end, "ws": wallets},
        )
        gross = {w: (float(gb), float(gs), float(pn)) for w, gb, gs, pn in gr.result_rows}

    out = [
        {"wallet": w, "amt_old": float(ao), "amt_new": float(an),
         "usd_old": float(uo), "usd_new": float(un), "unrealized_old": float(up),
         "account_value": acct.get(w, 0.0),
         "gross_buy": gross.get(w, (0.0, 0.0, 0.0))[0], "gross_sell": gross.get(w, (0.0, 0.0, 0.0))[1],
         "realized_pnl": gross.get(w, (0.0, 0.0, 0.0))[2],
         "categories": list(cats) if cats else []}
        for (w, an, ao, un, uo, up, cats) in base_rows
    ]
    return response.json({
        "token": token, "lookback": lb, "price": price,
        "time": int(t_end.replace(tzinfo=timezone.utc).timestamp()),
        "time_prev": int(t_prev.replace(tzinfo=timezone.utc).timestamp()),
        "rows": out,
    })


# Server-side ranking column for the Net Position dialog. Keys map to the SELECT
# aliases below (amount/size_usd are positive magnitudes; change_amount is signed).
_GTP_ORDER = {
    "change": "abs(change_amount)",                                  # |Δ position| (default)
    "value":  "size_usd",                                           # position notional $
    "upnl":   "abs(unrealized_pnl)",                                # |unrealized PnL|
    "roe":    "abs(unrealized_pnl / nullif(amount * entry_px, 0))",  # |return on entry notional|
    "entry":  "entry_px",                                           # entry price
    "last_change": "last_change",                                   # most recent fill ts
}

# Dialog-side override for the CHANGE column's window start (position snapshot stays
# at the bar; only t_prev moves back). Absent → the bar's own window (t_prev as
# derived from lookback/none).
_GTP_CHANGE_LB = {
    "15m": timedelta(minutes=15), "1h": timedelta(hours=1), "4h": timedelta(hours=4),
    "12h": timedelta(hours=12), "1d": timedelta(days=1), "3d": timedelta(days=3),
    "7d": timedelta(days=7), "14d": timedelta(days=14), "30d": timedelta(days=30),
}


@bp.get("/hyperliquid/group_token_positions")
@throttled("heavy")
async def group_token_positions(request):
    """Backtracker 'Net Position' dialog: the FULL position book in ONE token for a
    wallet GROUP at the clicked bar — every group member HOLDING the token (not just
    those who changed), with the wallets-page columns (side / value / amount / entry
    / uPnL / ROE / funding) plus the position CHANGE over the bar window. Ranked
    SERVER-SIDE by `order` (the query column) so the top-N is a real cut, not a
    client re-sort. Params: token, group, time, interval, lookback (15m|…|7d|none),
    order (change|value|upnl|roe|entry), n (default 20, cap 50)."""
    token = request.args.get("token")
    group = request.args.get("group")
    if not token or not group:
        return response.json({"error": "missing token/group"}, status=400)
    order = request.args.get("order", "change")
    if order not in _GTP_ORDER:
        return response.json({"error": f"order must be one of {list(_GTP_ORDER)}"}, status=400)
    lb = request.args.get("lookback", "1h")
    none_mode = lb == "none"
    if none_mode:
        iv = request.args.get("interval", "15m")
        if iv not in _BACKTRACK_LB:
            return response.json({"error": f"interval must be one of {list(_BACKTRACK_LB)}"}, status=400)
    elif lb not in _BACKTRACK_LB:
        return response.json({"error": f"lookback must be one of {list(_BACKTRACK_LB)} or 'none'"}, status=400)
    try:
        n = max(1, min(int(request.args.get("n", "20")), 50))
    except ValueError:
        n = 20
    # Optional "last change since" filter (YYYY-MM-DD, UTC): keep only wallets whose
    # most recent fill in the token is on/after this date. 0 = no filter.
    lcs = 0
    lcs_arg = request.args.get("last_change_since")
    if lcs_arg:
        try:
            lcs = int(datetime.fromisoformat(lcs_arg).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            lcs = 0
    time_arg = request.args.get("time")
    if not time_arg:
        return response.json({"error": "missing time"}, status=400)
    try:
        secs = int(float(time_arg))
    except ValueError:
        return response.json({"error": "bad time"}, status=400)
    t0 = datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    # NOT floored to 15m: the detail is reconstructed to t_end with fills, so t_end can
    # be an exact time (leaderboard → now). Bar clicks already pass grid-aligned times.
    if none_mode:
        t_prev, t_end = t0, t0 + _BACKTRACK_LB[iv]   # the bar itself: [T, T+interval)
    else:
        t_end, t_prev = t0, t0 - _BACKTRACK_LB[lb]   # run-up to the bar: [T-lookback, T)
    # Dialog override: measure the Change over a longer window ENDING at the bar
    # (t_end fixed, only the start moves back). '-'/absent → keep the bar's window.
    change_lb = request.args.get("change_lookback")
    if change_lb in _GTP_CHANGE_LB:
        t_prev = t_end - _GTP_CHANGE_LB[change_lb]

    ch = await client()
    try:
        member = _cutoff_membership_sql(await _resolve_group_passing(ch, request), col="wallet")
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    price = await mark_price(ch, token, t_end)
    # Position detail from the freshest published snapshot ≤ t_end, then CARRIED
    # FORWARD to t_end with fills (recon_amt) so the shown side/amount is current
    # even when the snapshot lags and the wallet flipped since (entry/uPnL/funding
    # can't be reconstructed → nulled on a flip). CHANGE = fills over [t_prev, t_end).
    te_bucket = await latest_snapshot_bucket(ch, token, t_end)
    out = []
    if te_bucket is not None:
        rows = await ch.query(
            """
            SELECT wallet, side, amount, size_usd, entry_px, unrealized_pnl, funding,
                   change_amount, last_change, recon_amt, categories, change_usd
            FROM (
                SELECT b.wallet AS wallet, d.side AS side, d.amt AS amount,
                       d.sz AS size_usd, d.entry AS entry_px, d.upnl AS unrealized_pnl,
                       d.fund AS funding, ifNull(c.d_amt, 0) AS change_amount,
                       ifNull(l.lc, 0) AS last_change, ifNull(rc.ra, 0) AS recon_amt,
                       dictGet('tradernick.wallet_labels', 'categories', lower(b.wallet)) AS categories,
                       ifNull(c.d_usd, 0) AS change_usd
                FROM (
                    -- everyone HOLDING at the snapshot OR who TRADED the token in the
                    -- change window — the latter may be flat at t_end (closed-out).
                    SELECT DISTINCT wallet FROM (
                        SELECT wallet FROM tradernick.hl_position_history
                        WHERE token = {tok:String}
                          AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                          AND """ + member + """
                        UNION DISTINCT
                        SELECT wallet FROM tradernick.hl_fills
                        WHERE token = {tok:String} AND time >= {tp:DateTime} AND time < {te:DateTime}
                          AND """ + member + """
                    )
                ) b
                LEFT JOIN (
                    SELECT wallet,
                           argMax(side, time)          AS side,
                           argMax(amount, time)        AS amt,
                           argMax(size, time)          AS sz,
                           argMax(avg_entry, time)     AS entry,
                           argMax(unrealized_pnl, time) AS upnl,
                           argMax(funding, time)       AS fund
                    FROM tradernick.hl_position_history
                    WHERE token = {tok:String}
                      AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                      AND """ + member + """
                    GROUP BY wallet
                ) d ON b.wallet = d.wallet
                LEFT JOIN (
                    -- change: net tokens (d_amt) AND net signed $ at FILL prices (d_usd),
                    -- the same basis as the leaderboard's Flow(grp) so they reconcile.
                    SELECT wallet, sum(if(side = 'B', size, -size)) AS d_amt,
                           sum(if(side = 'B', size * price, -size * price)) AS d_usd
                    FROM tradernick.hl_fills FINAL
                    WHERE token = {tok:String} AND time >= {tp:DateTime} AND time < {te:DateTime}
                      AND """ + member + """
                    GROUP BY wallet
                ) c ON b.wallet = c.wallet
                LEFT JOIN (
                    -- most recent fill for this token per wallet, as of the bar
                    -- (max is dedup-safe → no FINAL). 0 when the wallet never traded it.
                    SELECT wallet, toUnixTimestamp(max(time)) AS lc
                    FROM tradernick.hl_fills
                    WHERE token = {tok:String} AND time < {te:DateTime}
                      AND """ + member + """
                    GROUP BY wallet
                ) l ON b.wallet = l.wallet
                LEFT JOIN (
                    -- net signed fills since the snapshot → carry the position forward
                    -- to t_end (fixes a stale side when the wallet flipped since).
                    SELECT wallet, sum(if(side = 'B', size, -size)) AS ra
                    FROM tradernick.hl_fills FINAL
                    WHERE token = {tok:String} AND time > {b:DateTime} AND time < {te:DateTime}
                      AND """ + member + """
                    GROUP BY wallet
                ) rc ON b.wallet = rc.wallet
            )
            WHERE last_change >= {lcs:UInt32}
            ORDER BY """ + _GTP_ORDER[order] + """ DESC
            LIMIT {n:UInt32}
            """,
            parameters={"tok": token, "b": te_bucket, "tp": t_prev, "te": t_end,
                        "n": n, "lcs": lcs},
        )
        for (w, side, amt, entry, fund, dch, lc, ra, cats, dusd) in (
                (r[0], r[1], float(r[2]), float(r[4]), float(r[6]), float(r[7]),
                 int(r[8]), float(r[9]), r[10], float(r[11])) for r in rows.result_rows):
            # Carry the snapshot forward: signed position at t_end = snapshot + fills.
            snap_signed = amt * (1.0 if side == "long" else -1.0)
            now_signed = snap_signed + ra
            # closed-out = traded the token in the window but flat at t_end (no position).
            closed = abs(now_signed) < 1e-9
            side_now = "flat" if closed else ("long" if now_signed >= 0 else "short")
            amt_now = 0.0 if closed else abs(now_signed)
            # entry/uPnL/funding are for the snapshot position; keep them (revalued at
            # the current mark) only when the side didn't flip and there WAS a position.
            flipped = abs(snap_signed) > 1e-9 and (now_signed > 0) != (snap_signed > 0)
            if closed or flipped or abs(snap_signed) < 1e-9:
                entry_out = upnl_out = roe_out = fund_out = None
            else:
                entry_out = entry or None
                notional = amt_now * entry
                upnl_out = (now_signed * (price - entry)) if entry else None
                roe_out = (upnl_out / notional) if (upnl_out is not None and notional) else None
                fund_out = fund
            out.append({
                "wallet": w, "side": side_now, "closed": closed,
                "amount": amt_now, "size_usd": amt_now * price,   # positive magnitudes + side
                "entry_px": entry_out, "unrealized_pnl": upnl_out,
                "roe": roe_out, "funding": fund_out,
                "change_amount": float(dch), "change_usd": dusd,
                "last_change": int(lc),
                "categories": list(cats) if cats else [],
            })
    return response.json({
        "token": token, "order": order, "price": price,
        "time": int(t_end.replace(tzinfo=timezone.utc).timestamp()),
        "time_prev": int(t_prev.replace(tzinfo=timezone.utc).timestamp()),
        "rows": out,
    })


_BL_LB = {"15m": 900, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400, "7d": 604800}
# Position-staleness lookback: a group position counts toward the Positions column
# only if the wallet had a fill in that token within this window (filters out stale,
# long-untouched positions).
_BL_STALE = {"4h": 14400, "1d": 86400, "3d": 259200, "7d": 604800,
             "14d": 1209600, "30d": 2592000}


@bp.get("/hyperliquid/backtracker_leaderboard")
@throttled("heavy")
async def backtracker_leaderboard(request):
    """Backtracker Leaderboard tableview: one row per HL-perp token with activity over
    a lookback — price Δ%, net flow (group + overall $), net-signed-OI Δ%, long/short
    Δ%, volume Δ% (vs the preceding equal window), and spot volume-delta ($ + %). OI
    columns end at the freshest snapshot (as_of=recent) or reconstructed to now from
    fills (as_of=now). Params: lookback (1h|4h|12h|1d|7d), group (optional), as_of."""
    lb = request.args.get("lookback", "1h")
    if lb not in _BL_LB:
        return response.json({"error": f"lookback must be one of {list(_BL_LB)}"}, status=400)
    as_of = request.args.get("as_of", "now")
    if as_of not in ("now", "recent"):
        return response.json({"error": "as_of must be now|recent"}, status=400)
    stale = request.args.get("pos_staleness", "3d")
    stale_sec = _BL_STALE.get(stale, _BL_STALE["3d"])
    lb_sec = _BL_LB[lb]
    ch = await client()
    member = None
    if request.args.get("group"):
        try:
            member = _cutoff_membership_sql(await _resolve_group_passing(ch, request), col="wallet")
        except ValueError:
            member = None

    # Anchor the window END on the freshest data we actually have, not now() — so a
    # short lookback still shows data when a feed lags (e.g. DeFiStream behind on HL
    # ohlcv/fills → a 1h window ending at now() would be entirely in the gap = all 0).
    # HL columns share end_ts = min(latest ohlcv, latest fills); spot uses its own.
    mx = await ch.query(
        """
        SELECT (SELECT max(time) FROM tradernick.hl_ohlcv_1m WHERE time <= now())         AS oh,
               (SELECT max(time) FROM tradernick.hl_fills WHERE time <= now())            AS fi,
               (SELECT max(time) FROM tradernick.binance_spot_ohlcv_1m WHERE time <= now()) AS sp
        """
    )
    oh, fi, sp = (mx.result_rows[0] if mx.result_rows else (None, None, None))
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    hl_ends = [t for t in (oh, fi) if t is not None]
    end_ts = min(hl_ends) if hl_ends else now_naive
    spot_end = sp if sp is not None else now_naive
    # Flow (grp) is fills-based (fresh, ~2m) — for as_of=now anchor its window at now()
    # rather than the ohlcv-lagged end_ts, else a short "as of now" flow sits ~ohlcv_lag
    # behind and its window doesn't even overlap the positions dialog's (which uses now).
    flow_end = now_naive if as_of == "now" else end_ts

    # ── 1. Price Δ%, Volume Δ% (window vs preceding equal window), overall net flow
    # (= market taker CVD $ over the lookback; hl_fills is balanced market-wide so a
    # Σ(B−A) net would be structurally 0 — the taker split is the real directional flow).
    pv = await ch.query(
        """
        SELECT token,
               argMax(close, time)                                                     AS price_now,
               argMinIf(close, time, time > {end:DateTime} - INTERVAL {s:UInt32} SECOND) AS price_start,
               sumIf(volume * close, time > {end:DateTime} - INTERVAL {s:UInt32} SECOND) AS vol_lb,
               sumIf(volume * close, time <= {end:DateTime} - INTERVAL {s:UInt32} SECOND) AS vol_prev,
               sumIf((buyer_taker_volume - seller_taker_volume) * close,
                     time > {end:DateTime} - INTERVAL {s:UInt32} SECOND)                 AS nf_overall
        FROM tradernick.hl_ohlcv_1m FINAL
        WHERE token != '' AND time > {end:DateTime} - INTERVAL {s2:UInt32} SECOND AND time <= {end:DateTime}
        GROUP BY token
        """,
        parameters={"end": end_ts, "s": lb_sec, "s2": lb_sec * 2},
    )
    agg: dict[str, dict] = {}
    for tok, pn, ps, vl, vp, nfo in pv.result_rows:
        pn, ps, vl, vp = float(pn), float(ps), float(vl), float(vp)
        agg[tok] = {
            "token": tok, "price": pn,
            "price_pct": ((pn / ps - 1) * 100) if ps else None,
            "price_vs_btc_pct": None,
            "vol_pct": ((vl / vp - 1) * 100) if vp else None,
            "net_flow_overall": float(nfo), "net_flow_group": None,
            "net_oi_pct": None, "net_oi_now_pct": None, "long_pct": None, "short_pct": None,
            "flow_overall_pct": None, "flow_group_pct": None,
            "spot_vd": None, "spot_vd_pct": None,
            "pos_n_long": None, "pos_n_short": None, "pos_oi_long": None, "pos_oi_short": None,
            "pos_d_n_long": None, "pos_d_n_short": None, "pos_d_oi_long": None, "pos_d_oi_short": None,
        }

    # Relative price performance vs BTC: the token/BTC ratio change over the lookback
    # (= (1+token_ret)/(1+btc_ret) − 1). + = appreciated vs BTC; BTC itself = 0.
    btc_pct = agg.get("BTC", {}).get("price_pct")
    if btc_pct is not None and (1 + btc_pct / 100) != 0:
        btc_ratio = 1 + btc_pct / 100
        for r in agg.values():
            tp = r.get("price_pct")
            if tp is not None:
                r["price_vs_btc_pct"] = ((1 + tp / 100) / btc_ratio - 1) * 100

    # ── 2. Group net flow $ (the group's net position flow from fills) — only when a
    # group is selected (the market-wide overall comes from taker CVD above). ──
    if member:
        nf = await ch.query(
            """
            SELECT token, sumIf(if(side='B', size*price, -size*price), """ + member + """) AS nf_group
            FROM tradernick.hl_fills FINAL
            WHERE token != '' AND time > {end:DateTime} - INTERVAL {s:UInt32} SECOND AND time <= {end:DateTime}
            GROUP BY token
            """,
            parameters={"end": flow_end, "s": lb_sec},
        )
        for tok, nfg in nf.result_rows:
            if tok in agg:
                agg[tok]["net_flow_group"] = float(nfg)

    # ── 3. OI: long/short/net at start vs end snapshots (+ reconstruct end for now) ──
    er = await ch.query("SELECT max(time) FROM tradernick.hl_position_history WHERE time <= now()")
    end_bucket = er.result_rows[0][0] if (er.result_rows and er.result_rows[0][0]) else None
    if end_bucket is not None:
        start_bucket = end_bucket - timedelta(seconds=lb_sec)
        # start snapshot (signed magnitudes per token/side) — same for both modes
        s_oi = await ch.query(
            """
            SELECT token, side, sum(v) AS oi FROM (
                SELECT token, side, wallet, argMax(size, time) AS v
                FROM tradernick.hl_position_history
                WHERE token != '' AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                GROUP BY token, side, wallet
            ) GROUP BY token, side
            """,
            parameters={"b": start_bucket},
        )
        start_oi: dict[str, dict] = {}
        for tok, side, oi in s_oi.result_rows:
            start_oi.setdefault(tok, {}).update({side: float(oi)})

        if as_of == "recent":
            e_rows = await ch.query(
                """
                SELECT token, side, sum(v) AS oi FROM (
                    SELECT token, side, wallet, argMax(size, time) AS v
                    FROM tradernick.hl_position_history
                    WHERE token != '' AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                    GROUP BY token, side, wallet
                ) GROUP BY token, side
                """,
                parameters={"b": end_bucket},
            )
            end_oi: dict[str, dict] = {}
            for tok, side, oi in e_rows.result_rows:
                end_oi.setdefault(tok, {}).update({side: float(oi)})
        else:
            # Reconstruct each wallet's now-position (snapshot@end_bucket + fills since),
            # classify long/short by the signed amount, value at the token's mark price.
            e_rows = await ch.query(
                """
                SELECT token,
                       sumIf(amt, amt > 0)  AS long_amt,
                       -sumIf(amt, amt < 0) AS short_amt
                FROM (
                    SELECT token, wallet, sum(v) AS amt FROM (
                        SELECT token, wallet, argMax(amount, time) * if(side='long',1,-1) AS v
                        FROM tradernick.hl_position_history
                        WHERE token != '' AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                        GROUP BY token, wallet, side
                        UNION ALL
                        SELECT token, wallet, sum(if(side='B', size, -size)) AS v
                        FROM tradernick.hl_fills FINAL
                        WHERE token != '' AND time >= {b:DateTime} AND time < now()
                        GROUP BY token, wallet
                    ) GROUP BY token, wallet
                ) GROUP BY token
                """,
                parameters={"b": end_bucket},
            )
            end_oi = {}
            for tok, la, sa in e_rows.result_rows:
                price = agg.get(tok, {}).get("price", 0.0) or 0.0
                end_oi[tok] = {"long": float(la) * price, "short": float(sa) * price}

        for tok in set(start_oi) | set(end_oi):
            r = agg.get(tok)
            if not r:
                continue
            ls, ss = start_oi.get(tok, {}).get("long", 0.0), start_oi.get(tok, {}).get("short", 0.0)
            le, se = end_oi.get(tok, {}).get("long", 0.0), end_oi.get(tok, {}).get("short", 0.0)
            total_end = le + se
            r["net_oi_pct"] = (((le - se) - (ls - ss)) / total_end * 100) if total_end else None
            r["net_oi_now_pct"] = ((le - se) / total_end * 100) if total_end else None   # current lean
            r["long_pct"] = ((le / ls - 1) * 100) if ls else None
            r["short_pct"] = ((se / ss - 1) * 100) if ss else None
            # Flow normalised by total OI at end — for cross-token comparison.
            if total_end:
                r["flow_overall_pct"] = r["net_flow_overall"] / total_end * 100
                if r["net_flow_group"] is not None:
                    r["flow_group_pct"] = r["net_flow_group"] / total_end * 100

        # ── Group Positions column + delta: per token, the group's long/short
        # positions at a snapshot, filtered to wallets with a fill in that token within
        # pos_staleness of the snapshot (drops stale positions). Computed at the end
        # snapshot AND at T-lookback (start_bucket) → the delta is end − start.
        if member:
            async def _positions(snap_bucket):
                pr = await ch.query(
                    """
                    SELECT token,
                           countIf(signed > 0) AS n_long,
                           countIf(signed < 0) AS n_short,
                           sumIf(oi, signed > 0) AS oi_long,
                           sumIf(oi, signed < 0) AS oi_short
                    FROM (
                        SELECT token, wallet, sum(sa) AS signed, sum(sz) AS oi FROM (
                            SELECT token, wallet,
                                   argMax(amount, time) * if(side = 'long', 1, -1) AS sa,
                                   argMax(size, time) AS sz
                            FROM tradernick.hl_position_history
                            WHERE token != '' AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND
                              AND """ + member + """
                            GROUP BY token, wallet, side
                        ) GROUP BY token, wallet
                    ) p
                    INNER JOIN (
                        SELECT DISTINCT token, wallet FROM tradernick.hl_fills
                        WHERE time > {b:DateTime} - INTERVAL {st:UInt32} SECOND AND time <= {b:DateTime}
                          AND """ + member + """
                    ) f USING (token, wallet)
                    WHERE abs(signed) > 1e-9
                    GROUP BY token
                    """,
                    parameters={"b": snap_bucket, "st": stale_sec},
                )
                return {tok: (int(nl), int(ns), float(ol), float(os_))
                        for tok, nl, ns, ol, os_ in pr.result_rows}

            end_pos = await _positions(end_bucket)
            start_pos = await _positions(start_bucket)
            for tok in set(end_pos) | set(start_pos):
                r = agg.get(tok)
                if not r:
                    continue
                e = end_pos.get(tok)
                s = start_pos.get(tok, (0, 0, 0.0, 0.0))
                if e is not None:
                    r["pos_n_long"], r["pos_n_short"], r["pos_oi_long"], r["pos_oi_short"] = e
                ee = e if e is not None else (0, 0, 0.0, 0.0)
                r["pos_d_n_long"], r["pos_d_n_short"] = ee[0] - s[0], ee[1] - s[1]
                r["pos_d_oi_long"], r["pos_d_oi_short"] = ee[2] - s[2], ee[3] - s[3]

    # ── 4. Spot volume-delta $ + % (Binance spot; null for HL tokens without spot) ──
    sv = await ch.query(
        """
        SELECT token,
               sum((buyer_taker_volume - seller_taker_volume) * close) AS vd,
               sum((buyer_taker_volume + seller_taker_volume) * close) AS vol
        FROM tradernick.binance_spot_ohlcv_1m FINAL
        WHERE token != '' AND time > {end:DateTime} - INTERVAL {s:UInt32} SECOND AND time <= {end:DateTime}
        GROUP BY token
        """,
        parameters={"end": spot_end, "s": lb_sec},
    )
    for tok, vd, vol in sv.result_rows:
        r = agg.get(tok)
        if not r:
            continue
        r["spot_vd"] = float(vd)
        r["spot_vd_pct"] = (float(vd) / float(vol) * 100) if float(vol) else None

    return response.json({"lookback": lb, "as_of": as_of, "rows": list(agg.values())})


@bp.get("/hyperliquid/early_movers")
@throttled("heavy")
async def early_movers(request):
    """Early Movers: detect sharp price 'moves' in one token's bars, then rank wallets
    by how well they predicted them (opened the right position at/just-before each move).

    Move detection: per tf-bar (open O), scanning L=1..max_len bars, long_len = min L with
    (maxHigh-O)/O >= long_thr; short_len = min L with (O-minLow)/O >= short_thr. Shorter
    length wins; equal-both → skip. Reaction per mode over [T-lead·tf, T+tf) (or the
    position state at T): flow = net signed fill $; open_flip = net Open/Flip $ (hl_fills
    `dir`); position_state = signed position $ at T's snapshot. Cols: Long/Short as
    correct/incorrect/missed (missed = total_dir - correct - incorrect). Params: token,
    interval, since, until, long_thr, short_thr, max_len, lead, mode, min_size, n,
    moves_only."""
    token = request.args.get("token")
    if not token:
        return response.json({"error": "missing token"}, status=400)
    interval = request.args.get("interval", "1h")
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"interval must be one of {list(INTERVAL_SECONDS)}"}, status=400)
    sec = INTERVAL_SECONDS[interval]
    since = request.args.get("since")
    until = request.args.get("until")
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt, until_dt = _parse_iso(since), _parse_iso(until)

    def _f(name, default):
        try:
            return float(request.args.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    long_thr = _f("long_thr", 5) / 100.0
    short_thr = _f("short_thr", 5) / 100.0
    min_size = _f("min_size", 0)
    min_avg_size = _f("min_avg_size", 0)  # $ floor on a wallet's avg identifying size
    # Per-wallet inclusion floors (server-side HAVING): correct-long/short as a count
    # AND as a % of all long/short moves.
    min_cl = _f("min_correct_long", 0)
    min_cs = _f("min_correct_short", 0)
    min_cl_pct = _f("min_correct_long_pct", 0)
    min_cs_pct = _f("min_correct_short_pct", 0)
    # Realized-PnL floor ($). Absent → -1e30 (off); PnL can be negative so 0 is a real value.
    min_pnl = _f("min_realized_pnl", -1e30)
    try:
        max_len = max(1, min(int(request.args.get("max_len", "3")), 20))
    except ValueError:
        max_len = 3
    try:
        lead = max(0, min(int(request.args.get("lead", "1")), 20))
    except ValueError:
        lead = 1
    try:
        n = max(1, min(int(request.args.get("n", "50")), 500))
    except ValueError:
        n = 50
    mode = request.args.get("mode", "flow")
    if mode not in ("flow", "open_flip", "position_state"):
        return response.json({"error": "mode must be flow|open_flip|position_state"}, status=400)
    moves_only = request.args.get("moves_only") in ("1", "true", "yes")
    # Skip Intra-bar: exclude the trigger bar itself → require the action in the lead
    # bars strictly BEFORE the move. Needs lead ≥ 1 (else the window is empty).
    skip_intra = request.args.get("skip_intra") in ("1", "true", "yes") and lead >= 1

    ch = await client()

    # ── 1. tf-bars → moves (detection in Python; cheap) ──
    br = await ch.query(
        """
        SELECT toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS bkt,
               argMin(open, time) AS o, max(high) AS h, min(low) AS l, argMax(close, time) AS c
        FROM tradernick.hl_ohlcv_1m FINAL
        WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
        GROUP BY bkt ORDER BY bkt
        """,
        parameters={"sec": sec, "tok": token, "s": since_dt, "u": until_dt},
    )
    bars = [(int(bkt), float(o), float(h), float(l)) for bkt, o, h, l, c in br.result_rows]
    moves = []  # (trigger_unix, dir, length)
    for i, (bkt, o, _h, _l) in enumerate(bars):
        if o <= 0:
            continue
        long_len = short_len = None
        run_hi, run_lo = -1e30, 1e30
        for L in range(1, max_len + 1):
            j = i + L - 1
            if j >= len(bars):
                break
            run_hi, run_lo = max(run_hi, bars[j][2]), min(run_lo, bars[j][3])
            if long_len is None and (run_hi - o) / o >= long_thr:
                long_len = L
            if short_len is None and (o - run_lo) / o >= short_thr:
                short_len = L
            if long_len is not None and short_len is not None:
                break
        if long_len is not None and short_len is not None:
            if long_len < short_len:
                moves.append((bkt, "long", long_len))
            elif short_len < long_len:
                moves.append((bkt, "short", short_len))
            # equal → skip (ambiguous)
        elif long_len is not None:
            moves.append((bkt, "long", long_len))
        elif short_len is not None:
            moves.append((bkt, "short", short_len))

    moves_out = [{"time": t, "dir": d, "len": L} for t, d, L in moves]
    total_long = sum(1 for _, d, _ in moves if d == "long")
    total_short = sum(1 for _, d, _ in moves if d == "short")
    base = {"token": token, "interval": interval, "total_long": total_long,
            "total_short": total_short, "total_bars": len(bars), "moves": moves_out}
    if moves_only or not moves:
        return response.json({**base, "rows": []})

    # ── 2. move → (window/snapshot) bucket table for the equi-join ──
    wbkts, mids, mdirs = [], [], []
    if mode == "position_state":
        for j, (t, d, _) in enumerate(moves):
            wbkts.append((t // 900) * 900)   # position state at T's 15-min snapshot
            mids.append(j)
            mdirs.append(d)
    else:
        # window buckets {T-lead·tf … T}; skip_intra drops the trigger bucket (i=0).
        for j, (t, d, _) in enumerate(moves):
            for i in range(1 if skip_intra else 0, lead + 1):
                wbkts.append(t - i * sec)
                mids.append(j)
                mdirs.append(d)

    # ── 3. per-(wallet,bucket) signal (wb) → join to moves → classify → aggregate ──
    if mode == "flow":
        wb_sql = """
            SELECT wallet, toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS bkt,
                   sum(if(side = 'B', size * price, -size * price)) AS val
            FROM tradernick.hl_fills FINAL
            WHERE token = {tok:String} AND time >= {s2:DateTime} AND time < {u:DateTime}
            GROUP BY wallet, bkt """
        wb_params = {"sec": sec, "tok": token, "s2": since_dt - timedelta(seconds=lead * sec), "u": until_dt}
    elif mode == "open_flip":
        wb_sql = """
            SELECT wallet, toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS bkt,
                   sum(multiIf(dir = 'Open Long', size*price, dir = 'Short > Long', size*price,
                               dir = 'Open Short', -size*price, dir = 'Long > Short', -size*price, 0)) AS val
            FROM tradernick.hl_fills FINAL
            WHERE token = {tok:String} AND time >= {s2:DateTime} AND time < {u:DateTime}
            GROUP BY wallet, bkt """
        wb_params = {"sec": sec, "tok": token, "s2": since_dt - timedelta(seconds=lead * sec), "u": until_dt}
    else:  # position_state — signed position $ at the move snapshot buckets
        wb_sql = """
            SELECT wallet, bkt, sum(v) AS val FROM (
                SELECT wallet, toUnixTimestamp(time) AS bkt, side,
                       argMax(size, time) * if(side = 'long', 1, -1) AS v
                FROM tradernick.hl_position_history
                WHERE token = {tok:String} AND toUnixTimestamp(time) IN {snaps:Array(UInt32)}
                GROUP BY wallet, time, side
            ) GROUP BY wallet, bkt """
        wb_params = {"tok": token, "snaps": sorted(set(wbkts))}

    rows = await ch.query(
        "SELECT s.wallet AS wallet, cl, il, cs, ish, avg_size, cats,"
        " coalesce(pnl.realized_pnl, 0) AS realized_pnl"
        " FROM ("
        "   SELECT wallet,"
        "     countIf(mdir = 'long'  AND react = 'long')  AS cl,"
        "     countIf(mdir = 'long'  AND react = 'short') AS il,"
        "     countIf(mdir = 'short' AND react = 'short') AS cs,"
        "     countIf(mdir = 'short' AND react = 'long')  AS ish,"
        "     avg(av) AS avg_size,"
        "     any(dictGet('tradernick.wallet_labels', 'categories', lower(wallet))) AS cats"
        "   FROM ("
        "     SELECT wallet, mid, mdir, abs(v) AS av,"
        "            multiIf(v >= {ms:Float64}, 'long', v <= -{ms:Float64}, 'short', 'none') AS react"
        "     FROM ("
        "       SELECT wb.wallet AS wallet, m.mid AS mid, m.mdir AS mdir, sum(wb.val) AS v"
        "       FROM (" + wb_sql + ") wb"
        "       INNER JOIN ("
        "         SELECT t.1 AS wbkt, t.2 AS mid, t.3 AS mdir"
        "         FROM (SELECT arrayJoin(arrayZip({wbkts:Array(UInt32)}, {mids:Array(UInt32)}, {mdirs:Array(String)})) AS t)"
        "       ) m ON wb.bkt = m.wbkt"
        "       GROUP BY wallet, mid, mdir"
        "     )"
        "     WHERE react != 'none'"
        "   ) GROUP BY wallet"
        " ) s"
        # realized PnL over ALL of the token's fills in the range (per wallet).
        " LEFT JOIN ("
        "   SELECT wallet, sum(closed_pnl) AS realized_pnl FROM tradernick.hl_fills FINAL"
        "   WHERE token = {tok_pnl:String} AND time >= {s_pnl:DateTime} AND time < {u_pnl:DateTime}"
        "   GROUP BY wallet"
        " ) pnl ON s.wallet = pnl.wallet"
        # count floors + accuracy floors (% = correct / (correct + incorrect), of the
        # moves the wallet reacted to; pct <= 0 disables) + size + realized-PnL floors.
        " WHERE cl >= {min_cl:Float64} AND cs >= {min_cs:Float64}"
        "   AND ({clp:Float64} <= 0 OR (cl + il > 0 AND 100 * cl >= {clp:Float64} * (cl + il)))"
        "   AND ({csp:Float64} <= 0 OR (cs + ish > 0 AND 100 * cs >= {csp:Float64} * (cs + ish)))"
        "   AND avg_size >= {min_avg:Float64}"
        "   AND coalesce(pnl.realized_pnl, 0) >= {min_pnl:Float64}"
        " ORDER BY (cl + cs) DESC"
        " LIMIT {n:UInt32}",
        parameters={**wb_params, "ms": min_size, "min_avg": min_avg_size,
                    "min_cl": min_cl, "min_cs": min_cs, "clp": min_cl_pct, "csp": min_cs_pct,
                    "min_pnl": min_pnl, "tok_pnl": token, "s_pnl": since_dt, "u_pnl": until_dt,
                    "wbkts": wbkts, "mids": mids, "mdirs": mdirs, "n": n},
    )
    out = []
    for w, cl, il, cs, ish, avg_size, cats, realized_pnl in rows.result_rows:
        cl, il, cs, ish = int(cl), int(il), int(cs), int(ish)
        out.append({
            "wallet": w,
            "correct_long": cl, "incorrect_long": il, "missed_long": max(0, total_long - cl - il),
            "correct_short": cs, "incorrect_short": ish, "missed_short": max(0, total_short - cs - ish),
            "avg_size": float(avg_size or 0),
            "realized_pnl": float(realized_pnl or 0),
            "categories": list(cats) if cats else [],
        })
    return response.json({**base, "rows": out})


_TP_LB = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
# fill type from dir + signed start_position + size; end = sp + if(B,+size,-size).
_TP_TYPE_SQL = """multiIf(
    dir = 'Open Long'  AND start_position = 0, 'open_long',
    dir = 'Open Long', 'inc_long',
    dir = 'Open Short' AND start_position = 0, 'open_short',
    dir = 'Open Short', 'inc_short',
    dir = 'Close Long'  AND abs(start_position + if(side='B', size, -size)) < 1e-6 * abs(start_position), 'close_long',
    dir = 'Close Long', 'dec_long',
    dir = 'Close Short' AND abs(start_position + if(side='B', size, -size)) < 1e-6 * abs(start_position), 'close_short',
    dir = 'Close Short', 'dec_short',
    dir = 'Long > Short', 'flip_ls',
    dir = 'Short > Long', 'flip_sl',
    'other')"""
_TP_LONG_TYPES = ["open_long", "inc_long", "dec_long", "close_long", "flip_sl"]
_TP_SHORT_TYPES = ["open_short", "inc_short", "dec_short", "close_short", "flip_ls"]
_TP_TYPE_KEYS = ["open_long", "inc_long", "dec_long", "close_long",
                 "open_short", "inc_short", "dec_short", "close_short", "flip_ls", "flip_sl"]


@bp.get("/hyperliquid/trading_pit")
@throttled("heavy")
async def trading_pit(request):
    """Trading Pit: a wallet GROUP's HL-perp fills over a short window, classified by
    action type (opened/increased/decreased/closed a long/short; flips) — hl_fills only.

    Modes: normal (latest classified fills), aggregate (per wallet+token: opens summed,
    inc/dec netted by side → Increase/Decrease, closes summed, flips), overview (per token:
    the 8 categories + flips as $ and count). flip_mode=split folds each flip into a
    close-old + open-new pair (split by start_position). Filters (server-side): min_size $,
    side long|short, type (one category), token (one of the selected). Params: tokens
    (comma list, required), group, lookback (5m|15m|30m|1h|4h), mode, flip_mode, min_size,
    side, type, token, n."""
    tokens = [t.strip().upper() for t in (request.args.get("tokens", "")).split(",") if t.strip()]
    if not tokens:
        return response.json({"error": "missing tokens (comma list)"}, status=400)
    lb = request.args.get("lookback", "5m")
    if lb not in _TP_LB:
        return response.json({"error": f"lookback must be one of {list(_TP_LB)}"}, status=400)
    mode = request.args.get("mode", "normal")
    if mode not in ("normal", "aggregate", "overview"):
        return response.json({"error": "mode must be normal|aggregate|overview"}, status=400)
    flip_split = request.args.get("flip_mode") == "split"

    def _f(name, default):
        try:
            return float(request.args.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    min_size = _f("min_size", 0)
    side = request.args.get("side") or None       # long | short | None
    type_filter = request.args.get("type") or None  # one classified type or None
    token_one = (request.args.get("token") or "").strip().upper() or None
    try:
        n = max(1, min(int(request.args.get("n", "500")), 2000))
    except ValueError:
        n = 500

    ch = await client()
    member = None
    if request.args.get("group"):
        try:
            member = _cutoff_membership_sql(await _resolve_group_passing(ch, request), col="wallet")
        except ValueError:
            member = None

    # ── classified base: raw fills → type; flip_mode=split expands each flip into a
    # close-old + open-new pair via arrayJoin (else one row). Then filters on `type`. ──
    where = ["token IN {tokens:Array(String)}",
             "time >= now() - INTERVAL {lb:UInt32} SECOND", "time < now()"]
    params = {"tokens": tokens, "lb": _TP_LB[lb], "split": 1 if flip_split else 0}
    if member:
        where.append(member)
    if min_size > 0:
        where.append("size * price >= {min_size:Float64}")
        params["min_size"] = min_size
    if token_one:
        where.append("token = {token_one:String}")
        params["token_one"] = token_one
    outer = ["type != 'other'"]
    if side == "long":
        outer.append("type IN {long_types:Array(String)}")
        params["long_types"] = _TP_LONG_TYPES
    elif side == "short":
        outer.append("type IN {short_types:Array(String)}")
        params["short_types"] = _TP_SHORT_TYPES

    base = (
        "SELECT wallet, token, time, price, side, closed_pnl, p.1 AS type, p.2 AS value FROM ("
        "  SELECT wallet, token, time, price, side, closed_pnl, "
        "         " + _TP_TYPE_SQL + " AS t0, "
        "         multiIf("
        "           {split:UInt8} = 1 AND " + _TP_TYPE_SQL + " = 'flip_ls',"
        "             [('close_long', abs(start_position)*price), ('open_short', (size-abs(start_position))*price)],"
        "           {split:UInt8} = 1 AND " + _TP_TYPE_SQL + " = 'flip_sl',"
        "             [('close_short', abs(start_position)*price), ('open_long', (size-abs(start_position))*price)],"
        "           [(" + _TP_TYPE_SQL + ", size*price)]) AS parts"
        "  FROM tradernick.hl_fills FINAL WHERE " + " AND ".join(where) +
        ") ARRAY JOIN parts AS p WHERE " + " AND ".join(outer)
    )

    if mode == "normal":
        if type_filter:
            base += " AND type = {tf:String}"
            params["tf"] = type_filter
        r = await ch.query(
            "SELECT toUnixTimestamp(time) AS ts, wallet, token, type, side, price, value, closed_pnl,"
            " dictGet('tradernick.wallet_labels', 'categories', lower(wallet)) AS cats"
            " FROM (" + base + ") ORDER BY time DESC LIMIT {n:UInt32}",
            parameters={**params, "n": n},
        )
        rows = [{
            "time": int(ts), "wallet": w, "token": tok, "type": ty, "side": sd,
            "price": float(p), "value": float(v), "closed_pnl": float(cp),
            "categories": list(cats) if cats else [],
        } for (ts, w, tok, ty, sd, p, v, cp, cats) in r.result_rows]
        return response.json({"mode": mode, "rows": rows})

    if mode == "overview":
        r = await ch.query(
            "SELECT token, type, sum(value) AS v, count() AS c"
            " FROM (" + base + ") GROUP BY token, type",
            parameters=params,
        )
        agg: dict[str, dict] = {t: {"token": t} | {k: [0.0, 0] for k in _TP_TYPE_KEYS} for t in tokens}
        for tok, ty, v, c in r.result_rows:
            if tok in agg and ty in agg[tok]:
                agg[tok][ty] = [float(v), int(c)]
        return response.json({"mode": mode, "flip_split": flip_split, "tokens": list(agg.values())})

    # aggregate: per (wallet, token, bucket, pside). incdec netted (signed), else summed.
    r = await ch.query(
        "SELECT wallet, token,"
        " multiIf(type IN ('open_long','open_short'),'open',"
        "         type IN ('close_long','close_short'),'close',"
        "         type IN ('flip_ls','flip_sl'),'flip','incdec') AS bucket,"
        " if(type IN {long_all:Array(String)},'long','short') AS pside,"
        " sum(multiIf(type IN ('inc_long','inc_short'), value, type IN ('dec_long','dec_short'), -value, value)) AS net,"
        " sum(value) AS gross, count() AS c,"
        " toUInt32(medianExact(toUnixTimestamp(time))) AS med_time,"
        " any(dictGet('tradernick.wallet_labels','categories',lower(wallet))) AS cats"
        " FROM (" + base + ") GROUP BY wallet, token, bucket, pside",
        parameters={**params, "long_all": _TP_LONG_TYPES},
    )
    rows = []
    for w, tok, bucket, pside, net, gross, c, med_time, cats in r.result_rows:
        net, gross = float(net), float(gross)
        if bucket == "open":
            ty, val = f"open_{pside}", gross
        elif bucket == "close":
            ty, val = f"close_{pside}", gross
        elif bucket == "flip":
            ty, val = ("flip_sl" if pside == "long" else "flip_ls"), gross
        else:  # incdec net
            ty = f"{'inc' if net >= 0 else 'dec'}_{pside}"
            val = abs(net)
        rows.append({"wallet": w, "token": tok, "type": ty, "side": pside,
                     "value": val, "count": int(c), "time": int(med_time),
                     "categories": list(cats) if cats else []})
    if type_filter:
        rows = [x for x in rows if x["type"] == type_filter]
    rows.sort(key=lambda x: x["value"], reverse=True)
    return response.json({"mode": mode, "rows": rows[:n]})


@bp.get("/hyperliquid/group_fill_pressure")
@throttled("heavy")
async def group_fill_pressure(request):
    """Backtracker group overlay: per-bar buy vs sell USD pressure by a wallet
    GROUP for one token. A HL fill's side='B' is a buy = any position change TO
    long (open/increase long, close/decrease short, flip to long); side='A' is a
    sell = position change to short. So per bar we sum buy-fill and sell-fill
    notional (size×price) over the group's wallets. Params: token, group,
    interval, since, until."""
    token = request.args.get("token")
    group = request.args.get("group")
    if not token or not group:
        return response.json({"error": "missing token/group"}, status=400)
    interval = request.args.get("interval", "15m")
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    since = request.args.get("since")
    until = request.args.get("until")
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    ch = await client()
    try:
        member = _cutoff_membership_sql(await _resolve_group_passing(ch, request), col="wallet")
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)
    since_dt, until_dt = _parse_iso(since), _parse_iso(until)
    sec = INTERVAL_SECONDS[interval]
    rows = await ch.query(
        """
        SELECT toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS bucket,
               sumIf(size * price, side = 'B') AS buys,
               sumIf(size * price, side = 'A') AS sells
        FROM tradernick.hl_fills FINAL
        WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
          AND """ + member + """
        GROUP BY bucket ORDER BY bucket
        """,
        parameters={"sec": sec, "tok": token, "s": since_dt, "u": until_dt},
    )
    # Cumulative realized-PnL LINE value per bar (pnl=1). From Start (no
    # pnl_window): base (Σ closing-fill PnL before the window) + running cumulative
    # within it. Rolling (pnl_window=W seconds): Σ closing-fill PnL over the
    # trailing W ENDING AT EACH BAR — a sliding window re-cut per bar, computed
    # over an extended [since−W, until] scan with a RANGE window frame.
    pnl_line = []
    if request.args.get("pnl") in ("1", "true", "yes"):
        try:
            win = int(request.args.get("pnl_window", "0"))
        except ValueError:
            win = 0
        if win > 0:
            # win is inlined (validated int) — CH doesn't substitute query params
            # inside a RANGE window frame.
            lr = await ch.query(
                """
                SELECT toUnixTimestamp(b) AS t,
                       sum(p) OVER (ORDER BY toUnixTimestamp(b) ASC
                            RANGE BETWEEN """ + str(win) + """ PRECEDING AND CURRENT ROW) AS v
                FROM (
                    SELECT toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND) AS b, sum(closed_pnl) AS p
                    FROM tradernick.hl_fills FINAL
                    WHERE token = {tok:String} AND time >= {rs:DateTime} AND time < {u:DateTime}
                      AND """ + member + """
                    GROUP BY b
                )
                WHERE b >= {s:DateTime} ORDER BY b
                """,
                parameters={"sec": sec, "tok": token,
                            "rs": since_dt - timedelta(seconds=win), "s": since_dt, "u": until_dt},
            )
            pnl_line = [{"time": int(t), "value": float(v)} for t, v in lr.result_rows]
        else:
            pr = await ch.query(
                "SELECT sum(closed_pnl) FROM tradernick.hl_fills FINAL "
                "WHERE token = {tok:String} AND time < {s:DateTime} AND " + member,
                parameters={"tok": token, "s": since_dt},
            )
            base = float(pr.result_rows[0][0]) if (pr.result_rows and pr.result_rows[0][0] is not None) else 0.0
            lr = await ch.query(
                """
                SELECT toUnixTimestamp(b) AS t,
                       sum(p) OVER (ORDER BY b ASC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS v
                FROM (
                    SELECT toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND) AS b, sum(closed_pnl) AS p
                    FROM tradernick.hl_fills FINAL
                    WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
                      AND """ + member + """
                    GROUP BY b
                )
                ORDER BY b
                """,
                parameters={"sec": sec, "tok": token, "s": since_dt, "u": until_dt},
            )
            pnl_line = [{"time": int(t), "value": base + float(v)} for t, v in lr.result_rows]
    # Net position of the group at each BAR START (netpos=1): signed OI (long +,
    # short −) summed over the group's wallets, sampled at bar-boundary snapshots.
    # Base bars come from the RAW hl_position_history (authoritative; argMax dedups,
    # token-first ORDER BY keeps it cheap). The most-recent bars the raw hasn't
    # published yet (~25m DeFiStream lag) are reconstructed from fills via
    # positions.positions_at — same as the Backtracker dialog — so the marker shows
    # on the last few bars instead of dropping off.
    net_pos = []
    if request.args.get("netpos") in ("1", "true", "yes"):
        # net + per-bar count of wallets net-long vs net-short (each wallet's signed
        # position summed over sides first, then classified). Counts power the
        # Consensus parenthesis (#long/#short) on the Net Position marker.
        npr = await ch.query(
            """
            SELECT t, sum(wv) AS net, countIf(wv > 0) AS n_long, countIf(wv < 0) AS n_short FROM (
                SELECT t, wallet, sum(v) AS wv FROM (
                    SELECT toUnixTimestamp(time) AS t, wallet, side,
                           argMax(size, time) * if(side = 'long', 1, -1) AS v
                    FROM tradernick.hl_position_history
                    WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
                      AND toUnixTimestamp(time) % {sec:UInt32} = 0
                      AND """ + member + """
                    GROUP BY time, wallet, side
                )
                GROUP BY t, wallet
            )
            GROUP BY t ORDER BY t
            """,
            parameters={"tok": token, "s": since_dt, "u": until_dt, "sec": sec},
        )
        net_pos = [{"time": int(t), "net": float(n), "n_long": int(nl), "n_short": int(ns)}
                   for t, n, nl, ns in npr.result_rows]
        # Reconstruct the recent bars past the raw's latest snapshot.
        def _naive(ts: int) -> datetime:
            return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        last_bar = int(until_dt.timestamp()) // sec * sec
        if net_pos:
            anchor, start_ts = _naive(net_pos[-1]["time"]), net_pos[-1]["time"] + sec
        else:
            anchor = await latest_snapshot_bucket(ch, token, until_dt)
            start_ts = (int(anchor.timestamp()) // sec * sec + sec) if anchor else None
        if anchor is not None and start_ts is not None:
            recon_times = list(range(start_ts, last_bar + 1, sec))[:12]  # cap the fan-out
            mem_and = " AND " + member

            async def _recon(ts: int) -> dict:
                pos = await positions_at(ch, token=token, at_time=_naive(ts),
                                         base_bucket=anchor, member=mem_and)
                vals = pos.values()
                return {"time": ts,
                        "net": sum(p["size_usd"] for p in vals),
                        "n_long": sum(1 for p in vals if p["amount"] > 1e-9),
                        "n_short": sum(1 for p in vals if p["amount"] < -1e-9)}

            if recon_times:
                net_pos.extend(await asyncio.gather(*[_recon(ts) for ts in recon_times]))
    # Market-wide SPOT volume delta per bar (spotvd=1): Σ (buyer_taker −
    # seller_taker) × close from Binance spot 1m. Not group-scoped.
    spot_vd = []
    if request.args.get("spotvd") in ("1", "true", "yes"):
        vr = await ch.query(
            """
            SELECT toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS t,
                   sum((buyer_taker_volume - seller_taker_volume) * close) AS vd,
                   sum((buyer_taker_volume + seller_taker_volume) * close) AS vol
            FROM tradernick.binance_spot_ohlcv_1m FINAL
            WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
            GROUP BY t ORDER BY t
            """,
            parameters={"sec": sec, "tok": token, "s": since_dt, "u": until_dt},
        )
        spot_vd = [{"time": int(t), "vd": float(v), "vol": float(vo)} for t, v, vo in vr.result_rows]
    # Consensus counts (consensus=1): per bar, # group wallets whose NET fill
    # direction was buy vs sell (each wallet's signed notional summed then
    # classified). Powers the "count wallets, not $" marker mode.
    consensus = []
    if request.args.get("consensus") in ("1", "true", "yes"):
        cr = await ch.query(
            """
            SELECT bucket, countIf(wn > 0) AS buyers, countIf(wn < 0) AS sellers FROM (
                SELECT toUnixTimestamp(toStartOfInterval(time, INTERVAL {sec:UInt32} SECOND)) AS bucket,
                       wallet, sum(if(side = 'B', size * price, -size * price)) AS wn
                FROM tradernick.hl_fills FINAL
                WHERE token = {tok:String} AND time >= {s:DateTime} AND time < {u:DateTime}
                  AND """ + member + """
                GROUP BY bucket, wallet
            )
            GROUP BY bucket ORDER BY bucket
            """,
            parameters={"sec": sec, "tok": token, "s": since_dt, "u": until_dt},
        )
        consensus = [{"time": int(b), "buyers": int(nb), "sellers": int(ns)}
                     for b, nb, ns in cr.result_rows]
    return response.json({
        "token": token, "interval": interval, "pnl_line": pnl_line,
        "bars": [{"time": int(b), "buys": float(bu), "sells": float(se)}
                 for b, bu, se in rows.result_rows],
        "net_pos": net_pos, "spot_vd": spot_vd, "consensus": consensus,
    })


@bp.get("/hyperliquid/smart_wallet_oi_rolling")
@throttled("heavy")
async def smart_wallet_oi_rolling(request):
    """Per-bucket HL OI aggregated over the ROLLING smart-wallet set — the
    set qualifying for EACH day, not one fixed window.

    For every day D in [since, until], `_build_rolling_selection` selects the
    wallets passing every min_*/max_* guard over their trailing [D-lookback, D]
    window (the qualifying set differs per day). This endpoint then sums OI for
    `oi_token` per hourly bucket over the wallets passing AS OF that bucket's DAY
    (join the per-day passing set on toDate(bucket) = day). Same MV cascade /
    response shape as /smart_oi + /smart_wallet_oi, PLUS each bucket carries
    `wallet_count` = the number of qualifying wallets for that bucket's day.

    Selection params (define the per-day wallet set; same names as
    /smart_wallet_metrics, minus `snapshot`):
      token (scope; absent = global), lookback (1|3|7|14|30), since, until, and
      all min_*/max_*.
    OI params:
      oi_token — token whose OI to plot (default = `token`, else BTC).
      interval, since, until, limit — same as /smart_wallet_oi.
    Returns {"buckets":[{time,long_oi,short_oi,total_oi,long_oi_value,
      short_oi_value,total_oi_value,wallet_count}, …], …echo}.
    """
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    oi_limit = int(request.args.get("limit", "200000"))
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    try:
        sel = _build_rolling_selection(request)
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    # oi_token defaults to the selection scope token, else BTC.
    oi_token = request.args.get("oi_token") or sel["echo"]["token"] or "BTC"

    seconds = INTERVAL_SECONDS[interval]
    oi_since_dt = _parse_iso(since)
    oi_until_dt = _parse_iso(until)
    # Same MV cascade as /smart_wallet_oi.
    if seconds >= 3600 and seconds % 3600 == 0:
        oi_source = "tradernick.hl_position_history_1h"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    elif seconds >= 900 and seconds % 900 == 0:
        oi_source = "tradernick.hl_position_history_15m"
        oi_time_col = "bucket"
        oi_amount_expr = "argMaxMerge(amount_state)"
        oi_size_expr   = "argMaxMerge(size_state)"
    else:
        oi_source = "tradernick.hl_position_history"
        oi_time_col = "time"
        oi_amount_expr = "argMax(amount, time)"
        oi_size_expr   = "argMax(size,   time)"

    ch = await client()
    # Resolve (compute-or-cache) the rolling per-(day,wallet) passing set.
    sel_key = await _resolve_rolling_passing(ch, sel)

    params = {
        "seconds": seconds, "oi_token": oi_token,
        "oi_since": oi_since_dt, "oi_until": oi_until_dt, "oi_limit": oi_limit,
        "sel_key": sel_key,
    }

    # Per-bucket OI over wallets passing AS OF that bucket's day. A first stage
    # collapses (bucket, wallet, side) to the latest amount/size, carrying the
    # bucket's day; we INNER JOIN the per-day passing set on (day, wallet) and
    # the per-day distinct count so each surviving row carries day_cnt. After the
    # bucket GROUP BY, any(day_cnt) = the number of qualifying wallets that day
    # (constant within a day). NOTE wallet_count counts ALL qualifying wallets
    # for the day, including any holding no oi_token position that bucket.
    sql = (
        "WITH pset AS (\n"
        "    SELECT day, wallet FROM " + _ROLLING_SET_CACHE_TABLE + "\n"
        "    WHERE sel_key = {sel_key:String}\n"
        "      AND computed_at = (SELECT max(computed_at) FROM " + _ROLLING_SET_CACHE_TABLE + " WHERE sel_key = {sel_key:String})\n"
        "),\n"
        "day_counts AS (\n"
        "    SELECT day, toUInt32(uniqExact(wallet)) AS cnt FROM pset GROUP BY day\n"
        ")\n"
        "SELECT\n"
        "    toUnixTimestamp(bucket)            AS bucket,\n"
        "    sumIf(latest_amount, side='long')  AS long_oi,\n"
        "    sumIf(latest_amount, side='short') AS short_oi,\n"
        "    sum(latest_amount)                 AS total_oi,\n"
        "    sumIf(latest_size,   side='long')  AS long_oi_value,\n"
        "    sumIf(latest_size,   side='short') AS short_oi_value,\n"
        "    sum(latest_size)                   AS total_oi_value,\n"
        "    toUInt32(any(day_cnt))             AS wallet_count,\n"
        "    toUInt32(uniqExactIf(wallet, side='long'  AND latest_amount > 0)) AS long_count,\n"
        "    toUInt32(uniqExactIf(wallet, side='short' AND latest_amount > 0)) AS short_count\n"
        "FROM (\n"
        "    SELECT p.bucket AS bucket, p.side AS side, p.wallet AS wallet,\n"
        "        p.latest_amount AS latest_amount, p.latest_size AS latest_size,\n"
        "        dc.cnt AS day_cnt\n"
        "    FROM (\n"
        "        SELECT\n"
        "            toStartOfInterval(" + oi_time_col + ", INTERVAL {seconds:UInt32} SECOND) AS bucket,\n"
        "            toDate(" + oi_time_col + ") AS day,\n"
        "            wallet, side,\n"
        "            " + oi_amount_expr + " AS latest_amount,\n"
        "            " + oi_size_expr + "   AS latest_size\n"
        "        FROM " + oi_source + "\n"
        "        WHERE token = {oi_token:String}\n"
        "          AND " + oi_time_col + " >= {oi_since:DateTime}\n"
        "          AND " + oi_time_col + " <  {oi_until:DateTime}\n"
        "        GROUP BY bucket, day, wallet, side\n"
        "    ) p\n"
        "    INNER JOIN pset s ON s.day = p.day AND s.wallet = p.wallet\n"
        "    INNER JOIN day_counts dc ON dc.day = p.day\n"
        ")\n"
        "GROUP BY bucket\n"
        "ORDER BY bucket\n"
        "LIMIT {oi_limit:UInt32}\n"
    )

    rows = await ch.query(sql, parameters=params)
    buckets = [
        {
            "time": int(r[0]),
            "long_oi": float(r[1]),
            "short_oi": float(r[2]),
            "total_oi": float(r[3]),
            "long_oi_value": float(r[4]),
            "short_oi_value": float(r[5]),
            "total_oi_value": float(r[6]),
            "wallet_count": int(r[7]),
            "long_count": int(r[8]),
            "short_count": int(r[9]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        **sel["echo"],
        "oi_token": oi_token,
        "interval": interval,
        "buckets": buckets,
    })


@bp.get("/hyperliquid/smart_wallets")
@throttled("heavy")
async def smart_wallets(request):
    """List the wallets the SmartSelector picks on a specific day.

    Companion endpoint to /hyperliquid/smart_oi — the chart shows an
    aggregate wallet count per bucket; clicking on the wallets line
    pops up the actual addresses, which this endpoint returns.

    Query params:
      token   — required when any criterion uses scope=token
      day     — ISO date the user clicked (YYYY-MM-DD). Pinned to one
                day so the response is bounded.
      selector — same JSON shape as /smart_oi.
    """
    token = request.args.get("token")
    day_arg = request.args.get("day")
    selector_raw = request.args.get("filter") or request.args.get("selector")
    if not day_arg:
        return response.json({"error": "missing day (YYYY-MM-DD)"}, status=400)
    try:
        day_dt = datetime.fromisoformat(day_arg).replace(tzinfo=None)
    except ValueError:
        return response.json({"error": "invalid day; expected YYYY-MM-DD"}, status=400)
    # The selector's window must straddle `day` so its target_days CTE
    # produces a row for it. Use [day, day+1) as a minimal window — the
    # selector still computes the full lookback ending at `day`.
    since_dt = day_dt
    until_dt = day_dt + timedelta(days=1)
    try:
        selector = SmartSelector.from_json(selector_raw, token=token)
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    ch = await client()
    selector_cte_sql, smart_cte_name, selector_params = await wallets_cache.resolve(
        ch, selector, token, since_dt, until_dt)
    params: dict = {**selector_params, "day": day_dt.date()}

    # Pull the wallets array for the requested day. groupArray inside
    # the selector already top-N caps it; we just expand to one row per
    # wallet preserving rank order (limit again as a belt-and-suspenders).
    sql = f"""
        {selector_cte_sql}
        SELECT arrayJoin(wallets) AS wallet
        FROM {smart_cte_name}
        WHERE day = {{day:Date}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters=params)
    wallets = [r[0] for r in rows.result_rows]

    # Per-wallet "as of <day>" metric values: the exact figures the SELECTOR
    # computed at the admission day (Sharpe annualized, etc.) — what the dialog
    # shows instead of a current-time recomputation. Only the ROOT node's own
    # criteria/sort metrics (a pure-composite root has none). Best-effort: a
    # failure here just omits the as-of stats, the wallet list still returns.
    as_of_metrics: list = []
    wallet_metrics: dict = {}
    mq = selector.build_root_metrics_query(since_dt, until_dt)
    if mq is not None and wallets:
        msql, mparams, meta = mq
        mparams = {**mparams, "m_day": day_dt.date(), "m_wallets": wallets}
        try:
            mrows = await ch.query(msql, parameters=mparams)
            as_of_metrics = meta
            for r in mrows.result_rows:
                w = r[0]
                wallet_metrics[w] = {
                    meta[i]["key"]: (float(r[i + 1]) if r[i + 1] is not None else None)
                    for i in range(len(meta))
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("smart_wallets as-of metrics failed: %s", exc)

    # Per-wallet position in the chart token AS OF the filter day (latest hourly
    # snapshot that day): drives the long/short/none dot in the list + the
    # position panel in the expanded view. Only wallets actually holding the
    # token that day appear; the rest render gray (no position). Best-effort.
    wallet_positions: dict = {}
    if token and wallets:
        pos_sql = """
            SELECT wallet, side, amount, size_usd, unrealized
            FROM (
                SELECT wallet,
                       argMax(side, bucket)  AS side,
                       argMax(amt, bucket)   AS amount,
                       argMax(sz, bucket)    AS size_usd,
                       argMax(pnl, bucket)   AS unrealized
                FROM (
                    SELECT wallet, bucket, side,
                           argMaxMerge(amount_state) AS amt,
                           argMaxMerge(size_state)   AS sz,
                           argMaxMerge(pnl_state)    AS pnl
                    FROM tradernick.hl_position_history_1h
                    WHERE token = {p_token:String}
                      AND toDate(bucket) = {p_day:Date}
                      AND wallet IN {p_wallets:Array(String)}
                    GROUP BY wallet, bucket, side
                )
                GROUP BY wallet
            )
            WHERE amount > 0
        """
        try:
            prows = await ch.query(pos_sql, parameters={
                "p_token": token, "p_day": day_dt.date(), "p_wallets": wallets})
            for r in prows.result_rows:
                wallet_positions[r[0]] = {
                    "side": r[1],
                    "amount": float(r[2]),
                    "size_usd": float(r[3]),
                    "unrealized": float(r[4]),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("smart_wallets positions failed: %s", exc)

    return response.json({
        "day": day_dt.date().isoformat(),
        "selector": selector.summary(),
        "token": token,
        "wallets": wallets,
        "as_of_metrics": as_of_metrics,
        "wallet_metrics": wallet_metrics,
        "wallet_positions": wallet_positions,
    })


@bp.get("/hyperliquid/wallet_pnl")
@throttled("heavy")
async def wallet_pnl(request):
    """Per-wallet daily PnL time series + summary stats.

    Powers the collapsible PnL view in the Smart Wallets dialog. Returns,
    per day d, two cumulative curves the client can switch between:

        realized_day(d) = Σ net_pnl on day d        (price PnL net of fees)
        unrealized(d)   = EOD mark-to-market of open positions at day d
        realized(d)     = cumsum(realized_day) up to and including day d
        total(d)        = realized(d) + unrealized(d)

    `realized` is the cumulative realized-PnL equity curve; `total` adds the
    end-of-day unrealized snapshot on top. Funding is excluded from both.
    realized PnL is GLOBAL: summed across all HL tokens for the wallet, not
    scoped to any single token.

    hl_trade_history is a ReplacingMergeTree — read with FINAL so re-backfilled
    duplicate (wallet, token, time) rows collapse to their latest version
    instead of summing (which otherwise massively inflates PnL/volume).

    Stats are computed from the DAILY series regardless of display timeframe:

        realized_pnl   — Σ realized over the window (= realized at last day)
        unrealized_pnl — latest EOD unrealized snapshot
        sharpe         — mean / stddevPop of daily realized returns, ANNUALIZED (×√365)
        volatility     — stddevPop of daily realized returns ($)

    Query params:
      wallet — required, the address (lowercased to match the tables).
      token  — optional. When set, the curve + stats cover ONLY this token
               (matches a token-scoped criterion); omitted → global/all tokens.
      since  — ISO date (inclusive). Defaults to until − 180 days.
      until  — ISO date (inclusive). Defaults to today (UTC).
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()

    # Optional token scope: when set, the curve + stats cover only this token
    # (matches a token-scoped Sharpe/OI criterion). Omitted → GLOBAL (all
    # tokens), the original behaviour. Token symbols are stored as-is
    # (uppercase), so do NOT lowercase like the wallet.
    token = request.args.get("token") or None

    until_arg = request.args.get("until")
    since_arg = request.args.get("since")
    try:
        until_dt = (datetime.fromisoformat(until_arg).replace(tzinfo=None)
                    if until_arg else datetime.utcnow())
    except ValueError:
        return response.json({"error": "invalid until; expected YYYY-MM-DD"}, status=400)
    try:
        since_dt = (datetime.fromisoformat(since_arg).replace(tzinfo=None)
                    if since_arg else until_dt - timedelta(days=180))
    except ValueError:
        return response.json({"error": "invalid since; expected YYYY-MM-DD"}, status=400)
    if since_dt > until_dt:
        return response.json({"error": "since must be <= until"}, status=400)

    # One row per day in [since, until]: realized flow (Σ net_pnl, GLOBAL —
    # across all tokens) from hl_trade_history, and the EOD unrealized
    # snapshot collapsed across (token, side). Funding is intentionally NOT
    # read — the curve is realized PnL only. LEFT JOINs onto a dense day
    # spine so gaps read 0.
    sql = """
        WITH
        days AS (
            SELECT toDate({since:DateTime}) + number AS day
            FROM numbers(0, dateDiff('day', toDate({since:DateTime}), toDate({until:DateTime})) + 1)
        ),
        @@REALIZED@@,
        realized_tail AS (
            -- In-progress day reconstructed to NOW: realized so far today =
            -- closed_pnl − fee from hl_fills since today's 00:00 trade_history
            -- snapshot. Anchored to now() (not the `until` arg) so the latest
            -- point is exact-to-the-minute even if a caller passes a date-only
            -- `until`; only contributes when today falls inside [since, until].
            SELECT toDate(now()) AS day,
                   sum(closed_pnl) - sum(fee) AS tail
            FROM tradernick.hl_fills FINAL
            WHERE wallet = {wallet:String}
              AND time > toStartOfDay(now())
              AND time <= now()
              @@TOK@@
        ),
        unreal AS (
            SELECT day, sum(eod) AS unrealized
            FROM (
                SELECT day, token, side, argMaxMerge(pnl_state) AS eod
                FROM tradernick.hl_position_history_eod_wallet
                WHERE wallet = {wallet:String}
                  AND day >= toDate({since:DateTime})
                  AND day <= toDate({until:DateTime})
                  @@TOK@@
                GROUP BY day, token, side
            )
            GROUP BY day
        ),
        @@OICTE@@,
        @@VOLCTE@@
        SELECT d.day AS day,
               coalesce(r.realized, 0) + coalesce(rt.tail, 0) AS realized,
               coalesce(u.unrealized, 0) AS unrealized,
               coalesce(o.oi, 0) AS oi,
               coalesce(v.volume, 0) AS volume,
               coalesce(v.trades, 0) AS trades
        FROM days d
        LEFT JOIN realized r       ON r.day  = d.day
        LEFT JOIN unreal   u       ON u.day  = d.day
        LEFT JOIN realized_tail rt ON rt.day = d.day
        LEFT JOIN oi_daily o       ON o.day  = d.day
        LEFT JOIN vol_daily v      ON v.day  = d.day
        ORDER BY d.day
    """
    # realized daily-flow CTE: GLOBAL reads the pre-aggregated per-(day,wallet)
    # rollup (token dimension summed away, HIP3 excluded) — the per-day delta is
    # snapshot[d] − snapshot[d-1] of the across-tokens cumulative. TOKEN scope
    # keeps the source (the rollup has no token column). Both fetch one day
    # before `since` so the first in-range day has a prior snapshot to diff.
    if token:
        realized_cte = """realized AS (
            SELECT day,
                   cum - lagInFrame(cum, 1, 0)
                         OVER (ORDER BY day ASC
                               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS realized
            FROM (
                SELECT d AS day, sum(cum_np) AS cum
                FROM (
                    SELECT toDate(time) AS d, token, argMax(net_pnl, time) AS cum_np
                    FROM tradernick.hl_trade_history FINAL
                    WHERE wallet = {wallet:String}
                      AND time >= {since:DateTime} - INTERVAL 1 DAY
                      AND time <  {until:DateTime} + INTERVAL 1 DAY
                      AND token = {token:String}
                    GROUP BY d, token
                )
                GROUP BY d
            )
        )"""
    else:
        realized_cte = """realized AS (
            SELECT day,
                   cum - lagInFrame(cum, 1, 0)
                         OVER (ORDER BY day ASC
                               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS realized
            FROM (
                SELECT day, sumMerge(net_pnl_state) AS cum
                FROM tradernick.hl_trade_history_wallet_daily
                WHERE wallet = {wallet:String}
                  AND day >= toDate({since:DateTime}) - 1
                  AND day <  toDate({until:DateTime}) + 1
                GROUP BY day
            )
        )"""
    sql = sql.replace("@@REALIZED@@", realized_cte)
    # Daily EOD open-interest (total notional $) curve. GLOBAL only — the
    # per-(day,wallet) OI rollup has the token dimension collapsed, so a
    # token-scoped request gets an empty CTE (oi → 0). last_total_oi_usd_state
    # is argMaxIf(value, bucket, non-HIP3) → the value at the day's last bucket.
    if token:
        oi_cte = ("oi_daily AS (SELECT toDate({since:DateTime}) AS day, "
                  "toFloat64(0) AS oi FROM numbers(0))")
    else:
        oi_cte = """oi_daily AS (
            SELECT day, argMaxIfMerge(last_total_oi_usd_state) AS oi
            FROM tradernick.hl_position_history_oi_wallet_daily
            WHERE wallet = {wallet:String}
              AND day >= toDate({since:DateTime})
              AND day <= toDate({until:DateTime})
            GROUP BY day
        )"""
    sql = sql.replace("@@OICTE@@", oi_cte)
    # Daily within-window volume ($) + trade count. GLOBAL only (the wallet-daily
    # rollup has no token column); token scope → empty CTE (0). trade_history is
    # cumulative-snapshot, so the per-day flow is the snapshot-diff cum − lag.
    if token:
        vol_cte = ("vol_daily AS (SELECT toDate({since:DateTime}) AS day, "
                   "toFloat64(0) AS volume, toUInt64(0) AS trades FROM numbers(0))")
    else:
        vol_cte = """vol_daily AS (
            SELECT day,
                   cum_v - lagInFrame(cum_v, 1, 0) OVER w AS volume,
                   cum_t - lagInFrame(cum_t, 1, 0) OVER w AS trades
            FROM (
                SELECT day,
                       sumMerge(volume_state)      AS cum_v,
                       sumMerge(trade_count_state) AS cum_t
                FROM tradernick.hl_trade_history_wallet_daily
                WHERE wallet = {wallet:String}
                  AND day >= toDate({since:DateTime}) - 1
                  AND day <  toDate({until:DateTime}) + 1
                GROUP BY day
            )
            WINDOW w AS (ORDER BY day ASC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )"""
    sql = sql.replace("@@VOLCTE@@", vol_cte)
    # Splice the token filter into the remaining sources (fills tail + eod
    # unrealized), or strip the sentinel for the global curve.
    sql = sql.replace("@@TOK@@", "AND token = {token:String}" if token else "")
    params = {"wallet": wallet, "since": since_dt, "until": until_dt}
    if token:
        params["token"] = token
    ch = await client()
    rows = await ch.query(sql, parameters=params)

    series = []
    cum_realized = 0.0
    cum_volume = 0.0
    cum_trades = 0
    daily_returns: list[float] = []
    last_unrealized = 0.0
    # Skip the dead lead-in: a 180-day default window can start before the
    # wallet's first activity (or before the data floor), which would show a
    # long flat-zero stretch and dilute the volatility. Start emitting (and
    # counting returns) at the first day with any realized / unrealized value.
    started = False
    for r in rows.result_rows:
        day, realized, unrealized, oi, volume, trades = r
        realized = float(realized)
        unrealized = float(unrealized)
        oi = float(oi)
        if not started:
            if realized == 0.0 and unrealized == 0.0:
                continue
            started = True
        cum_realized += realized
        cum_volume += float(volume)
        cum_trades += int(trades)
        last_unrealized = unrealized
        # Unix seconds at UTC midnight — Lightweight Charts time format.
        t = int(datetime(day.year, day.month, day.day,
                         tzinfo=timezone.utc).timestamp())
        series.append({
            "time": t,
            # Two switchable cumulative curves.
            "realized": cum_realized,
            "total": cum_realized + unrealized,
            # Per-day components (for reference / future tooltips).
            "realized_day": realized,
            "unrealized": unrealized,
            # End-of-day open interest (total notional $), GLOBAL.
            "oi": oi,
            # Cumulative within-window volume ($) + trade count (GLOBAL).
            "volume": cum_volume,
            "trades": cum_trades,
        })
        # Daily return = that day's realized PnL (= Δ of the realized curve).
        daily_returns.append(realized)

    # Sharpe / volatility from daily realized returns (population stddev to
    # match the smart_selector Sharpe definition). Volatility is reported as
    # a plain number — the daily-returns standard deviation — not a currency.
    # Sharpe is ANNUALIZED (×√365, 24/7 crypto) so it matches the wallet page's
    # annualized range-Sharpe and is comparable across windows.
    ANNUALIZE = 365 ** 0.5
    n = len(daily_returns)
    if n > 0:
        mean = sum(daily_returns) / n
        var = sum((x - mean) ** 2 for x in daily_returns) / n
        vol = var ** 0.5
        sharpe = (mean / vol) * ANNUALIZE if vol > 0 else 0.0
    else:
        vol = 0.0
        sharpe = 0.0

    stats = {
        "realized_pnl": cum_realized,
        "unrealized_pnl": last_unrealized,
        "sharpe": sharpe,
        "volatility": vol,
    }
    return response.json({
        "wallet": wallet,
        "token": token,            # null = global (all tokens)
        "since": since_dt.date().isoformat(),
        "until": until_dt.date().isoformat(),
        "series": series,
        "stats": stats,
    })


@bp.get("/hyperliquid/token_close")
@throttled("light")
async def token_close(request):
    """Daily close price for a token — overlays on the Smart Wallets dialog's
    mini-PnL chart. Returns one point per day (last 1-minute close of the day).

    Query params:
      token — required (e.g. AAVE).
      since — ISO date (inclusive). Defaults to until − 180 days.
      until — ISO date (inclusive). Defaults to today (UTC).
    """
    token = request.args.get("token")
    if not token:
        return response.json({"error": "missing token"}, status=400)
    until_arg = request.args.get("until")
    since_arg = request.args.get("since")
    try:
        until_dt = (datetime.fromisoformat(until_arg).replace(tzinfo=None)
                    if until_arg else datetime.utcnow())
        since_dt = (datetime.fromisoformat(since_arg).replace(tzinfo=None)
                    if since_arg else until_dt - timedelta(days=180))
    except ValueError:
        return response.json({"error": "invalid since/until; expected YYYY-MM-DD"}, status=400)
    if since_dt > until_dt:
        return response.json({"error": "since must be <= until"}, status=400)

    sql = """
        SELECT toDate(time) AS d, argMax(close, time) AS close
        FROM tradernick.hl_ohlcv_1m
        WHERE token = {token:String}
          AND time >= {since:DateTime}
          AND time <  {until:DateTime} + INTERVAL 1 DAY
        GROUP BY d
        ORDER BY d
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "token": token, "since": since_dt, "until": until_dt})
    series = [
        {"time": int(datetime(r[0].year, r[0].month, r[0].day,
                              tzinfo=timezone.utc).timestamp()),
         "close": float(r[1])}
        for r in rows.result_rows
    ]
    return response.json({"token": token, "series": series})


@bp.get("/hyperliquid/wallet_fills")
@throttled("light")
async def wallet_fills(request):
    """The wallet's latest individual fills (raw trades) for the wallet-page trades
    table + flow debugging: time, token, direction (Open/Close/flip), buy/sell, price,
    size, value $, realized PnL, fee. Optionally scoped to one token, newest first.

    Query params:
      wallet — required (0x…; lowercased to match the table).
      token  — optional (scope to one token).
      since/until — ISO datetime window (default until=now, since=until-30d).
      limit  — max rows (default 200, cap 1000).
    Returns { wallet, token, trades:[{time, token, dir, side, price, size, value,
              closed_pnl, fee}] } newest-first.

    Perf: hl_fills is sorted (token, time, …) so a wallet-only filter can't use the
    primary index and scans the whole window across all tokens. A bloom_filter skip
    index on `wallet` skips granules that don't contain it; FINAL (kept for correctness —
    it returns the latest re-backfilled version of each fill) is then acceptable for
    short windows. The wallet-page table defaults to a 1d window and shows a loading
    indicator, so a long (90d) window is opt-in and non-blocking.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()
    token = request.args.get("token") or None
    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 1000))
    except ValueError:
        limit = 200
    until_arg = request.args.get("until")
    since_arg = request.args.get("since")
    try:
        until_dt = (datetime.fromisoformat(until_arg).replace(tzinfo=None)
                    if until_arg else datetime.utcnow())
        since_dt = (datetime.fromisoformat(since_arg).replace(tzinfo=None)
                    if since_arg else until_dt - timedelta(days=30))
    except ValueError:
        return response.json({"error": "invalid since/until; expected ISO"}, status=400)

    tok_filter = "AND token = {token:String}" if token else ""
    params = {"wallet": wallet, "s": since_dt, "u": until_dt, "lim": limit}
    if token:
        params["token"] = token
    ch = await client()
    r = await ch.query(
        f"""
        SELECT toUnixTimestamp(time) AS ts, token, dir, side, price, size,
               size * price AS value, closed_pnl, fee
        FROM tradernick.hl_fills FINAL
        WHERE wallet = {{wallet:String}} AND time >= {{s:DateTime}} AND time < {{u:DateTime}} {tok_filter}
        ORDER BY time DESC
        LIMIT {{lim:UInt32}}
        """,
        parameters=params,
    )
    trades = [{
        "time": int(ts), "token": tok, "dir": d, "side": s,
        "price": float(p), "size": float(sz), "value": float(v),
        "closed_pnl": float(cp), "fee": float(f),
    } for (ts, tok, d, s, p, sz, v, cp, f) in r.result_rows]
    return response.json({"wallet": wallet, "token": token, "trades": trades})


@bp.get("/hyperliquid/wallet_trades")
@throttled("light")
async def wallet_trades(request):
    """Per-(day, token) net buy/sell flow for a wallet — drives the 'Show
    Trades' markers on the wallet PnL chart.

    For each (day, token), the net signed USD traded = Σ(buy size·price) −
    Σ(sell size·price) over the wallet's fills (side 'B' = buy, 'A' = sell);
    net_tokens is the same in token units. Buys and sells of the SAME token on
    the same day net out (it's a signed sum); a positive net is a net BUY,
    negative a net SELL. Rows are per token (NOT summed across tokens), so the
    client can show a same-day net-buy and net-sell marker for different tokens
    and break the totals down by token on hover. When `token` is given, only
    that token's rows are returned.

    hl_fills is a ReplacingMergeTree — read FINAL so re-backfilled duplicate
    (token, time, tid, wallet) rows collapse instead of double-counting.

    Query params:
      wallet — required (0x…; lowercased to match the table).
      token  — optional (e.g. AAVE). Scopes the flow to one token.
      since  — ISO date (inclusive). Defaults to until − 180 days.
      until  — ISO date (inclusive). Defaults to today (UTC).
    Returns { wallet, token,
              series:[{time (epoch s, UTC midnight), token, net_usd, net_tokens}] }.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()
    token = request.args.get("token") or None

    until_arg = request.args.get("until")
    since_arg = request.args.get("since")
    try:
        until_dt = (datetime.fromisoformat(until_arg).replace(tzinfo=None)
                    if until_arg else datetime.utcnow())
        since_dt = (datetime.fromisoformat(since_arg).replace(tzinfo=None)
                    if since_arg else until_dt - timedelta(days=180))
    except ValueError:
        return response.json({"error": "invalid since/until; expected YYYY-MM-DD"}, status=400)
    if since_dt > until_dt:
        return response.json({"error": "since must be <= until"}, status=400)

    tok_filter = "AND token = {token:String}" if token else ""
    # net_usd = signed Σ(size·price); net_tokens = signed Σ(size). Grouped per
    # (day, token) so same-token buys/sells net out but different tokens stay
    # separate (the client aggregates per side and lists tokens on hover).
    sql = f"""
        SELECT toDate(time) AS day, token,
               sum(if(side = 'B', size * price, -size * price)) AS net_usd,
               sum(if(side = 'B', size, -size)) AS net_tokens,
               sum(size * price) / nullIf(sum(size), 0) AS avg_px
        FROM tradernick.hl_fills FINAL
        WHERE wallet = {{wallet:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}} + INTERVAL 1 DAY
          {tok_filter}
        GROUP BY day, token
        HAVING round(net_usd, 2) != 0
        ORDER BY day, abs(net_usd) DESC
    """
    params = {"wallet": wallet, "since": since_dt, "until": until_dt}
    if token:
        params["token"] = token
    ch = await client()
    rows = await ch.query(sql, parameters=params)
    # avg_px = volume-weighted execution price across the day's fills (both
    # sides), in USD per token — independent of the net direction.
    series = [
        {"time": int(datetime(r[0].year, r[0].month, r[0].day,
                              tzinfo=timezone.utc).timestamp()),
         "token": r[1],
         "net_usd": float(r[2]),
         "net_tokens": float(r[3]),
         "avg_px": float(r[4]) if r[4] is not None else 0.0}
        for r in rows.result_rows
    ]
    return response.json({"wallet": wallet, "token": token, "series": series})


@bp.get("/hyperliquid/wallet_transfers")
@throttled("light")
async def wallet_transfers(request):
    """All bridge transfers (deposits / withdrawals) for a wallet, newest
    first — independent of any snapshot day.

    hl_transfers is a ReplacingMergeTree; read FINAL so re-ingested duplicate
    (direction, time, wallet) rows collapse. Capped at the 1000 most recent
    (a few high-frequency wallets have tens of thousands).

    Query params:
      wallet — required (0x…; lowercased to match the table).
    Returns { wallet, transfers:[{time (epoch s), direction, amount}] }.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()
    sql = """
        SELECT toUnixTimestamp(time) AS ts, direction, amount
        FROM tradernick.hl_transfers FINAL
        WHERE wallet = {wallet:String}
        ORDER BY time DESC
        LIMIT 1000
    """
    ch = await client()
    rows = await ch.query(sql, parameters={"wallet": wallet})
    transfers = [
        {"time": int(r[0]), "direction": r[1], "amount": float(r[2])}
        for r in rows.result_rows
    ]
    return response.json({"wallet": wallet, "transfers": transfers})


@bp.get("/hyperliquid/wallet_trade_stats")
@throttled("light")
async def wallet_trade_stats(request):
    """Per-wallet execution-quality stats over a window (GLOBAL, all tokens):

      avg_trade_size = volume / trade_count                        ($/trade)
      taker_pct      = taker_volume / total_fill_volume × 100      (%)
      fee_pct        = fees / realized_pnl × 100                   (%)
      funding_pct    = funding_pnl / realized_pnl × 100            (%)

    realized_pnl is GROSS (pnl, pre-fee); fees are the trading fees (a cost,
    maker rebates → negative); funding_pnl is signed net (positive = received)
    from hl_funding_daily (matches the smart-money funding share). volume/trades/
    realized/fees come from the per-(day,wallet) trade-history rollup via
    snapshot-diff (cumulative end − start); taker/total volume from the per-day
    fills-volume rollup; funding from the per-day funding rollup.

    Query params: wallet (required), since/until ISO (default until−180 / today).
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()
    until_arg = request.args.get("until")
    since_arg = request.args.get("since")
    try:
        until_dt = (datetime.fromisoformat(until_arg).replace(tzinfo=None)
                    if until_arg else datetime.utcnow())
        since_dt = (datetime.fromisoformat(since_arg).replace(tzinfo=None)
                    if since_arg else until_dt - timedelta(days=180))
    except ValueError:
        return response.json({"error": "invalid since/until; expected YYYY-MM-DD"}, status=400)
    if since_dt > until_dt:
        return response.json({"error": "since must be <= until"}, status=400)

    sql = """
        WITH
        th_e AS (
            SELECT sumMerge(pnl_state) AS pnl, sumMerge(fees_state) AS fees,
                   sumMerge(volume_state) AS vol, sumMerge(trade_count_state) AS tc
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {wallet:String}
              AND day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily
                         WHERE wallet = {wallet:String} AND day <= toDate({until:DateTime}))
        ),
        th_s AS (
            SELECT sumMerge(pnl_state) AS pnl, sumMerge(fees_state) AS fees,
                   sumMerge(volume_state) AS vol, sumMerge(trade_count_state) AS tc
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {wallet:String}
              AND day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily
                         WHERE wallet = {wallet:String} AND day < toDate({since:DateTime}))
        ),
        taker AS (
            SELECT sumMerge(taker_buy_vol_usd_state) + sumMerge(taker_sell_vol_usd_state) AS tk,
                   sumMerge(vol_usd_state) AS tot,
                   uniqExact(day) AS active_days
            FROM tradernick.hl_fills_vol_daily
            WHERE wallet = {wallet:String}
              AND day >= toDate({since:DateTime}) AND day <= toDate({until:DateTime})
        ),
        fund AS (
            SELECT sumMerge(funding_pnl_state) AS f
            FROM tradernick.hl_funding_daily
            WHERE wallet = {wallet:String}
              AND day >= toDate({since:DateTime}) AND day <= toDate({until:DateTime})
        ),
        fs AS (
            -- First recorded trade day (account age), over ALL history (no window).
            SELECT min(day) AS first_day
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {wallet:String}
        )
        SELECT th_e.vol - th_s.vol, th_e.tc - th_s.tc, th_e.pnl - th_s.pnl,
               th_e.fees - th_s.fees, taker.tk, taker.tot, fund.f, taker.active_days,
               dateDiff('day', fs.first_day, toDate({until:DateTime})) AS account_duration_days
        FROM th_e, th_s, taker, fund, fs
    """
    ch = await client()
    rows = await ch.query(sql, parameters={"wallet": wallet, "since": since_dt, "until": until_dt})
    r = rows.result_rows[0] if rows.result_rows else (0, 0, 0, 0, 0, 0, 0, 0, None)
    volume, trades, realized, fees, taker_vol, total_vol, funding, active_days = (float(x or 0) for x in r[:8])
    active_days = int(active_days)
    account_duration_days = int(r[8]) if r[8] is not None else 0
    trades = int(trades)

    # Per-token traded volume (from the per-day fills-volume rollup), for the
    # token-mix breakdown. Tokens under 0.1% of the total fold into "Other".
    tok_rows = await ch.query(
        """
        SELECT token, sumMerge(vol_usd_state) AS vol
        FROM tradernick.hl_fills_vol_daily
        WHERE wallet = {wallet:String}
          AND day >= toDate({since:DateTime}) AND day <= toDate({until:DateTime})
        GROUP BY token HAVING vol > 0
        ORDER BY vol DESC
        """,
        parameters={"wallet": wallet, "since": since_dt, "until": until_dt},
    )
    # Per-token total PnL = window realized (gross) + current open unrealized.
    real_rows = await ch.query(
        """
        SELECT token,
            argMaxIf(pnl, time, toDate(time) <= toDate({until:DateTime}))
            - argMaxIf(pnl, time, toDate(time) < toDate({since:DateTime})) AS realized
        FROM tradernick.hl_trade_history FINAL
        WHERE wallet = {wallet:String} AND toDate(time) <= toDate({until:DateTime})
        GROUP BY token
        """,
        parameters={"wallet": wallet, "since": since_dt, "until": until_dt},
    )
    realized_by = {t[0]: float(t[1]) for t in real_rows.result_rows}
    unreal_rows = await ch.query(
        f"""
        SELECT token, sum(eod) AS un FROM (
            SELECT token, side, argMaxMerge(pnl_state) AS eod
            FROM tradernick.hl_position_history_eod_wallet
            WHERE wallet = {{wallet:String}}
              AND day = (SELECT max(day) FROM tradernick.hl_position_history_eod_wallet
                         WHERE wallet = {{wallet:String}} AND day <= toDate({{until:DateTime}}))
              {HIP3_EXCLUDE}
            GROUP BY token, side
        ) GROUP BY token
        """,
        parameters={"wallet": wallet, "until": until_dt},
    )
    unreal_by = {t[0]: float(t[1]) for t in unreal_rows.result_rows}
    pnl_of = lambda tok: realized_by.get(tok, 0.0) + unreal_by.get(tok, 0.0)
    # All traded tokens with volume + total PnL (realized + current unrealized).
    # The client sorts/percentages/buckets ("Other") by the selected metric.
    tokens = [
        {"token": t[0], "volume": float(t[1]), "pnl": pnl_of(t[0])}
        for t in tok_rows.result_rows
    ]

    # Win rate: % of ACTIVE (trade) days in the window with positive daily total
    # PnL (Δrealized + Δunrealized) — same definition as smart_wallet_metrics.
    wr_rows = await ch.query(
        f"""
        WITH
        rd AS (
            SELECT day AS d, sumMerge(pnl_state) AS cp, sumMerge(trade_count_state) AS ctc
            FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {{wallet:String}}
              AND day >= toDate({{since:DateTime}}) - 1 AND day <= toDate({{until:DateTime}})
            GROUP BY day
        ),
        rdd AS (
            SELECT d, cp - lagInFrame(cp, 1, 0) OVER w AS dr,
                   ctc - lagInFrame(ctc, 1, 0) OVER w AS dtc
            FROM rd WINDOW w AS (ORDER BY d ASC ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        ),
        ed AS (
            SELECT day AS d, sum(e) AS un FROM (
                SELECT day, token, side, argMaxMerge(pnl_state) AS e
                FROM tradernick.hl_position_history_eod_wallet
                WHERE wallet = {{wallet:String}}
                  AND day >= toDate({{since:DateTime}}) - 2 AND day <= toDate({{until:DateTime}}) {HIP3_EXCLUDE}
                GROUP BY day, token, side
            ) GROUP BY d
        ),
        edd AS (
            SELECT d, un - lagInFrame(un, 1, 0) OVER w AS du
            FROM ed WINDOW w AS (ORDER BY d ASC ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        ),
        ds AS (
            SELECT rdd.d AS d, rdd.dr + coalesce(edd.du, 0) AS dt, rdd.dtc AS dtc
            FROM rdd LEFT JOIN edd ON edd.d = rdd.d
            WHERE rdd.d >= toDate({{since:DateTime}})
        )
        SELECT 100 * countIf(dtc > 0 AND dt > 0) / nullIf(countIf(dtc > 0), 0) AS win_rate
        FROM ds
        """,
        parameters={"wallet": wallet, "since": since_dt, "until": until_dt},
    )
    win_rate = (float(wr_rows.result_rows[0][0])
                if wr_rows.result_rows and wr_rows.result_rows[0][0] is not None else None)

    return response.json({
        "win_rate": win_rate,
        "wallet": wallet,
        "volume": volume,
        "trades": trades,
        "realized_pnl": realized,
        "fees": fees,
        "funding": funding,
        "taker_vol": taker_vol,
        "total_vol": total_vol,
        "active_days": active_days,
        "account_duration_days": account_duration_days,
        "trades_per_day": (trades / active_days) if active_days else 0.0,
        "avg_trade_size": (volume / trades) if trades else 0.0,
        "taker_pct": (100.0 * taker_vol / total_vol) if total_vol else 0.0,
        "fee_pct": (100.0 * fees / realized) if realized else None,
        "funding_pct": (100.0 * funding / realized) if realized else None,
        "tokens": tokens,
    })


@bp.get("/hyperliquid/wallet_token_last_day")
@throttled("light")
async def wallet_token_last_day(request):
    """The most recent day whose END-OF-DAY snapshot still held the token — i.e.
    the last day /wallet_positions actually SHOWS it. Matching that is important:
    a position closed mid-day is non-zero earlier in the day but zero at the
    day's last snapshot (which wallet_positions reads), so keying off the last
    non-zero time would jump to a day that no longer shows the token.

    Performance note: hl_position_history is ORDER BY (token, time, side,
    wallet), so a wallet-only filter over all history can't use the primary
    index and full-scans every partition (~8-10s). We avoid that by mirroring
    /wallet_positions' trick — only ever scan the wallet within a *narrow*
    (≤2-day) time window, which prunes to one partition + a tight time range
    (~0.1s). Candidate days come cheaply from the token (token-indexed), then
    we check each, most-recent first, until one still holds the token at the
    wallet's end-of-day snapshot.

    Query params: wallet, token (both required).
    """
    wallet = request.args.get("wallet")
    token = request.args.get("token")
    if not wallet or not token:
        return response.json({"error": "missing wallet/token"}, status=400)
    wallet = wallet.lower()
    ch = await client()

    # Step 1 — candidate days (newest first). Source these from the small,
    # day-partitioned eod_wallet rollup (one row per day/wallet/token) rather
    # than the raw snapshot table: filtering raw by (token, wallet) without a
    # time bound full-scans every snapshot of a popular token across ALL
    # wallets (~3s for BTC), whereas the rollup seeks per-day partitions in
    # ~0.04s. The rollup has a mid-day-close blind spot (it can list a day the
    # token closed before end-of-day), but that only over-produces candidates —
    # the authoritative narrow check below rejects them.
    cand = await ch.query(
        """
        SELECT day AS d
        FROM tradernick.hl_position_history_eod_wallet
        WHERE wallet = {wallet:String} AND token = {token:String}
        GROUP BY day
        ORDER BY day DESC
        """,
        parameters={"wallet": wallet, "token": token},
    )
    cand_days = [r[0] for r in (cand.result_rows if cand else [])]

    # Step 2 — for each candidate day (newest first), confirm the token was
    # still in the WALLET's day-last snapshot (what /wallet_positions reads):
    # find the wallet's max snapshot time in a 2-day window ending at the next
    # midnight, then check the token's summed |amount| at exactly that time.
    # A position closed mid-day drops off the grid before the day's final
    # snapshot, so this correctly skips it. Loop is capped so a pathological
    # wallet (token closed mid-day every day) can't run unbounded; the common
    # case resolves on the first check.
    day = None
    for d in cand_days[:45]:
        until = datetime(d.year, d.month, d.day) + timedelta(days=1)
        chk = await ch.query(
            """
            WITH latest AS (
                SELECT max(time) AS t
                FROM tradernick.hl_position_history
                WHERE wallet = {wallet:String}
                  AND time < {until:DateTime}
                  AND time >= {until:DateTime} - INTERVAL 2 DAY
            )
            SELECT sum(abs(amount)) AS amt
            FROM tradernick.hl_position_history FINAL
            WHERE token = {token:String} AND wallet = {wallet:String}
              AND time = (SELECT t FROM latest)
              AND time >= {until:DateTime} - INTERVAL 2 DAY
            """,
            parameters={"wallet": wallet, "token": token, "until": until},
        )
        amt = chk.result_rows[0][0] if (chk and chk.result_rows) else None
        if amt and amt != 0:
            day = d.isoformat()
            break

    return response.json({"wallet": wallet, "token": token, "day": day})


# ── Live positions from the official Hyperliquid clearinghouse ───────
# Ground-truth check: our hl_position_history is sourced from DeFiStream and
# is, by design, snapshotted on a grid (and only for the INGEST_TOKENS roster)
# — so it lags and can be incomplete. The official HL `clearinghouseState`
# returns a wallet's *current, complete* perp book straight from the exchange.
# This endpoint is a thin pass-through normalizer; we don't store anything.
# It lets us diff "what we have" vs "what HL says right now" later.

_HL_INFO_URL = "https://api.hyperliquid.xyz/info"
_HL_INFO_TIMEOUT_S = 15.0


def _f(v) -> float:
    """HL returns every number as a JSON string ('0.5', '-123.4'). Coerce
    to float, tolerating None / '' / malformed → 0.0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@bp.get("/hyperliquid/live_positions")
@throttled("heavy")
async def live_positions(request):
    """Fetch a wallet's CURRENT perp positions from the official Hyperliquid
    API (clearinghouseState) and normalize them to our (token, side) shape.

    This is the authoritative live book — complete (all tokens, not just our
    ingest roster) and real-time — for comparing against our stored
    hl_position_history snapshots, which are grid-sampled and roster-scoped
    and therefore stale/partial by construction.

    Query params:
      wallet — required, the address (0x…).

    Returns:
      {
        wallet, time (ms epoch from HL), fetched_at (our ISO),
        margin_summary: { account_value, total_ntl_pos, total_raw_usd,
                          total_margin_used },
        withdrawable,
        positions: [ {
          token, side ('long'|'short'),
          amount,            // |szi|, position size in coins
          szi,               // signed size (+long / -short)
          size,              // |positionValue|, USD notional
          entry_px, mark_implied_px, liquidation_px,
          unrealized_pnl, return_on_equity, margin_used, max_leverage,
          leverage_type, leverage_value,
          cum_funding_all_time, cum_funding_since_open, cum_funding_since_change
        }, … ]   // sorted by descending USD notional
      }

    The position shape intentionally overlaps our stored columns
    (token/side/amount/size/unrealized_pnl) so a downstream diff is a plain
    per-(token,side) join.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()

    import aiohttp
    payload = {"type": "clearinghouseState", "user": wallet}
    try:
        timeout = aiohttp.ClientTimeout(total=_HL_INFO_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(_HL_INFO_URL, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return response.json(
                        {"error": f"hyperliquid api returned {resp.status}",
                         "detail": text[:500]},
                        status=502)
                data = await resp.json()
    except aiohttp.ClientError as e:
        return response.json({"error": f"hyperliquid api request failed: {e}"}, status=502)
    except Exception as e:  # JSON decode / timeout
        return response.json({"error": f"hyperliquid api error: {e}"}, status=502)

    ms = data.get("marginSummary") or {}
    positions = []
    for ap in (data.get("assetPositions") or []):
        p = ap.get("position") or {}
        szi = _f(p.get("szi"))
        if szi == 0.0:
            continue  # flat — HL shouldn't emit these, but guard anyway
        lev = p.get("leverage") or {}
        positions.append({
            "token": p.get("coin"),
            "side": "long" if szi > 0 else "short",
            "amount": abs(szi),
            "szi": szi,
            "size": abs(_f(p.get("positionValue"))),
            "entry_px": _f(p.get("entryPx")),
            "liquidation_px": _f(p.get("liquidationPx")),
            "unrealized_pnl": _f(p.get("unrealizedPnl")),
            "return_on_equity": _f(p.get("returnOnEquity")),
            "margin_used": _f(p.get("marginUsed")),
            "max_leverage": p.get("maxLeverage"),
            "leverage_type": lev.get("type"),
            "leverage_value": lev.get("value"),
            "cum_funding_all_time": _f((p.get("cumFunding") or {}).get("allTime")),
            "cum_funding_since_open": _f((p.get("cumFunding") or {}).get("sinceOpen")),
            "cum_funding_since_change": _f((p.get("cumFunding") or {}).get("sinceChange")),
        })
    positions.sort(key=lambda x: x["size"], reverse=True)

    # The live HL API has no position open time, but our latest stored snapshot
    # does (roster tokens only). Attach opened_at per (token, side) so the chart
    # can draw the entry-date marker in live mode too. Cheap: latest snapshot
    # within a 2-day lookback (partition + time pruning).
    if positions:
        try:
            ch = await client()
            op_rows = await ch.query(
                """
                WITH latest AS (
                    SELECT max(time) AS t
                    FROM tradernick.hl_position_history
                    WHERE wallet = {wallet:String}
                      AND time < now()
                      AND time >= now() - INTERVAL 2 DAY
                )
                SELECT token, side,
                       toUnixTimestamp(argMax(opened_at, time)) AS opened
                FROM tradernick.hl_position_history
                WHERE wallet = {wallet:String}
                  AND time = (SELECT t FROM latest)
                  AND time >= now() - INTERVAL 2 DAY
                GROUP BY token, side
                """,
                parameters={"wallet": wallet},
            )
            opened_by = {(r[0], r[1]): int(r[2]) for r in op_rows.result_rows if r[2]}
            for p in positions:
                p["opened_at"] = opened_by.get((p["token"], p["side"]))
        except Exception:  # opened_at is best-effort enrichment — never fail the book
            for p in positions:
                p.setdefault("opened_at", None)

    return response.json({
        "wallet": wallet,
        "time": data.get("time"),
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "margin_summary": {
            "account_value": _f(ms.get("accountValue")),
            "total_ntl_pos": _f(ms.get("totalNtlPos")),
            "total_raw_usd": _f(ms.get("totalRawUsd")),
            "total_margin_used": _f(ms.get("totalMarginUsed")),
        },
        "withdrawable": _f(data.get("withdrawable")),
        "positions": positions,
    })


@bp.get("/hyperliquid/wallet_positions")
@throttled("heavy")
async def wallet_positions(request):
    """A wallet's stored positions AS OF a past day — the historical companion
    to /live_positions (which only has the real-time book).

    Reads the latest hl_position_history snapshot at or before end-of-`day` and
    returns one row per (token, side) still open then. Unlike the 1h rollup,
    the base snapshot table carries the full per-position detail HL's API
    reports — entry price, cumulative funding-since-open, and fees — so the
    wallet page can show the same columns historically that it shows live.

    Query params:
      wallet — required (0x…; lowercased to match the table).
      day    — ISO date (YYYY-MM-DD). Positions as of end of that day.
               Defaults to today (UTC).
    Returns { wallet, day, bucket (epoch s or null), positions:[{token, side,
              amount, size_usd, unrealized_pnl, entry_px, funding, fee}] }
    sorted by descending notional.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()

    day_arg = request.args.get("day")
    try:
        day = (datetime.fromisoformat(day_arg).date() if day_arg
               else datetime.now(timezone.utc).date())
    except ValueError:
        return response.json({"error": "invalid day; expected YYYY-MM-DD"}, status=400)
    # As of END of `day` → everything strictly before the next day's midnight.
    until_dt = datetime(day.year, day.month, day.day) + timedelta(days=1)

    # Positions are snapshotted densely while open, so the latest snapshot
    # at/​before end-of-day is on the day itself — look back only ~2 days so the
    # scan stays inside one monthly partition + a narrow time range (the table
    # sorts token-first, so a token-less wallet filter can't use the index, but
    # partition + time pruning keeps this fast, ~0.1s).
    #
    # NB: do NOT alias the timestamp projection `time` — a SELECT alias that
    # shadows the `time` column would turn the `time = (SELECT t)` row filter
    # into a constant tautology and argMax would then return the latest book
    # for every day (the bug that bit the old `bucket` alias).
    #
    # `funding` is stored as the signed funding PnL (negative = paid); the live
    # API reports cum_funding_since_open with the opposite sign, so negate it
    # here to match the live-mode column.
    sql = """
        WITH latest AS (
            SELECT max(time) AS t
            FROM tradernick.hl_position_history
            WHERE wallet = {wallet:String}
              AND time < {until:DateTime}
              AND time >= {until:DateTime} - INTERVAL 2 DAY
        )
        SELECT token, side,
            argMax(amount, time)         AS amount,
            argMax(size, time)           AS size_usd,
            argMax(unrealized_pnl, time) AS unrealized,
            argMax(avg_entry, time)      AS entry_px,
            -argMax(funding, time)       AS funding,
            argMax(fee, time)            AS fee,
            toUnixTimestamp(argMax(opened_at, time)) AS opened_ts,
            toUnixTimestamp((SELECT t FROM latest)) AS snap_ts
        FROM tradernick.hl_position_history
        WHERE wallet = {wallet:String}
          AND time = (SELECT t FROM latest)
          AND time >= {until:DateTime} - INTERVAL 2 DAY
        GROUP BY token, side
        HAVING amount != 0
        ORDER BY size_usd DESC
    """
    ch = await client()
    rows = await ch.query(sql, parameters={"wallet": wallet, "until": until_dt})
    positions = [
        {
            "token": r[0],
            "side": r[1],
            "amount": float(r[2]),
            "size_usd": float(r[3]),
            "unrealized_pnl": float(r[4]),
            "entry_px": float(r[5]),
            "funding": float(r[6]),
            "fee": float(r[7]),
            "opened_at": int(r[8]) if r[8] else None,
        }
        for r in rows.result_rows
    ]
    bucket = rows.result_rows[0][9] if rows.result_rows else None
    return response.json({
        "wallet": wallet,
        "day": day.isoformat(),
        "bucket": int(bucket) if bucket else None,
        "positions": positions,
    })


@bp.get("/hyperliquid/wallet_range_volume")
@throttled("light")
async def wallet_range_volume(request):
    """Total HL trading volume for a wallet over a [start, end] day range.

    Powers the wallet page's range-mode stats. hl_trade_history_wallet_daily
    stores per-day *cumulative* (from-inception) volume, so the range total is
    the snapshot diff cum(end) − cum(start) — NOT a sum over the days (which
    would add up cumulative curves). Both ends take the latest snapshot ≤ the
    target day so a non-trading boundary day still resolves.

    Query params: wallet (required), start, end (ISO dates).
    Returns { wallet, start, end, volume }.
    """
    wallet = request.args.get("wallet")
    if not wallet:
        return response.json({"error": "missing wallet"}, status=400)
    wallet = wallet.lower()
    try:
        start_dt = datetime.fromisoformat(request.args.get("start")).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(request.args.get("end")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return response.json({"error": "invalid/missing start|end; expected YYYY-MM-DD"}, status=400)

    sql = """
        WITH
        end_day AS (
            SELECT max(day) AS d FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {wallet:String} AND day <= toDate({end:DateTime})
        ),
        start_day AS (
            SELECT max(day) AS d FROM tradernick.hl_trade_history_wallet_daily
            WHERE wallet = {wallet:String} AND day <= toDate({start:DateTime})
        )
        SELECT
            coalesce((SELECT sumMerge(volume_state) FROM tradernick.hl_trade_history_wallet_daily
                      WHERE wallet = {wallet:String} AND day = (SELECT d FROM end_day)), 0)
          - coalesce((SELECT sumMerge(volume_state) FROM tradernick.hl_trade_history_wallet_daily
                      WHERE wallet = {wallet:String} AND day = (SELECT d FROM start_day)), 0)
          AS volume
    """
    ch = await client()
    rows = await ch.query(sql, parameters={"wallet": wallet, "start": start_dt, "end": end_dt})
    volume = float(rows.result_rows[0][0]) if rows.result_rows else 0.0
    return response.json({
        "wallet": wallet,
        "start": start_dt.date().isoformat(),
        "end": end_dt.date().isoformat(),
        "volume": volume,
    })


# ── SmartSelector presets ────────────────────────────────────────────
# Persistence layer for "criteria groups" — a saved SmartSelectorState
# (lookback / top_n / scope / sort_by / criteria[…]) under a name. Lets
# the user dial in a filter on one chart and reuse it elsewhere.

_PRESET_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9._\- ]{1,80}$")


@bp.get("/hyperliquid/smart_selector_presets")
async def smart_selector_presets_list(request):
    """List all saved SmartSelector presets. Returns
    [{name, config, updated_at}], sorted alphabetically by name. Cheap —
    no JOIN, no FINAL on a multi-row read (FINAL still adds a merge pass
    over the table; small table, no problem). The frontend dropdown
    consumes this directly."""
    ch = await client()
    rows = await ch.query(
        "SELECT name, config, toString(updated_at) AS updated_at "
        "FROM tradernick.smart_selector_presets FINAL "
        "ORDER BY name")
    return response.json({
        "presets": [
            {"name": r[0], "config": r[1], "updated_at": r[2]}
            for r in rows.result_rows
        ],
    })


@bp.post("/hyperliquid/smart_selector_presets")
async def smart_selector_presets_save(request):
    """Upsert a preset. Body: {name, config}. `config` is the JSON-
    encoded SmartSelectorState — we validate it round-trips through
    SmartSelector.from_json (with a placeholder token to satisfy
    token-scope criteria) so we don't persist garbage.
    ReplacingMergeTree dedupes on `name`; the inserted row carries
    `updated_at = now()`."""
    body = request.json or {}
    name = body.get("name")
    config = body.get("config")
    if not isinstance(name, str) or not _PRESET_NAME_RE.match(name):
        return response.json({"error": "name must be 1-80 chars [A-Za-z0-9._- ]"}, status=400)
    if not isinstance(config, str):
        return response.json({"error": "config must be a JSON string"}, status=400)
    # Validate config can be parsed as a SmartSelectorState. Use a
    # placeholder token so token-scope criteria don't trip the "no token
    # supplied" check.
    try:
        SmartSelector.from_json(config, token="__validate__")
    except ValueError as e:
        return response.json({"error": f"invalid selector config: {e}"}, status=400)
    ch = await client()
    # ch.command() doesn't accept VALUES rows the way ch.insert() does —
    # use the typed insert helper instead. ReplacingMergeTree dedupes
    # on `name` so repeated saves overwrite cleanly (background merge).
    await ch.insert(
        "smart_selector_presets",
        [(name, config)],
        column_names=["name", "config"],
    )
    return response.json({"ok": True, "name": name})


@bp.delete("/hyperliquid/smart_selector_presets/<name>")
async def smart_selector_presets_delete(request, name: str):
    """Remove a preset by name. ALTER … DELETE is the standard
    MergeTree-family delete; runs as a background mutation but the row
    becomes invisible immediately. No-op if the name doesn't exist."""
    # Sanic doesn't auto-decode dynamic path params, so a name with a
    # space arrives as "BTC%20whales". Decode before the regex check.
    from urllib.parse import unquote
    name = unquote(name)
    if not _PRESET_NAME_RE.match(name):
        return response.json({"error": "invalid name"}, status=400)
    ch = await client()
    await ch.command(
        "DELETE FROM tradernick.smart_selector_presets "
        "WHERE name = {name:String}",
        parameters={"name": name})
    return response.json({"ok": True, "name": name})


@bp.get("/hyperliquid/oi_split")
@throttled("heavy")
async def oi_split(request):
    """Per-bucket Open Interest on HL, split into long / short / total.

    Same state-aware aggregation pattern as /hyperliquid/unrealized_pnl:
    per-wallet argMax(*, time) per (bucket, wallet, side) collapses to
    one row per snapshot before summing across wallets — avoids
    double-counting carry-forward position rows.

    Returns BOTH token-unit totals (long_oi/short_oi/total_oi) and USD
    notional totals (*_value), so the chart can plot either dimension
    without a second request.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    # Pick the coarsest MV whose bucket divides the query interval — the
    # smaller the source table, the cheaper the argMaxMerge. 1h MV for
    # 1h/4h/1d, 15m MV for 15m/30m, raw for 1m/5m.
    if seconds >= 3600 and seconds % 3600 == 0:
        mv_table = "tradernick.hl_position_history_1h"
    elif seconds >= 900 and seconds % 900 == 0:
        mv_table = "tradernick.hl_position_history_15m"
    else:
        mv_table = None
    if mv_table is not None:
        sql = f"""
            SELECT
                toUnixTimestamp(bucket)                AS bucket,
                sumIf(latest_amount, side='long')      AS long_oi,
                sumIf(latest_amount, side='short')     AS short_oi,
                sum(latest_amount)                     AS total_oi,
                sumIf(latest_size,   side='long')      AS long_oi_value,
                sumIf(latest_size,   side='short')     AS short_oi_value,
                sum(latest_size)                       AS total_oi_value
            FROM (
                SELECT
                    toStartOfInterval(bucket, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                    wallet, side,
                    argMaxMerge(amount_state) AS latest_amount,
                    argMaxMerge(size_state)   AS latest_size
                FROM {mv_table}
                WHERE token = {{token:String}}
                  AND bucket >= {{since:DateTime}}
                  AND bucket <  {{until:DateTime}}
                GROUP BY bucket, wallet, side
            )
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {{limit:UInt32}}
        """
    else:
        sql = """
            SELECT
                toUnixTimestamp(bucket)                AS bucket,
                sumIf(latest_amount, side='long')      AS long_oi,
                sumIf(latest_amount, side='short')     AS short_oi,
                sum(latest_amount)                     AS total_oi,
                sumIf(latest_size,   side='long')      AS long_oi_value,
                sumIf(latest_size,   side='short')     AS short_oi_value,
                sum(latest_size)                       AS total_oi_value
            FROM (
                SELECT
                    toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND) AS bucket,
                    wallet, side,
                    argMax(amount, time) AS latest_amount,
                    argMax(size,   time) AS latest_size
                FROM tradernick.hl_position_history
                WHERE token = {token:String}
                  AND time >= {since:DateTime}
                  AND time <  {until:DateTime}
                GROUP BY bucket, wallet, side
            )
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {limit:UInt32}
        """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "long_oi": float(r[1]),
            "short_oi": float(r[2]),
            "total_oi": float(r[3]),
            "long_oi_value": float(r[4]),
            "short_oi_value": float(r[5]),
            "total_oi_value": float(r[6]),
        }
        for r in rows.result_rows
    ]
    return response.json({"token": token, "interval": interval, "series": series})


@bp.get("/hyperliquid/bridge_flows")
async def bridge_flows(request):
    """Per-bucket USDC flow across the HL Arbitrum bridge: deposit (in),
    withdrawal (out), and net = deposit - withdrawal.

    Both deposit and withdrawal are returned as POSITIVE magnitudes so
    the operator can compare the two side-by-side. Net is the signed
    difference and floats through zero showing the directional bias.
    """
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sumIf(amount, direction='deposit')     AS deposit,
            sumIf(amount, direction='withdrawal')  AS withdrawal,
            sumIf(amount, direction='deposit')
              - sumIf(amount, direction='withdrawal') AS net,
            countIf(direction='deposit')           AS deposit_count,
            countIf(direction='withdrawal')        AS withdrawal_count
        FROM tradernick.hl_transfers FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "deposit": float(r[1]),
            "withdrawal": float(r[2]),
            "net": float(r[3]),
            "deposit_count": int(r[4]),
            "withdrawal_count": int(r[5]),
        }
        for r in rows.result_rows
    ]
    return response.json({"interval": interval, "series": series})


@bp.get("/hyperliquid/vault_flow")
async def vault_flow(request):
    """Per-bucket vault deposit / withdraw / net flow (USDC).

    Mirrors the bridge_flows shape — deposit and withdraw are both
    positive magnitudes for easy side-by-side comparison; net is the
    signed difference (deposit - withdraw). distribution is included
    as a fourth field but the chart only plots the three core lines.
    """
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sumIf(amount, action='deposit')      AS deposit,
            sumIf(amount, action='withdraw')     AS withdraw,
            sumIf(amount, action='deposit')
              - sumIf(amount, action='withdraw') AS net,
            sumIf(amount, action='distribution') AS distribution,
            countIf(action='deposit')            AS deposit_count,
            countIf(action='withdraw')           AS withdraw_count
        FROM tradernick.hl_vaults FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "deposit": float(r[1]),
            "withdraw": float(r[2]),
            "net": float(r[3]),
            "distribution": float(r[4]),
            "deposit_count": int(r[5]),
            "withdraw_count": int(r[6]),
        }
        for r in rows.result_rows
    ]
    return response.json({"interval": interval, "series": series})


_VAULT_SORT_KEYS = {
    "net":          "net DESC",
    "deposits":     "deposits DESC",
    "withdrawals":  "withdrawals DESC",
    "commission":   "commission DESC",
    "total_pnl":    "total_pnl DESC",
    "realized_pnl": "realized_pnl DESC",
    "roe":          "roe DESC",
}


# Shared SQL fragments — vault performance JOINs against position_history
# (open notional + unrealized PnL at the latest snapshot) and trade_history
# (realized PnL + trade volume over the window). Both wrapped in CTEs
# so the main top_vaults / vault_detail queries can LEFT JOIN without
# duplicating the aggregation logic.
#
# `vault_positions` uses a recent 30-min window to identify "currently
# open" positions — position_history is carry-forward so any open
# position appears in every snapshot. argMax-per-(wallet,token,side)
# then collapses to one row per slot (latest values), and the outer
# sum aggregates per wallet across all tokens / both sides.
_VAULT_PERF_CTE = """
  vault_positions AS (
    SELECT wallet AS vault,
      sum(latest_size) AS open_notional,
      sum(latest_upnl) AS unrealized_pnl
    FROM (
      SELECT wallet, token, side,
        argMax(size, time)            AS latest_size,
        argMax(unrealized_pnl, time)  AS latest_upnl
      FROM tradernick.hl_position_history FINAL
      WHERE time >= now() - INTERVAL 30 MINUTE
      GROUP BY wallet, token, side
    )
    GROUP BY wallet
  ),
  vault_realized AS (
    -- GLOBAL realized_pnl over the window = snapshot(until_day) − snapshot(since)
    -- summed across all tokens, read from the pre-aggregated per-(day,wallet)
    -- rollup (hl_trade_history_wallet_daily, HIP3 excluded) via two single-
    -- partition reads at the latest day ≤ each bound (dense-to-now snapshots),
    -- + a current-day realized/fees tail from hl_fills (HIP3-excluded to match).
    -- trade_volume / trade_count are at the last daily snapshot (≤24h stale).
    SELECT v.vault AS vault,
      v.realized_pnl + coalesce(tl.t_pnl, 0) - coalesce(tl.t_fee, 0) AS realized_pnl,
      v.trade_volume      AS trade_volume,
      v.trade_count_total AS trade_count_total
    FROM (
      SELECT e.wallet AS vault,
        e.net_pnl     - coalesce(s.net_pnl, 0)     AS realized_pnl,
        e.volume      - coalesce(s.volume, 0)      AS trade_volume,
        e.trade_count - coalesce(s.trade_count, 0) AS trade_count_total
      FROM (
        SELECT wallet, sumMerge(net_pnl_state) AS net_pnl,
               sumMerge(volume_state) AS volume, sumMerge(trade_count_state) AS trade_count
        FROM tradernick.hl_trade_history_wallet_daily
        WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= toDate({until:DateTime}))
        GROUP BY wallet
      ) e
      LEFT JOIN (
        SELECT wallet, sumMerge(net_pnl_state) AS net_pnl,
               sumMerge(volume_state) AS volume, sumMerge(trade_count_state) AS trade_count
        FROM tradernick.hl_trade_history_wallet_daily
        WHERE day = (SELECT max(day) FROM tradernick.hl_trade_history_wallet_daily WHERE day <= toDate({since:DateTime}))
        GROUP BY wallet
      ) s ON s.wallet = e.wallet
    ) v
    LEFT JOIN (
      SELECT wallet, sum(closed_pnl) AS t_pnl, sum(fee) AS t_fee
      FROM tradernick.hl_fills FINAL
      WHERE time > toStartOfDay({until:DateTime}) AND time <= {until:DateTime}
        AND position(token, ':') = 0
      GROUP BY wallet
    ) tl ON tl.wallet = v.vault
  )
"""


@bp.get("/hyperliquid/top_vaults")
async def top_vaults(request):
    """Leaderboard of vaults over a [since, until] window. Sort key
    selects which metric ranks the table.

    Returns per-vault aggregates: deposits / withdrawals / net /
    commission earned by leader / distributions paid to LPs /
    distinct LP count / event count / age (first event in window).
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "20"))
    order_by = request.args.get("order_by", "net")
    if order_by not in _VAULT_SORT_KEYS:
        return response.json({"error": f"order_by must be one of {list(_VAULT_SORT_KEYS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = f"""
        WITH
          vault_stats AS (
            SELECT
              vault,
              sumIf(amount, action='deposit')      AS deposits,
              sumIf(amount, action='withdraw')     AS withdrawals,
              sumIf(amount, action='deposit')
                - sumIf(amount, action='withdraw') AS net,
              sumIf(commission, action='withdraw') AS commission,
              sumIf(amount, action='distribution') AS distributions,
              uniqExact(wallet)                    AS lp_count,
              count()                              AS event_count
            FROM tradernick.hl_vaults FINAL
            WHERE time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
            GROUP BY vault
          ),
          {_VAULT_PERF_CTE}
        SELECT
          s.vault, s.deposits, s.withdrawals, s.net,
          s.commission, s.distributions, s.lp_count, s.event_count,
          COALESCE(p.open_notional, 0)                                 AS open_notional,
          COALESCE(p.unrealized_pnl, 0)                                AS unrealized_pnl,
          COALESCE(r.realized_pnl, 0)                                  AS realized_pnl,
          COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)  AS total_pnl,
          COALESCE(r.trade_volume, 0)                                  AS trade_volume,
          COALESCE(r.trade_count_total, 0)                             AS trade_count_total,
          -- RoE = total PnL / open notional × 100. Open notional is the
          -- proxy for "capital deployed right now". Returns 0 when no
          -- positions are open (commonly: vaults that only have deposit/
          -- withdraw activity, no actual trading).
          if(p.open_notional > 0,
             (COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)) / p.open_notional * 100,
             0)                                                        AS roe
        FROM vault_stats s
        LEFT JOIN vault_positions p ON p.vault = s.vault
        LEFT JOIN vault_realized  r ON r.vault = s.vault
        ORDER BY {_VAULT_SORT_KEYS[order_by]}
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    vaults = [
        {
            "rank": idx + 1,
            "vault": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "commission": float(r[4]),
            "distributions": float(r[5]),
            "lp_count": int(r[6]),
            "event_count": int(r[7]),
            "open_notional": float(r[8]),
            "unrealized_pnl": float(r[9]),
            "realized_pnl": float(r[10]),
            "total_pnl": float(r[11]),
            "trade_volume": float(r[12]),
            "trade_count_total": int(r[13]),
            "roe": float(r[14]),
        }
        for idx, r in enumerate(rows.result_rows)
    ]
    return response.json({
        "order_by": order_by, "since": since, "until": until,
        "vaults": vaults,
    })


@bp.get("/hyperliquid/top_vault_lps")
async def top_vault_lps(request):
    """Top LPs by net USDC deposited into HL vaults over [since, until].

    Action filter to deposit + withdraw (distribution is the vault paying
    its LPs and 'create' is the vault leader's own initial seed, neither
    is an LP action). Categories surfaces wallet labels for whale tagging.
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "20"))
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            wallet,
            sumIf(amount, action='deposit')      AS deposits,
            sumIf(amount, action='withdraw')     AS withdrawals,
            sumIf(amount, action='deposit')
              - sumIf(amount, action='withdraw') AS net,
            uniqExact(vault)                     AS vaults_used,
            count()                              AS event_count,
            dictGet('tradernick.wallet_labels', 'categories', lower(wallet)) AS categories
        FROM tradernick.hl_vaults FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
          AND action IN ('deposit', 'withdraw')
        GROUP BY wallet
        ORDER BY net DESC
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    lps = [
        {
            "rank": idx + 1,
            "wallet": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "vaults_used": int(r[4]),
            "event_count": int(r[5]),
            "categories": list(r[6]) if r[6] else [],
        }
        for idx, r in enumerate(rows.result_rows)
    ]
    return response.json({
        "since": since, "until": until,
        "lps": lps,
    })


@bp.get("/hyperliquid/vault_detail")
async def vault_detail(request):
    """Top-N vaults by gross flow + each vault's most-recent activity
    log in a single response. Mirrors the top_positions UX so flipping
    the vault selector on the dashboard is instant (no re-fetch).

    Per-event row includes time / action / wallet (LP) / amount /
    commission / fee — the full event detail. Activity list is capped
    at recent_n per vault (default 50) so the response stays bounded
    even with very active vaults.
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10"))
    recent_n = int(request.args.get("recent_n", "50"))
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    ch = await client()

    # Step 1 — top-N vaults by gross flow, plus perf metrics from the
    # shared CTEs (open notional / UPnL / realized PnL / trade volume).
    top_rows = await ch.query(
        f"""
        WITH
          vault_stats AS (
            SELECT
                vault,
                sumIf(amount, action='deposit')      AS deposits,
                sumIf(amount, action='withdraw')     AS withdrawals,
                sumIf(amount, action='deposit')
                  - sumIf(amount, action='withdraw') AS net,
                sumIf(commission, action='withdraw') AS commission,
                sumIf(amount, action='distribution') AS distributions,
                uniqExact(wallet)                    AS lp_count,
                count()                              AS event_count,
                toUnixTimestamp(min(time))           AS first_event_at,
                toUnixTimestamp(max(time))           AS last_event_at
            FROM tradernick.hl_vaults FINAL
            WHERE time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
            GROUP BY vault
            ORDER BY (sumIf(amount, action='deposit') + sumIf(amount, action='withdraw')) DESC
            LIMIT {{limit:UInt32}}
          ),
          {_VAULT_PERF_CTE}
        SELECT
          s.vault, s.deposits, s.withdrawals, s.net,
          s.commission, s.distributions, s.lp_count, s.event_count,
          s.first_event_at, s.last_event_at,
          COALESCE(p.open_notional, 0)                                AS open_notional,
          COALESCE(p.unrealized_pnl, 0)                               AS unrealized_pnl,
          COALESCE(r.realized_pnl, 0)                                 AS realized_pnl,
          COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0) AS total_pnl,
          COALESCE(r.trade_volume, 0)                                 AS trade_volume,
          COALESCE(r.trade_count_total, 0)                            AS trade_count_total,
          if(p.open_notional > 0,
             (COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)) / p.open_notional * 100,
             0)                                                       AS roe
        FROM vault_stats s
        LEFT JOIN vault_positions p ON p.vault = s.vault
        LEFT JOIN vault_realized  r ON r.vault = s.vault
        ORDER BY (s.deposits + s.withdrawals) DESC
        """,
        parameters={"since": since_dt, "until": until_dt, "limit": limit},
    )
    if not top_rows.result_rows:
        return response.json({"since": since, "until": until, "vaults": []})

    vault_list = [r[0] for r in top_rows.result_rows]
    vault_meta: dict = {
        r[0]: {
            "vault": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "commission": float(r[4]),
            "distributions": float(r[5]),
            "lp_count": int(r[6]),
            "event_count": int(r[7]),
            "first_event_at": int(r[8]),
            "last_event_at": int(r[9]),
            "open_notional": float(r[10]),
            "unrealized_pnl": float(r[11]),
            "realized_pnl": float(r[12]),
            "total_pnl": float(r[13]),
            "trade_volume": float(r[14]),
            "trade_count_total": int(r[15]),
            "roe": float(r[16]),
            "events": [],
        }
        for r in top_rows.result_rows
    }

    # Step 2 — pull the last recent_n events per top vault. Using
    # row_number() partitioned by vault gives a bounded per-vault tail
    # without a separate query per vault.
    event_rows = await ch.query(
        """
        SELECT vault, ts, wallet, action, amount, commission, fee FROM (
            SELECT
                vault,
                toUnixTimestamp(time) AS ts,
                wallet, action, amount, commission, fee,
                row_number() OVER (PARTITION BY vault ORDER BY time DESC) AS rn
            FROM tradernick.hl_vaults FINAL
            WHERE vault IN {vaults:Array(String)}
              AND time >= {since:DateTime}
              AND time <  {until:DateTime}
        )
        WHERE rn <= {recent_n:UInt32}
        ORDER BY vault, ts DESC
        """,
        parameters={
            "vaults": vault_list, "since": since_dt, "until": until_dt,
            "recent_n": recent_n,
        },
    )
    for r in event_rows.result_rows:
        v = r[0]
        if v not in vault_meta:
            continue
        vault_meta[v]["events"].append({
            "time": int(r[1]),
            "wallet": r[2],
            "action": r[3],
            "amount": float(r[4]),
            "commission": float(r[5]),
            "fee": float(r[6]),
        })

    vaults_out = []
    for rank, v in enumerate(vault_list, start=1):
        entry = vault_meta[v]
        entry["rank"] = rank
        vaults_out.append(entry)

    return response.json({
        "since": since, "until": until,
        "vaults": vaults_out,
    })


@bp.get("/hyperliquid/top_positions")
@throttled("heavy")
async def top_positions(request):
    """Top 10 wallets by current unrealized PnL, plus each wallet's full
    position breakdown (all tokens, both sides).

    `token` is optional:
      - specified: rank wallets by their unrealized_pnl in that token
        (sum over sides)
      - omitted:  rank wallets by sum(unrealized_pnl) across ALL tokens

    Either way, the per-wallet `positions` list contains every open
    position the wallet currently holds — not just the ranked-on token.
    That lets the dashboard show the full portfolio of a top trader
    even when filtered to a single token's leaderboard.

    'Current' = the most recent snapshot in the table (capped at the
    last 2h). Within a 1h lookback window we argMax to the latest row
    per (wallet, token, side) — handles wallets whose latest tick is
    a minute or two stale without missing them.
    """
    token = request.args.get("token") or None
    limit = int(request.args.get("limit", "10"))

    # The latest_t lookup is intentionally scoped to the last 2h so a
    # stale table (e.g. fresh restart, gap-fill behind) doesn't claim
    # 'current' from days ago.
    latest_where = ["time >= now() - INTERVAL 2 HOUR"]
    latest_params: dict = {}
    if token:
        latest_where.append("token = {token:String}")
        latest_params["token"] = token

    ch = await client()
    latest_rows = await ch.query(
        f"""
        SELECT toUnixTimestamp(max(time)) AS t
        FROM tradernick.hl_position_history FINAL
        WHERE {' AND '.join(latest_where)}
        """,
        parameters=latest_params,
    )
    if not latest_rows.result_rows or not latest_rows.result_rows[0][0]:
        return response.json({
            "token": token, "as_of": None, "wallets": [],
            "note": "no recent position_history data — backfill or live tick not caught up yet",
        })
    as_of = int(latest_rows.result_rows[0][0])

    # Combined query — top-N + their full positions in one pass via JOIN
    # against the same per-(wallet,token,side) latest set.
    where_p = [
        "time >= toDateTime({as_of:UInt32}) - INTERVAL 1 HOUR",
        "time <= toDateTime({as_of:UInt32})",
    ]
    params: dict = {"as_of": as_of, "limit": limit}
    rank_filter = ""
    if token:
        rank_filter = "WHERE token = {token:String}"
        params["token"] = token

    sql = f"""
        WITH positions AS (
            SELECT wallet, token, side,
                   argMax(unrealized_pnl, time) AS unrealized_pnl,
                   argMax(size, time)           AS size,
                   argMax(amount, time)         AS amount,
                   argMax(avg_entry, time)      AS avg_entry,
                   argMax(mark_price, time)     AS mark_price,
                   argMax(funding, time)        AS funding,
                   argMax(fee, time)            AS fee,
                   toUnixTimestamp(argMax(opened_at, time)) AS opened_at,
                   toUnixTimestamp(max(time))               AS row_as_of
            FROM tradernick.hl_position_history FINAL
            WHERE {' AND '.join(where_p)}
            GROUP BY wallet, token, side
        ),
        ranked AS (
            SELECT wallet, sum(unrealized_pnl) AS score
            FROM positions
            {rank_filter}
            GROUP BY wallet
            ORDER BY score DESC
            LIMIT {{limit:UInt32}}
        )
        SELECT
            r.wallet,
            r.score,
            dictGet('tradernick.wallet_labels', 'categories', lower(r.wallet)) AS categories,
            p.token, p.side, p.unrealized_pnl, p.size, p.amount,
            p.avg_entry, p.mark_price, p.funding, p.fee, p.opened_at, p.row_as_of
        FROM ranked r
        LEFT JOIN positions p ON p.wallet = r.wallet
        ORDER BY r.score DESC, abs(p.unrealized_pnl) DESC
    """

    rows = await ch.query(sql, parameters=params)

    # Group rows by wallet preserving the score-DESC order
    by_wallet: dict = {}
    order: list[str] = []
    for r in rows.result_rows:
        w = r[0]
        if w not in by_wallet:
            by_wallet[w] = {
                "wallet": w,
                "score_unrealized_pnl": float(r[1]),
                "categories": list(r[2]) if r[2] else [],
                "positions": [],
            }
            order.append(w)
        # r[3..] = per-position fields; some wallets may have empty positions
        # if the LEFT JOIN landed a NULL row (no positions in window) — skip.
        if r[3] is None:
            continue
        by_wallet[w]["positions"].append({
            "token": r[3],
            "side": r[4],
            "unrealized_pnl": float(r[5]),
            "size": float(r[6]),
            "amount": float(r[7]),
            "avg_entry": float(r[8]),
            "mark_price": float(r[9]),
            "funding": float(r[10]),
            "fee": float(r[11]),
            "opened_at": int(r[12]) if r[12] is not None else None,
            "as_of": int(r[13]) if r[13] is not None else as_of,
        })

    wallets = []
    for rank, w in enumerate(order, start=1):
        entry = by_wallet[w]
        entry["rank"] = rank
        wallets.append(entry)

    return response.json({
        "token": token,
        "as_of": as_of,
        "wallets": wallets,
    })


@bp.get("/hyperliquid/unrealized_pnl")
@throttled("heavy")
async def unrealized_pnl(request):
    """Per-bucket unrealized PnL totals for a token (long + short + net).

    position_history rows are STATE (mark-to-market at each 5m snapshot),
    not flow events — so summing raw rows across multiple snapshots in a
    bucket double-counts. The inner sub-query collapses to one row per
    (bucket, wallet, side) by taking argMax(unrealized_pnl, time), then
    the outer aggregate sums across wallets.

    Returns long_pnl / short_pnl / net_pnl plus wallet counts so the
    chart can show 'how many longs are underwater' tooltips later.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    wallet = request.args.get("wallet")

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)
    params: dict = {
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    }
    # Same MV-selection logic as /oi_split: 1h MV for 1h/4h/1d, 15m MV for
    # 15m/30m, raw for 1m/5m. MV time column is `bucket`, raw is `time`.
    if seconds >= 3600 and seconds % 3600 == 0:
        mv_table = "tradernick.hl_position_history_1h"
    elif seconds >= 900 and seconds % 900 == 0:
        mv_table = "tradernick.hl_position_history_15m"
    else:
        mv_table = None
    time_col = "bucket" if mv_table is not None else "time"
    inner_where = [
        "token = {token:String}",
        f"{time_col} >= {{since:DateTime}}",
        f"{time_col} <  {{until:DateTime}}",
    ]
    if wallet:
        inner_where.append("lower(wallet) = {wallet:String}")
        params["wallet"] = wallet.lower()
    inner_where_sql = " AND ".join(inner_where)

    if mv_table is not None:
        sql = f"""
            SELECT
                toUnixTimestamp(bucket)         AS bucket,
                sumIf(latest_pnl, side='long')  AS long_pnl,
                sumIf(latest_pnl, side='short') AS short_pnl,
                sum(latest_pnl)                 AS net_pnl,
                countIf(side='long')            AS long_wallets,
                countIf(side='short')           AS short_wallets
            FROM (
                SELECT
                    toStartOfInterval(bucket, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                    wallet, side,
                    argMaxMerge(pnl_state) AS latest_pnl
                FROM {mv_table}
                WHERE {inner_where_sql}
                GROUP BY bucket, wallet, side
            )
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {{limit:UInt32}}
        """
    else:
        sql = f"""
            SELECT
                toUnixTimestamp(bucket)         AS bucket,
                sumIf(latest_pnl, side='long')  AS long_pnl,
                sumIf(latest_pnl, side='short') AS short_pnl,
                sum(latest_pnl)                 AS net_pnl,
                countIf(side='long')            AS long_wallets,
                countIf(side='short')           AS short_wallets
            FROM (
                SELECT
                    toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                    wallet, side,
                    argMax(unrealized_pnl, time) AS latest_pnl
                FROM tradernick.hl_position_history
                WHERE {inner_where_sql}
                GROUP BY bucket, wallet, side
            )
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {{limit:UInt32}}
        """

    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {
            "time": int(r[0]),
            "long_pnl": float(r[1]),
            "short_pnl": float(r[2]),
            "net_pnl": float(r[3]),
            "long_wallets": int(r[4]),
            "short_wallets": int(r[5]),
        }
        for r in rows.result_rows
    ]
    body = {"token": token, "interval": interval, "series": series}
    if wallet: body["wallet"] = wallet
    return response.json(body)
