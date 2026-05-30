"""Lido events aggregate endpoint.

Drives the Staking page's seven chart kinds. One endpoint, five physical
tables — `event` query param picks which.

Pool / token axes don't apply here: a Lido event is keyed by (chain, time)
alone. Mainnet events live on ETH; L2 events on the bridge-deployed L2s.
The dashboard's Net Stake (deposit − withdrawal_claimed) and Net L2 (l2_deposit
− l2_withdrawal_request) charts fire two parallel /lido/aggregate calls and
subtract on the client — same pattern as AAVE's Net Deposit / Net Borrow.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import is_chain_group, resolve_chain_group
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("lido")

# event → (table, amount-expression). All Lido tables expose a single
# headline amount field (minted_amount, burned_amount, withdraw_amount) —
# we surface it as `sum_amount` server-side so the dashboard's renderer
# stays kind-agnostic.
_EVENT_TABLES: dict[str, tuple[str, str]] = {
    "deposit":               ("tradernick.lido_deposits",                "minted_amount"),
    "withdrawal_request":    ("tradernick.lido_withdrawal_requests",     "burned_amount"),
    "withdrawal_claimed":    ("tradernick.lido_withdrawal_claims",       "withdraw_amount"),
    "l2_deposit":            ("tradernick.lido_l2_deposits",             "minted_amount"),
    "l2_withdrawal_request": ("tradernick.lido_l2_withdrawal_requests",  "burned_amount"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/lido/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_col = _EVENT_TABLES[event]

    chain = request.args.get("chain")
    chain_group = request.args.get("chain_group")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    # Resolve chain axis: single chain OR a server-defined group (e.g. EVM,
    # All). When grouped, predicate becomes `chain IN (...)` so the sum
    # spans every chain in the group that has rows for this event.
    if chain_group:
        if not is_chain_group(chain_group):
            return response.json({"error": f"unknown chain_group {chain_group!r}"}, status=400)
        chains = await resolve_chain_group(chain_group)
    elif chain:
        chains = [chain.upper()]
    else:
        return response.json({"error": "missing chain (or chain_group)"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    if len(chains) == 1:
        chain_where = "chain = {chain:String}"
        ch_extra = {"chain": chains[0]}
    else:
        # Build a literal `IN (...)` — `chains` comes from the server-side
        # group catalogue (never raw user input) but we still defensively
        # quote-escape each element.
        def _q(s: str) -> str:
            return "'" + s.replace("'", "''") + "'"
        chain_where = f"chain IN ({', '.join(_q(c) for c in chains)})"
        ch_extra = {}

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_col})                          AS sum_amount,
            sum(coalesce(value_usd, 0))                AS sum_value_usd,
            count()                                    AS count
        FROM {table} FINAL
        WHERE {chain_where}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds,
        "since": since_dt,
        "until": until_dt,
        "limit": limit,
        **ch_extra,
    })
    series = [
        {
            "time": int(r[0]),
            "sum_amount": float(r[1]),
            "sum_value_usd": float(r[2]),
            "count": int(r[3]),
        }
        for r in rows.result_rows
    ]
    body = {"event": event, "interval": interval, "series": series}
    if chain_group:
        body["chain_group"] = chain_group
        body["chains"] = chains
    else:
        body["chain"] = chains[0]
    return response.json(body)


@bp.get("/lido/streams")
async def streams(_request):
    """Distinct (event, chain) tuples with row counts — drives the chain
    selector on the Staking page so the dashboard only shows chains that
    actually have data. Lazily cached for STREAMS_TTL_SECONDS."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for event_key, (table, _amount_col) in _EVENT_TABLES.items():
        rows = await ch.query(f"""
            SELECT chain, count() AS rows
            FROM {table} FINAL
            GROUP BY chain
            ORDER BY chain
        """)
        for chain, n in rows.result_rows:
            out.append({"event": event_key, "chain": chain, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
