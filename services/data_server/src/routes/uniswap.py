"""Uniswap V3 events aggregate endpoint.

Drives the six Uniswap chart kinds on the dashboard's DeX page. One
endpoint, four physical tables — the `event` query param picks which.

Pool identity is the 4-tuple (chain, symbol0, symbol1, fee_tier).
DeFiStream stores token0/token1 in canonical (alphabetic) order so the
client must pass them in the same order — we normalise on the wire and
in the ingestion config.

Swap rows have a directional split: the response includes
`sum_value_usd_t0t1` (value moved from token0 → token1, i.e. token0 was
sold) and `sum_value_usd_t1t0`. The dashboard subtracts the two to draw
Net Swap Flow without a second round-trip.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("uniswap")

# event → (table, amount-expression for `sum_amount`)
# Swaps don't have a single "amount" — the user almost always wants value_usd
# anyway. We default sum_amount to amount_sold to give a Float64 the
# frontend can render, but the headline volume is value_usd.
_EVENT_TABLES: dict[str, tuple[str, str]] = {
    "swap":     ("tradernick.uniswap_swaps",       "amount_sold"),
    "deposit":  ("tradernick.uniswap_deposits",    "amount0 + amount1"),
    "withdraw": ("tradernick.uniswap_withdrawals", "amount0 + amount1"),
    "collect":  ("tradernick.uniswap_collects",    "amount0 + amount1"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/uniswap/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr = _EVENT_TABLES[event]

    chain = request.args.get("chain")
    symbol0 = request.args.get("symbol0")
    symbol1 = request.args.get("symbol1")
    fee_tier = request.args.get("fee_tier")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not (chain and symbol0 and symbol1 and fee_tier):
        return response.json({"error": "missing chain/symbol0/symbol1/fee_tier"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    try:
        fee_tier_int = int(fee_tier)
    except ValueError:
        return response.json({"error": "fee_tier must be an int"}, status=400)

    # Pair must be supplied in canonical order — the table is ordered by
    # (chain, symbol0, symbol1, fee_tier) so re-ordering here would silently
    # miss the partition. Reject misordered input instead.
    sym0_u, sym1_u = symbol0.upper(), symbol1.upper()
    if sym0_u > sym1_u:
        return response.json(
            {"error": f"symbol0/symbol1 not in canonical order; got {sym0_u}/{sym1_u} expected {sym1_u}/{sym0_u}"},
            status=400,
        )

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    # Per-token amount columns — let the dashboard plot token0 / token1
    # separately when the user picks Amount mode. LP events have explicit
    # amount0 / amount1 cols on the row; swap rows store amount_sold +
    # amount_bought with token_sold / token_bought tags, so we reconstruct
    # per-token totals via sumIf.
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

    # Swap-specific extra columns: directional value_usd split via sumIf on
    # token_sold == symbol0. Subtraction (t0t1 - t1t0) is Net Swap Flow.
    swap_extra_cols = ""
    if event == "swap":
        swap_extra_cols = (
            ",\n            sumIf(coalesce(value_usd, 0), token_sold = {symbol0:String}) AS sum_value_usd_t0t1"
            ",\n            sumIf(coalesce(value_usd, 0), token_sold = {symbol1:String}) AS sum_value_usd_t1t0"
        )

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_expr})                          AS sum_amount,
            {amount0_expr}                              AS sum_amount0,
            {amount1_expr}                              AS sum_amount1,
            sum(coalesce(value_usd, 0))                 AS sum_value_usd,
            count()                                     AS count{swap_extra_cols}
        FROM {table} FINAL
        WHERE chain    = {{chain:String}}
          AND symbol0  = {{symbol0:String}}
          AND symbol1  = {{symbol1:String}}
          AND fee_tier = {{fee_tier:UInt32}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds,
        "chain": chain.upper(),
        "symbol0": sym0_u,
        "symbol1": sym1_u,
        "fee_tier": fee_tier_int,
        "since": since_dt,
        "until": until_dt,
        "limit": limit,
    })

    # Column order matches the SELECT above:
    #   bucket, sum_amount, sum_amount0, sum_amount1, sum_value_usd, count
    #   [, sum_value_usd_t0t1, sum_value_usd_t1t0]   ← swap only
    if event == "swap":
        series = [
            {
                "time": int(r[0]),
                "sum_amount": float(r[1]),
                "sum_amount0": float(r[2]),
                "sum_amount1": float(r[3]),
                "sum_value_usd": float(r[4]),
                "count": int(r[5]),
                "sum_value_usd_t0t1": float(r[6]),
                "sum_value_usd_t1t0": float(r[7]),
            }
            for r in rows.result_rows
        ]
    else:
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
        "event": event,
        "chain": chain.upper(),
        "symbol0": sym0_u,
        "symbol1": sym1_u,
        "fee_tier": fee_tier_int,
        "interval": interval,
        "series": series,
    })


@bp.get("/uniswap/streams")
async def streams(_request):
    """Distinct (event, chain, symbol0, symbol1, fee_tier) tuples with row
    counts — drives the pool selector on the DeX page. Lazily cached for
    STREAMS_TTL_SECONDS."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for event_key, (table, _amount_expr) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, symbol0, symbol1, fee_tier, count() AS rows
            FROM {table} FINAL
            GROUP BY chain, symbol0, symbol1, fee_tier
            ORDER BY chain, symbol0, symbol1, fee_tier
        """)
        for chain, symbol0, symbol1, fee_tier, n in rows.result_rows:
            out.append({
                "event": event_key,
                "chain": chain,
                "symbol0": symbol0,
                "symbol1": symbol1,
                "fee_tier": int(fee_tier),
                "rows": int(n),
            })
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
