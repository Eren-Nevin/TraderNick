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
from routes.ohlcv import INTERVAL_SECONDS
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


def _build_smart_wallet_selection(request, include_avg_oi: bool = False):
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
    lookback = int(request.args.get("lookback", "7"))
    if lookback not in (1, 7, 30, 90, 150):
        raise ValueError("lookback must be 1|7|30|90|150")
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
    limit = min(int(request.args.get("limit", "100")), 500)
    min_days = max(int(request.args.get("min_days", "3")), 1)
    try:
        min_volume = float(request.args.get("min_volume", "0"))
    except ValueError:
        min_volume = 0.0
    try:
        min_realized = float(request.args.get("min_realized", "0"))
    except ValueError:
        min_realized = 0.0
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
    try:
        min_volume_share = float(request.args.get("min_volume_share", "0"))
    except ValueError:
        min_volume_share = 0.0
    try:
        max_volume_share = float(request.args.get("max_volume_share", str(NO_MAX)))
    except ValueError:
        max_volume_share = NO_MAX

    snap_arg = request.args.get("snapshot")
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
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
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
    build_oi_window = oi_share_active or (include_avg_oi and token is None)
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
    # Share denominators as SCALAR CTEs (WITH (…) AS x): ClickHouse evaluates
    # these once, whereas a `(SELECT … FROM win)` referenced inline in the WHERE
    # re-inlines (re-runs) the whole table CTE on every reference. vol_total is
    # always defined (volume-share guards are always present); oi_total only when
    # oi_window exists.
    cte_block += "\n        , (SELECT sum(volume) FROM win) AS vol_total"
    if build_oi_window:
        cte_block += "\n        , (SELECT sum(oi_sum) FROM oi_window) AS oi_total"

    # Share guards: volume-share is cheap (sum over the existing `win` CTE) so
    # it's always present (a no-op at the 0/NO_MAX defaults); OI-share joins the
    # conditionally-built oi_window. coalesce(…, 0) keeps a 0-share fallback when
    # a denominator is 0, so the defaults never exclude a wallet. The join is
    # added whenever oi_window exists (guards OR the avg_oi column need it).
    oi_share_join = (
        "\n        LEFT JOIN oi_window ow ON ow.wallet = w.wallet"
        if build_oi_window else "")
    oi_share_guard = """
          AND coalesce(10000 * ow.oi_sum / nullIf(oi_total, 0), 0) >= {min_avg_oi_share:Float64}
          AND coalesce(10000 * ow.oi_sum / nullIf(oi_total, 0), 0) <= {max_avg_oi_share:Float64}""" if oi_share_active else ""

    from_where_block = """
        FROM win w
        LEFT JOIN sharpe_agg sa ON sa.wallet = w.wallet
        LEFT JOIN unreal_now u  ON u.wallet = w.wallet
        LEFT JOIN oi_now oi     ON oi.wallet = w.wallet
        LEFT JOIN taker_agg tk  ON tk.wallet = w.wallet
        LEFT JOIN funding_agg fn ON fn.wallet = w.wallet
        LEFT JOIN first_seen fseen ON fseen.wallet = w.wallet""" + oi_share_join + """
        WHERE w.volume >= {min_volume:Float64}
          AND w.realized >= {min_realized:Float64}
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
          AND coalesce(10000 * w.volume / nullIf(vol_total, 0), 0) >= {min_volume_share:Float64}
          AND coalesce(10000 * w.volume / nullIf(vol_total, 0), 0) <= {max_volume_share:Float64}""" + oi_share_guard

    echo = {
        "metric": metric, "order_by": order_by, "token": token,
        "lookback": lookback, "snapshot": end_day.isoformat(),
        "limit": limit, "min_days": min_days, "min_volume": min_volume,
        "min_realized": min_realized, "min_oi": min_oi,
        "min_avg_trade_size": min_avg_trade_size, "min_taker_pct": min_taker_pct,
        "max_fee_pct": max_fee_pct, "max_funding_pct": max_funding_pct,
        "min_account_duration": min_account_duration, "min_tokens": min_tokens,
        "min_win_rate": min_win_rate, "max_trades_per_day": max_trades_per_day,
        "min_trades_per_day": min_trades_per_day,
        "min_avg_oi_share": min_avg_oi_share, "max_avg_oi_share": max_avg_oi_share,
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
    "min_oi", "min_avg_trade_size", "min_taker_pct", "max_fee_pct",
    "max_funding_pct", "min_account_duration", "min_tokens", "min_win_rate",
    "min_trades_per_day", "max_trades_per_day",
    "min_avg_oi_share", "max_avg_oi_share", "min_volume_share", "max_volume_share",
)
# In-process hint of when we last ensured a key was fresh, to skip the freshness
# round-trip on hot keys. The CH table is the source of truth.
_set_ensured: dict[str, float] = {}


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
      min_oi    — min open interest USD as of the snapshot (default 0)
    """
    try:
        sel = _build_smart_wallet_selection(request, include_avg_oi=True)
    except ValueError as e:
        return response.json({"error": str(e)}, status=400)

    # `count() OVER ()` rides the filtered (pre-LIMIT) set, so total_found is the
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
            toUInt32(uniqExact(wallet))            AS wallet_count
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
        }
        for r in rows.result_rows
    ]
    return response.json({
        "token": oi_token,
        "interval": interval,
        "series": series,
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
            FROM tradernick.hl_position_history
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
