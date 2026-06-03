"""AAVE v2 events aggregate endpoint. Same shape as /aave/aggregate minus
the eth_market axis (V2 had a single pool per chain). Six event types
back the Lending page's V2 chart kinds."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import (
    is_chain_group, is_token_group, resolve_chain_group, resolve_token_group,
)
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aave_v2")

_EVENT_TABLES = {
    "deposit":     ("tradernick.aave_v2_deposits",     "amount",        None),
    "withdraw":    ("tradernick.aave_v2_withdrawals",  "amount",        None),
    "borrow":      ("tradernick.aave_v2_borrows",      "amount",        None),
    "repay":       ("tradernick.aave_v2_repays",       "amount",        None),
    "flashloan":   ("tradernick.aave_v2_flashloans",   "amount",        None),
    "liquidation": ("tradernick.aave_v2_liquidations", "debt_to_cover", "debt_token"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _parse_csv(s):
    return [v.strip() for v in (s or "").split(",") if v.strip()]


@bp.get("/aave_v2/aggregate")
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
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

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
        FROM {table} FINAL
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


# See aave.py:/aave/wallets/leaderboard for the SQL shape rationale + wallet
# semantics per event. V2 mirrors V3 minus eth_market. Same wallet column
# mapping (V2 contract event sigs match V3).
_LEADERBOARD_EVENTS_V2 = [
    ("tradernick.aave_v2_deposits",     "on_behalf_of", "token",      "deposit"),
    ("tradernick.aave_v2_withdrawals",  "user",         "token",      "withdraw"),
    ("tradernick.aave_v2_borrows",      "on_behalf_of", "token",      "borrow"),
    ("tradernick.aave_v2_repays",       "user",         "token",      "repay"),
    ("tradernick.aave_v2_liquidations", "owner",        "debt_token", "liquidation"),
]
_LEADERBOARD_ORDER_COLS = {
    "deposit":     "deposit_usd",
    "withdraw":    "withdraw_usd",
    "net_deposit": "net_deposit_usd",
    "borrow":      "borrow_usd",
    "repay":       "repay_usd",
    "net_borrow":  "net_borrow_usd",
    "liquidation": "liquidation_usd",
}


def _qlit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


@bp.get("/aave_v2/wallets/leaderboard")
async def leaderboard(request):
    chain = request.args.get("chain")
    token = request.args.get("token")
    chain_group = request.args.get("chain_group")
    token_group = request.args.get("token_group")
    since = request.args.get("since")
    until = request.args.get("until")
    order_by = request.args.get("order_by", "deposit")
    limit = int(request.args.get("limit", "10"))

    if order_by not in _LEADERBOARD_ORDER_COLS:
        return response.json(
            {"error": f"order_by must be one of {list(_LEADERBOARD_ORDER_COLS)}"},
            status=400,
        )
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if limit < 1 or limit > 200:
        return response.json({"error": "limit must be in [1, 200]"}, status=400)

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

    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    legs = []
    for table, wallet_col, token_col, ev_label in _LEADERBOARD_EVENTS_V2:
        pairs = [(c, t) for c in chains for t in tokens]
        tup = ", ".join(f"({_qlit(c)}, {_qlit(t)})" for (c, t) in pairs)
        ckt = f"(chain, {token_col}) IN ({tup})"
        legs.append(f"""
            SELECT {wallet_col} AS wallet, coalesce(value_usd, 0) AS value_usd, '{ev_label}' AS ev
            FROM {table} FINAL
            WHERE {ckt}
              AND time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
        """)
    union = "\nUNION ALL\n".join(legs)
    order_col = _LEADERBOARD_ORDER_COLS[order_by]

    sql = f"""
        WITH events AS (
            {union}
        )
        SELECT
            wallet,
            arrayStringConcat(dictGet('tradernick.wallet_labels', 'categories', lower(wallet)), ',') AS labels,
            sumIf(value_usd, ev = 'deposit')                                              AS deposit_usd,
            countIf(ev = 'deposit')                                                       AS deposit_count,
            sumIf(value_usd, ev = 'withdraw')                                             AS withdraw_usd,
            countIf(ev = 'withdraw')                                                      AS withdraw_count,
            sumIf(value_usd, ev = 'deposit') - sumIf(value_usd, ev = 'withdraw')          AS net_deposit_usd,
            sumIf(value_usd, ev = 'borrow')                                               AS borrow_usd,
            countIf(ev = 'borrow')                                                        AS borrow_count,
            sumIf(value_usd, ev = 'repay')                                                AS repay_usd,
            countIf(ev = 'repay')                                                         AS repay_count,
            sumIf(value_usd, ev = 'borrow') - sumIf(value_usd, ev = 'repay')              AS net_borrow_usd,
            sumIf(value_usd, ev = 'liquidation')                                          AS liquidation_usd,
            countIf(ev = 'liquidation')                                                   AS liquidation_count
        FROM events
        WHERE wallet != ''
        GROUP BY wallet
        ORDER BY {order_col} DESC
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    leaders = []
    for rank, r in enumerate(rows.result_rows, start=1):
        (wallet, labels,
         dep_usd, dep_n, wd_usd, wd_n, nd_usd,
         br_usd, br_n, rp_usd, rp_n, nb_usd,
         lq_usd, lq_n) = r
        leaders.append({
            "rank": rank,
            "wallet": wallet,
            "labels": labels or "",
            "deposit_usd": float(dep_usd),     "deposit_count":     int(dep_n),
            "withdraw_usd": float(wd_usd),     "withdraw_count":    int(wd_n),
            "net_deposit_usd": float(nd_usd),
            "borrow_usd": float(br_usd),       "borrow_count":      int(br_n),
            "repay_usd": float(rp_usd),        "repay_count":       int(rp_n),
            "net_borrow_usd": float(nb_usd),
            "liquidation_usd": float(lq_usd),  "liquidation_count": int(lq_n),
        })

    body = {
        "order_by": order_by,
        "limit": limit,
        "since": since, "until": until,
        "leaders": leaders,
    }
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


@bp.get("/aave_v2/streams")
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
            FROM {table} FINAL
            GROUP BY chain, {token_col}
            ORDER BY chain, {token_col}
        """)
        for chain, tok, n in rows.result_rows:
            out.append({"event": ev, "chain": chain, "token": tok, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
