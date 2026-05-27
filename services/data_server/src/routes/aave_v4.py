"""AAVE v4 events aggregate endpoint. Same shape as /aave/aggregate minus
the eth_market axis (V4 uses spoke/reserve_id instead, but those aren't
selector dimensions — chain + token still drive the chart). 5 events;
liquidations use debt_token/debt_amount as the headline series."""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import (
    is_chain_group, is_token_group, resolve_chain_group, resolve_token_group,
)
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aave_v4")

_EVENT_TABLES = {
    "deposit":     ("tradernick.aave_v4_deposits",     "amount",      None),
    "withdraw":    ("tradernick.aave_v4_withdrawals",  "amount",      None),
    "borrow":      ("tradernick.aave_v4_borrows",      "amount",      None),
    "repay":       ("tradernick.aave_v4_repays",       "amount",      None),
    "liquidation": ("tradernick.aave_v4_liquidations", "debt_amount", "debt_token"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/aave_v4/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_col, token_col_override = _EVENT_TABLES[event]
    token_col = token_col_override or "token"

    chain = request.args.get("chain")
    token = request.args.get("token")
    chain_group = request.args.get("chain_group")
    token_group = request.args.get("token_group")
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
    if token_group:
        if not is_token_group(token_group):
            return response.json({"error": f"unknown token_group {token_group!r}"}, status=400)
        tokens = resolve_token_group(token_group)
    elif token:
        tokens = [token]
    else:
        return response.json({"error": "missing token (or token_group)"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    def _q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    if len(chains) == 1 and len(tokens) == 1:
        ckt_where = f"chain = {{chain:String}} AND {token_col} = {{token:String}}"
        extra = {"chain": chains[0], "token": tokens[0]}
    else:
        pairs = [(c, t) for c in chains for t in tokens]
        tup = ", ".join(f"({_q(c)}, {_q(t)})" for (c, t) in pairs)
        ckt_where = f"(chain, {token_col}) IN ({tup})"
        extra = {}

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_col})           AS sum_amount,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                     AS count
        FROM {table}
        WHERE {ckt_where}
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
    if token_group:
        body["token_group"] = token_group
        body["tokens"] = tokens
    else:
        body["token"] = token
    return response.json(body)


@bp.get("/aave_v4/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _a, token_col_override) in _EVENT_TABLES.items():
        token_col = token_col_override or "token"
        rows = await ch.query(f"""
            SELECT chain, {token_col} AS token, count() AS rows
            FROM {table}
            GROUP BY chain, {token_col}
            ORDER BY chain, {token_col}
        """)
        for chain, tok, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "token": tok, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
