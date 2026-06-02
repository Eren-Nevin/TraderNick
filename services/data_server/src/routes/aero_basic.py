"""Aerodrome basic-pool aggregate endpoint (BASE only). Pool identity:
(chain, sym0, sym1, stable). 4 events (swap/deposit/withdraw/claim).
Per-token sum_amount0/sum_amount1 split on swap via sumIf (basic swaps
store token_sold/token_bought).

USD pricing: identical pattern to routes/aero.py — ingestion stores
value_usd=NULL for every row, so we ASOF-join each side of the pool
against tradernick.binance_ohlcv_1m at query time. See aero.py for the
full rationale (stable short-circuit, wrapped-token unwrap, subquery
ordering)."""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aero_basic")

_EVENT_TABLES: dict[str, str] = {
    "swap":     "tradernick.aero_basic_swaps",
    "deposit":  "tradernick.aero_basic_deposits",
    "withdraw": "tradernick.aero_basic_withdrawals",
    "claim":    "tradernick.aero_basic_claims",
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

# Same stable + wrapped-token lookups as routes/aero.py. Kept inline
# rather than imported so the pricing rules live alongside the SQL that
# uses them (one place to update if a token's spot symbol or the stable
# basket changes).
_STABLES = frozenset({"USDC", "USDT", "DAI", "USDE", "USDS", "PYUSD"})
_OHLCV_TOKEN_UNWRAP: dict[str, str] = {
    "WETH": "ETH", "STETH": "ETH", "WSTETH": "ETH", "WEETH": "ETH", "RETH": "ETH",
    "WBTC": "BTC", "CBBTC": "BTC",
}


def _ohlcv_token(symbol: str) -> str | None:
    s = symbol.upper()
    if s in _STABLES:
        return None
    return _OHLCV_TOKEN_UNWRAP.get(s, s)


_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/aero_basic/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table = _EVENT_TABLES[event]

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

    # Per-side price expression + ASOF JOIN setup — mirrors aero.py.
    ohlcv0 = _ohlcv_token(sym0_u)
    ohlcv1 = _ohlcv_token(sym1_u)

    join_lines: list[str] = []

    if ohlcv0 is None:
        side0_expr = "amount0 * 1.0"
        ptok0_select = ""
    else:
        side0_expr = "amount0 * coalesce(p0.close, 0.0)"
        ptok0_select = f", '{ohlcv0}' AS pricing_token0"
        join_lines.append(
            "ASOF LEFT JOIN tradernick.binance_ohlcv_1m AS p0\n"
            "  ON p0.token = t.pricing_token0 AND p0.time <= t.time"
        )

    if ohlcv1 is None:
        side1_expr = "amount1 * 1.0"
        ptok1_select = ""
    else:
        side1_expr = "amount1 * coalesce(p1.close, 0.0)"
        ptok1_select = f", '{ohlcv1}' AS pricing_token1"
        join_lines.append(
            "ASOF LEFT JOIN tradernick.binance_ohlcv_1m AS p1\n"
            "  ON p1.token = t.pricing_token1 AND p1.time <= t.time"
        )

    joins_sql = "\n        ".join(join_lines)

    if event == "swap":
        # Same pivot + per-row swap pricing as aero.py.
        amount0_expr = (
            "sumIf(t.amount_sold, t.token_sold = {symbol0:String}) +"
            " sumIf(t.amount_bought, t.token_bought = {symbol0:String})"
        )
        amount1_expr = (
            "sumIf(t.amount_sold, t.token_sold = {symbol1:String}) +"
            " sumIf(t.amount_bought, t.token_bought = {symbol1:String})"
        )
        if ohlcv0 is None and ohlcv1 is None:
            row_value_usd = "t.amount_sold"
        elif ohlcv0 is None:
            row_value_usd = (
                "t.amount_sold * if(t.token_sold = {symbol0:String},"
                " 1.0,"
                " coalesce(p1.close, 0.0))"
            )
        elif ohlcv1 is None:
            row_value_usd = (
                "t.amount_sold * if(t.token_sold = {symbol1:String},"
                " 1.0,"
                " coalesce(p0.close, 0.0))"
            )
        else:
            row_value_usd = (
                "t.amount_sold * if(t.token_sold = {symbol0:String},"
                " coalesce(p0.close, 0.0),"
                " coalesce(p1.close, 0.0))"
            )

        sql = f"""
            SELECT
                toUnixTimestamp(toStartOfInterval(t.time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
                sum(t.amount_sold)           AS sum_amount,
                {amount0_expr}               AS sum_amount0,
                {amount1_expr}               AS sum_amount1,
                sum({row_value_usd})         AS sum_value_usd,
                count()                      AS count
            FROM (
                SELECT
                    time, amount_sold, amount_bought, token_sold, token_bought
                    {ptok0_select}
                    {ptok1_select}
                FROM {table} FINAL
                WHERE chain    = {{chain:String}}
                  AND symbol0  = {{symbol0:String}}
                  AND symbol1  = {{symbol1:String}}
                  AND stable   = {{stable:UInt8}}
                  AND time >= {{since:DateTime}}
                  AND time <  {{until:DateTime}}
            ) AS t
            {joins_sql}
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {{limit:UInt32}}
        """
    else:
        # LP events: deposit/withdraw/claim — all carry amount0/amount1.
        sql = f"""
            SELECT
                toUnixTimestamp(toStartOfInterval(t.time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
                sum(t.amount0 + t.amount1)   AS sum_amount,
                sum(t.amount0)               AS sum_amount0,
                sum(t.amount1)               AS sum_amount1,
                sum({side0_expr.replace('amount0', 't.amount0')} + {side1_expr.replace('amount1', 't.amount1')}) AS sum_value_usd,
                count()                      AS count
            FROM (
                SELECT
                    time, amount0, amount1
                    {ptok0_select}
                    {ptok1_select}
                FROM {table} FINAL
                WHERE chain    = {{chain:String}}
                  AND symbol0  = {{symbol0:String}}
                  AND symbol1  = {{symbol1:String}}
                  AND stable   = {{stable:UInt8}}
                  AND time >= {{since:DateTime}}
                  AND time <  {{until:DateTime}}
            ) AS t
            {joins_sql}
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
    for ev, table in _EVENT_TABLES.items():
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
