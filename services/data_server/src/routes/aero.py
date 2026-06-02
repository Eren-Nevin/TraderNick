"""Aerodrome concentrated-pool aggregate endpoint (BASE only). Pool
identity: (chain, sym0, sym1, tick_spacing). Same per-token amount split
on swap as Uniswap V3.

USD pricing is computed at query time per-row by ASOF-joining each side
of the pool against tradernick.binance_ohlcv_1m. The ingestion writer
stores value_usd=NULL for every Aero row (DeFiStream doesn't emit a
priced value for this protocol), so the chart used to fall back to
sum(amount0+amount1) — meaningless because it mixes wildly different
token decimals (e.g. USDC at 6 dec + WETH at 18 dec). With per-row
pricing the headline series is honest USD again.

Mirrors the approach in routes/transfers.py and routes/exchange_flow.py
— wrap the event-table SELECT in a subquery so the WHERE filter runs
before the ASOF JOIN (otherwise CH joins every row in the event table
against the OHLCV table). Stablecoins are short-circuited to $1; the
ASOF JOIN only fires for non-stable sides."""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aero")

_EVENT_TABLES: dict[str, str] = {
    "swap":     "tradernick.aero_concentrated_swaps",
    "deposit":  "tradernick.aero_concentrated_deposits",
    "withdraw": "tradernick.aero_concentrated_withdrawals",
    "collect":  "tradernick.aero_concentrated_collects",
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

# Tokens we treat as USD-pegged ($1) — same set as the transfer pipeline.
# Avoids a useless ASOF JOIN against binance_ohlcv_1m for stablecoin sides
# of the pool (USDC/USDT/DAI don't have a Binance spot pair anyway).
_STABLES = frozenset({"USDC", "USDT", "DAI", "USDE", "USDS", "PYUSD"})

# Wrapped + LST variants → their Binance-spot symbol so the ASOF JOIN
# against tradernick.binance_ohlcv_1m (which is keyed by spot symbol,
# e.g. ETH, BTC) actually hits a row. Tokens already at their spot symbol
# (AERO, LINK, …) pass through unchanged via the .get(..., s) below.
_OHLCV_TOKEN_UNWRAP: dict[str, str] = {
    "WETH": "ETH", "STETH": "ETH", "WSTETH": "ETH", "WEETH": "ETH", "RETH": "ETH",
    "WBTC": "BTC", "CBBTC": "BTC",
}


def _ohlcv_token(symbol: str) -> str | None:
    """Return the OHLCV-side token to ASOF-join against, or None if the
    symbol is a stable (price = $1, no join needed)."""
    s = symbol.upper()
    if s in _STABLES:
        return None
    return _OHLCV_TOKEN_UNWRAP.get(s, s)


_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/aero/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table = _EVENT_TABLES[event]

    chain = request.args.get("chain", "BASE")
    symbol0 = request.args.get("symbol0")
    symbol1 = request.args.get("symbol1")
    tick_spacing = request.args.get("tick_spacing")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not (chain and symbol0 and symbol1 and tick_spacing):
        return response.json({"error": "missing chain/symbol0/symbol1/tick_spacing"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    try:
        ts_int = int(tick_spacing)
    except ValueError:
        return response.json({"error": "tick_spacing must be an int"}, status=400)

    sym0_u, sym1_u = symbol0.upper(), symbol1.upper()
    if sym0_u > sym1_u:
        return response.json({"error": f"symbol0/symbol1 not canonical; expected {sym1_u}/{sym0_u}"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    # Build per-side price expressions + the ASOF JOIN clauses. For a
    # stable side: just multiply by 1.0 (no join). For a non-stable side:
    # multiply by coalesce(<alias>.close, 0) and add the ASOF JOIN that
    # binds <alias>.close to the nearest 1m bar <= the row's timestamp.
    # The subquery exposes a constant `pricing_tokenN` column so CH can
    # match the JOIN's equality side against a per-row value (ASOF JOIN
    # requires an equality column alongside the inequality).
    ohlcv0 = _ohlcv_token(sym0_u)
    ohlcv1 = _ohlcv_token(sym1_u)

    join_lines: list[str] = []
    side0_expr: str
    side1_expr: str

    if ohlcv0 is None:
        side0_expr = "amount0 * 1.0"
        ptok0_select = ""  # no virtual column needed
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
        # Swap rows are per-trade with token_sold/token_bought identifying
        # which side moved. Pivot to amount0/amount1 so the per-side price
        # path above just works. The amount0/amount1 here is signed by the
        # swap direction (token_sold → outgoing, token_bought → incoming),
        # so a single trade contributes a positive amount0 OR a positive
        # amount1, never both — which means side0_expr + side1_expr
        # double-counts ~2x net of fees if we add them. Use a single
        # amount * price per row instead: `value_usd = amount_sold *
        # price(token_sold)` is the dollar volume of the trade.
        amount0_expr = (
            "sumIf(amount_sold, token_sold = {symbol0:String}) +"
            " sumIf(amount_bought, token_bought = {symbol0:String})"
        )
        amount1_expr = (
            "sumIf(amount_sold, token_sold = {symbol1:String}) +"
            " sumIf(amount_bought, token_bought = {symbol1:String})"
        )
        # Per-row USD value of a swap: amount_sold * price(token_sold).
        # When token_sold is the stable side, no join is needed (multiply
        # by 1). When it's the non-stable side, multiply by the matching
        # ASOF close. We CASE on token_sold to pick the right side.
        if ohlcv0 is None and ohlcv1 is None:
            # Both sides stable (rare — e.g. USDC/DAI). Pure amount_sold.
            row_value_usd = "amount_sold"
        elif ohlcv0 is None:
            # symbol0 stable. price = $1 when sold-side is symbol0, else p1.close.
            row_value_usd = (
                "amount_sold * if(token_sold = {symbol0:String},"
                " 1.0,"
                " coalesce(p1.close, 0.0))"
            )
        elif ohlcv1 is None:
            # symbol1 stable.
            row_value_usd = (
                "amount_sold * if(token_sold = {symbol1:String},"
                " 1.0,"
                " coalesce(p0.close, 0.0))"
            )
        else:
            # Both non-stable. Pick price by which side was sold.
            row_value_usd = (
                "amount_sold * if(token_sold = {symbol0:String},"
                " coalesce(p0.close, 0.0),"
                " coalesce(p1.close, 0.0))"
            )

        sql = f"""
            SELECT
                toUnixTimestamp(toStartOfInterval(t.time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
                sum(t.amount_sold)           AS sum_amount,
                {amount0_expr.replace('amount_sold', 't.amount_sold').replace('amount_bought', 't.amount_bought').replace('token_sold', 't.token_sold').replace('token_bought', 't.token_bought')} AS sum_amount0,
                {amount1_expr.replace('amount_sold', 't.amount_sold').replace('amount_bought', 't.amount_bought').replace('token_sold', 't.token_sold').replace('token_bought', 't.token_bought')} AS sum_amount1,
                sum({row_value_usd.replace('amount_sold', 't.amount_sold').replace('token_sold', 't.token_sold')}) AS sum_value_usd,
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
                  AND tick_spacing = {{ts:UInt32}}
                  AND time >= {{since:DateTime}}
                  AND time <  {{until:DateTime}}
            ) AS t
            {joins_sql}
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {{limit:UInt32}}
        """
    else:
        # LP events (deposit / withdraw / collect): per-row amount0/amount1.
        # Total USD = side0 + side1; each side is amount × price (stable
        # sides skip the JOIN and multiply by 1).
        #
        # NOTE on `collect`: the endpoint still answers, but the dashboard
        # hides it from the subkind picker. The raw aggregate mixes
        # principal-withdrawn-after-Burn and actual fees, and we tried
        # subtracting matched Burn amounts to recover just fees — that
        # works for same-tx Burn→Collect cycles but is fundamentally
        # broken when an LP burns one day and collects accumulated
        # tokensOwed days later (the Burn is outside any reasonable query
        # window, so the Collect is reported as pure fees when it's
        # really old principal). Use swap_volume × fee_tier for a real
        # "fees" chart; we keep this branch unmodified so direct API
        # consumers still get the raw collect aggregate.
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
                  AND tick_spacing = {{ts:UInt32}}
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
        "symbol0": sym0_u, "symbol1": sym1_u,
        "ts": ts_int,
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
        "tick_spacing": ts_int,
        "interval": interval, "series": series,
    })


@bp.get("/aero/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, table in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, symbol0, symbol1, tick_spacing, count() AS rows
            FROM {table} FINAL
            GROUP BY chain, symbol0, symbol1, tick_spacing
            ORDER BY chain, symbol0, symbol1, tick_spacing
        """)
        for chain, s0, s1, ts, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "symbol0": s0, "symbol1": s1,
                        "tick_spacing": int(ts), "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
