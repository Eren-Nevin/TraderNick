"""GMX V2 events aggregate endpoint. 9 events on ARB.

Chart selector picks (chain, market) where `market` is the human-readable
`market_name` from DeFiStream (e.g. "BTC/USD [WBTC-USDC]"). When `market`
is omitted, the endpoint sums across every market.

Per-event amount semantics:
  - position_increase / position_decrease / liquidation: amount = size_delta_usd
    (positions are USD-denominated; value_usd ~ size_delta_usd)
  - swap: amount = amount_in (raw token-in units), value_usd separate
  - deposit: amount = long_token_amount + short_token_amount (mixed units —
    headline "total deposit size"; if you need per-token, use the raw CH table)
  - withdraw: amount = market_token_amount (GM receipt-token units)
  - funding / borrowing: amount = delta (no value_usd — rate-update events
    don't carry one; we return 0 for sum_value_usd)
  - fees_collected: amount = total_cost_amount, value_usd separate
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import is_chain_group, resolve_chain_group
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("gmx")

# Event → (table, amount-expression, has_value_usd)
# `amount-expression` is the SQL expression summed per bucket — usually a
# single column, sometimes an addition (deposits sum both token sides). When
# has_value_usd=False we synthesise sum_value_usd=0 so the response shape
# stays uniform across events.
_EVENT_TABLES = {
    "position_increase": ("tradernick.gmx_position_increases", "size_delta_usd",                       True),
    "position_decrease": ("tradernick.gmx_position_decreases", "size_delta_usd",                       True),
    "liquidation":       ("tradernick.gmx_liquidations",       "size_delta_usd",                       True),
    "swap":              ("tradernick.gmx_swaps",              "amount_in",                            True),
    # Both deposit + withdraw sum long + short token-units so net_lp is
    # apples-to-apples. For withdrawals the long/short columns hold the
    # 2.19 realized amounts (decimaled, correct USD).
    "deposit":           ("tradernick.gmx_deposits",            "long_token_amount + short_token_amount", True),
    "withdraw":          ("tradernick.gmx_withdrawals",         "long_token_amount + short_token_amount", True),
    "funding":           ("tradernick.gmx_funding",             "delta",                                False),
    "borrowing":         ("tradernick.gmx_borrowing",           "delta",                                False),
    "fees_collected":    ("tradernick.gmx_fees_collected",      "total_cost_amount",                    True),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/gmx/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr, has_value_usd = _EVENT_TABLES[event]

    chain = request.args.get("chain")
    chain_group = request.args.get("chain_group")
    market = request.args.get("market")              # market_name; if absent → sum across all markets
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    if chain_group:
        if not is_chain_group(chain_group):
            return response.json({"error": f"unknown chain_group {chain_group!r}"}, status=400)
        chains = await resolve_chain_group(chain_group)
    elif chain:
        chains = [chain]
    else:
        return response.json({"error": "missing chain (or chain_group)"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    def _q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    if len(chains) == 1:
        where_chain = "chain = {chain:String}"
        extra = {"chain": chains[0]}
    else:
        tup = ", ".join(_q(c) for c in chains)
        where_chain = f"chain IN ({tup})"
        extra = {}

    if market:
        where_market = "AND market_name = {market:String}"
        extra["market"] = market
    else:
        where_market = ""

    value_sum_expr = "sum(coalesce(value_usd, 0))" if has_value_usd else "0"

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_expr})  AS sum_amount,
            {value_sum_expr}    AS sum_value_usd,
            count()             AS count
        FROM {table} FINAL
        WHERE {where_chain}
          {where_market}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "since": since_dt, "until": until_dt,
        "limit": limit, **extra,
    })
    series = [
        {"time": int(r[0]), "sum_amount": float(r[1]), "sum_value_usd": float(r[2]), "count": int(r[3])}
        for r in rows.result_rows
    ]
    body = {"event": event, "interval": interval, "series": series}
    if chain_group:
        body["chain_group"] = chain_group
        body["chains"] = chains
    else:
        body["chain"] = chain
    if market:
        body["market"] = market
    return response.json(body)


@bp.get("/gmx/streams")
async def streams(_request):
    """List distinct (event, chain, market_name) tuples with row counts.
    Cached 60s — the dashboard polls this every page load to populate the
    per-chart market selector."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _a, _v) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, market_name, count() AS rows
            FROM {table} FINAL
            WHERE market_name != ''
            GROUP BY chain, market_name
            ORDER BY chain, rows DESC
        """)
        for chain, m, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "market": m, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
