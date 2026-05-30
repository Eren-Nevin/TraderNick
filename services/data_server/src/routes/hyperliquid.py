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
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

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
_EVENT_TABLES = {
    # OHLCV is special-cased — has window-bucketed shape, no aggregation.
    "ohlcv":            ("tradernick.hl_ohlcv_1m",        "volume",            "volume",          None,    "sum"),
    # Trades: amount = total volume per bucket (sum), value = sum(price*amount)
    "trades":           ("tradernick.hl_trades",          "amount",            "price*amount",    None,    "sum"),
    # Fills: amount = sum(size), value = sum(price*size) — also expose
    # closed_pnl via the leaderboard route. Wallet column: 'wallet'.
    "fills":            ("tradernick.hl_fills",           "size",              "price*size",      "wallet","sum"),
    # Funding: chart plots the funding RATE (avg per bucket). Positive rate
    # = longs paying shorts; negative = shorts paying longs. HL fires
    # funding hourly so `rate` is the hourly funding rate at that event.
    "funding":          ("tradernick.hl_funding",         "rate",              "rate",            "wallet","avg"),
    # position_history deferred — see note in clickhouse.py HL_EVENTS.
    # Trade history: already pre-aggregated. amount = sum(volume), value = sum(net_pnl).
    "trade_history":    ("tradernick.hl_trade_history",   "volume",            "net_pnl",         "wallet","sum"),
    "transfers":        ("tradernick.hl_transfers",       "amount",            "amount",          "wallet","sum"),
    "vaults":           ("tradernick.hl_vaults",          "amount",            "amount",          "wallet","sum"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/hyperliquid/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr, value_expr, wallet_col, agg_func = _EVENT_TABLES[event]

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


@bp.get("/hyperliquid/streams")
async def streams(_request):
    """Distinct (event, token) tuples with row counts. Cached 60s.
    Powers the per-chart token selector on the /hyperliquid page."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _a, _v, _w) in _EVENT_TABLES.items():
        # transfers + vaults have no token dimension — skip them in streams.
        if ev in ("transfers", "vaults"):
            continue
        rows = await ch.query(f"""
            SELECT token, count() AS rows
            FROM {table}
            WHERE token != ''
            GROUP BY token
            ORDER BY rows DESC
        """)
        for tok, n in rows.result_rows:
            out.append({"event": ev, "token": tok, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})


@bp.get("/hyperliquid/wallets/leaderboard")
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
    where = ["time >= {since:DateTime}", "time <  {until:DateTime}"]
    if token:
        where.append("token = {token:String}")
        params["token"] = token
    where_sql = " AND ".join(where)

    sql = f"""
        SELECT
            wallet,
            sum(net_pnl)  AS net_pnl,
            sum(pnl)      AS pnl,
            sum(fees)     AS fees,
            sum(volume)   AS volume,
            sum(buy_volume) AS buy_volume,
            sum(sell_volume) AS sell_volume,
            sum(trade_count) AS trade_count,
            -- Surface wallet labels (Array(String)) for the badge on the
            -- table chart; empty array for unlabelled wallets.
            dictGet('tradernick.wallet_labels', 'categories', lower(wallet)) AS categories
        FROM tradernick.hl_trade_history
        WHERE {where_sql}
        GROUP BY wallet
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
