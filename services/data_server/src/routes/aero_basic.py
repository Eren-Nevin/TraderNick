"""Aerodrome basic-pool aggregate endpoint (BASE only). Pool identity:
(chain, sym0, sym1, stable). 4 events (swap/deposit/withdraw/claim).
Per-token sum_amount0/sum_amount1 split on swap via sumIf (basic swaps
store token_sold/token_bought)."""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aero_basic")

_EVENT_TABLES: dict[str, tuple[str, str]] = {
    "swap":     ("tradernick.aero_basic_swaps",       "amount_sold"),
    "deposit":  ("tradernick.aero_basic_deposits",    "amount0 + amount1"),
    "withdraw": ("tradernick.aero_basic_withdrawals", "amount0 + amount1"),
    "claim":    ("tradernick.aero_basic_claims",      "amount0 + amount1"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/aero_basic/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr = _EVENT_TABLES[event]

    chain = request.args.get("chain", "BASE")
    symbol0 = request.args.get("symbol0")
    symbol1 = request.args.get("symbol1")
    stable = request.args.get("stable")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not (chain and symbol0 and symbol1 and stable is not None):
        return response.json({"error": "missing chain/symbol0/symbol1/stable"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    stable_int = 1 if str(stable).lower() in ("1", "true", "s", "stable") else 0
    sym0_u, sym1_u = symbol0.upper(), symbol1.upper()
    if sym0_u > sym1_u:
        return response.json({"error": f"symbol0/symbol1 not canonical; expected {sym1_u}/{sym0_u}"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

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
        amount0_expr = "sum(amount0)"
        amount1_expr = "sum(amount1)"

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_expr})           AS sum_amount,
            {amount0_expr}               AS sum_amount0,
            {amount1_expr}               AS sum_amount1,
            sum(coalesce(value_usd, 0))  AS sum_value_usd,
            count()                      AS count
        FROM {table} FINAL
        WHERE chain    = {{chain:String}}
          AND symbol0  = {{symbol0:String}}
          AND symbol1  = {{symbol1:String}}
          AND stable   = {{stable:UInt8}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "chain": chain.upper(),
        "symbol0": sym0_u, "symbol1": sym1_u, "stable": stable_int,
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
    return response.json({
        "event": event, "chain": chain.upper(),
        "symbol0": sym0_u, "symbol1": sym1_u, "stable": bool(stable_int),
        "interval": interval, "series": series,
    })


@bp.get("/aero_basic/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, symbol0, symbol1, stable, count() AS rows
            FROM {table} FINAL
            GROUP BY chain, symbol0, symbol1, stable
            ORDER BY chain, symbol0, symbol1, stable
        """)
        for chain, s0, s1, st, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "symbol0": s0, "symbol1": s1,
                        "stable": bool(st), "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
