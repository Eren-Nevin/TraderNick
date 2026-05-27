"""Uniswap V2 events aggregate endpoint. Same shape as /uniswap/aggregate
minus the fee_tier axis and the swap-side directional split (V2 swap
events are just amountSold/amountBought; we still expose per-token
sum_amount0/sum_amount1 so the DeX page can render token0/token1 on
the secondary y-axis in Amount mode)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("uniswap_v2")

_EVENT_TABLES: dict[str, tuple[str, str]] = {
    "swap":     ("tradernick.uniswap_v2_swaps",       "amount_sold"),
    "deposit":  ("tradernick.uniswap_v2_deposits",    "amount0 + amount1"),
    "withdraw": ("tradernick.uniswap_v2_withdrawals", "amount0 + amount1"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/uniswap_v2/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr = _EVENT_TABLES[event]

    chain = request.args.get("chain")
    symbol0 = request.args.get("symbol0")
    symbol1 = request.args.get("symbol1")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not (chain and symbol0 and symbol1):
        return response.json({"error": "missing chain/symbol0/symbol1"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    sym0_u, sym1_u = symbol0.upper(), symbol1.upper()
    if sym0_u > sym1_u:
        return response.json(
            {"error": f"symbol0/symbol1 not in canonical order; got {sym0_u}/{sym1_u} expected {sym1_u}/{sym0_u}"},
            status=400,
        )

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    # Per-token amount columns — V2 swap stores token_sold / token_bought
    # so we reconstruct via sumIf; LP events store amount0 / amount1
    # directly on the row.
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
            sum({amount_expr})          AS sum_amount,
            {amount0_expr}              AS sum_amount0,
            {amount1_expr}              AS sum_amount1,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                     AS count
        FROM {table}
        WHERE chain    = {{chain:String}}
          AND symbol0  = {{symbol0:String}}
          AND symbol1  = {{symbol1:String}}
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
        "symbol0": sym0_u, "symbol1": sym1_u,
        "interval": interval, "series": series,
    })


@bp.get("/uniswap_v2/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, symbol0, symbol1, count() AS rows
            FROM {table}
            GROUP BY chain, symbol0, symbol1
            ORDER BY chain, symbol0, symbol1
        """)
        for chain, sym0, sym1, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "symbol0": sym0, "symbol1": sym1, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
