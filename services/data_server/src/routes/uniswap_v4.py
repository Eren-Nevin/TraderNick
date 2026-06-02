"""Uniswap V4 events aggregate endpoint.

V4 pool identity is the 6-tuple (chain, sym0, sym1, fee, tick_spacing,
hooks). LP events only emit liquidity_delta (no amount0/amount1), so the
per-token amount columns surface as 0 for those events — the dashboard
hides the Amount-mode toggle accordingly. Swap events look V3-shaped and
get the full per-token sum_amount0/sum_amount1 split via sumIf.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("uniswap_v4")

# event → (table, amount-expression).
_EVENT_TABLES: dict[str, tuple[str, str]] = {
    "swap":       ("tradernick.uniswap_v4_swaps",       "amount_sold"),
    "deposit":    ("tradernick.uniswap_v4_deposits",    "liquidity_delta"),
    "withdraw":   ("tradernick.uniswap_v4_withdrawals", "liquidity_delta"),
    "initialize": ("tradernick.uniswap_v4_initializes", "1"),  # constant per row → count
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/uniswap_v4/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr = _EVENT_TABLES[event]

    chain = request.args.get("chain")
    symbol0 = request.args.get("symbol0")
    symbol1 = request.args.get("symbol1")
    fee = request.args.get("fee")
    tick_spacing = request.args.get("tick_spacing")
    hooks = request.args.get("hooks", "0x0000000000000000000000000000000000000000")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not (chain and symbol0 and symbol1 and fee and tick_spacing):
        return response.json({"error": "missing chain/symbol0/symbol1/fee/tick_spacing"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    try:
        fee_int = int(fee); ts_int = int(tick_spacing)
    except ValueError:
        return response.json({"error": "fee + tick_spacing must be ints"}, status=400)

    sym0_u, sym1_u = symbol0.upper(), symbol1.upper()
    if sym0_u > sym1_u:
        return response.json({"error": f"symbol0/symbol1 not canonical; expected {sym1_u}/{sym0_u}"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    # Per-token amount split — only meaningful for swap (LP events have no
    # amount0/amount1 in V4). Emit 0 for everything else so the response
    # shape stays consistent.
    if event == "swap":
        amount0_expr = (
            "sumIf(amount_sold, token_sold = {symbol0:String}) +"
            " sumIf(amount_bought, token_bought = {symbol0:String})"
        )
        amount1_expr = (
            "sumIf(amount_sold, token_sold = {symbol1:String}) +"
            " sumIf(amount_bought, token_bought = {symbol1:String})"
        )
    else:
        amount0_expr = "0"
        amount1_expr = "0"

    # Pool-creation rows (table `uniswap_v4_initializes`) carry no
    # value_usd column — they're a per-row event count, not a notional
    # transfer. Without this carve-out the SQL referenced value_usd
    # against the initialize table and CH errored with UNKNOWN_IDENTIFIER,
    # surfacing in the chart as a 500. Swap / deposit / withdraw keep the
    # existing sum(coalesce(value_usd,0)) shape so direct-API callers see
    # whatever DeFiStream priced (may still be 0; pricing those properly
    # is a separate item).
    if event == "initialize":
        value_usd_expr = "0"
    else:
        value_usd_expr = "sum(coalesce(value_usd, 0))"

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_expr})           AS sum_amount,
            {amount0_expr}               AS sum_amount0,
            {amount1_expr}               AS sum_amount1,
            {value_usd_expr}             AS sum_value_usd,
            count()                      AS count
        FROM {table} FINAL
        WHERE chain    = {{chain:String}}
          AND symbol0  = {{symbol0:String}}
          AND symbol1  = {{symbol1:String}}
          AND fee      = {{fee:UInt32}}
          AND tick_spacing = {{ts:UInt32}}
          AND hooks    = {{hooks:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "chain": chain.upper(),
        "symbol0": sym0_u, "symbol1": sym1_u,
        "fee": fee_int, "ts": ts_int, "hooks": hooks,
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "sum_amount": float(r[1]),
            "sum_amount0": float(r[2]),
            "sum_amount1": float(r[3]),
            "sum_value_usd": float(r[4]),
            "count": int(r[5]),
        }
        for r in rows.result_rows
    ]
    # Initialize event is a per-row count — surface that as the headline
    # `sum_amount` field too (the dashboard's pcount-style line plotter).
    return response.json({
        "event": event, "chain": chain.upper(),
        "symbol0": sym0_u, "symbol1": sym1_u,
        "fee": fee_int, "tick_spacing": ts_int, "hooks": hooks,
        "interval": interval, "series": series,
    })


@bp.get("/uniswap_v4/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, symbol0, symbol1, fee, tick_spacing, hooks, count() AS rows
            FROM {table} FINAL
            GROUP BY chain, symbol0, symbol1, fee, tick_spacing, hooks
            ORDER BY chain, symbol0, symbol1, fee, tick_spacing
        """)
        for chain, s0, s1, fee, ts, hk, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "symbol0": s0, "symbol1": s1,
                        "fee": int(fee), "tick_spacing": int(ts), "hooks": hk, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
