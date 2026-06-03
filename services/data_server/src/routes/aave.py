"""AAVE v3 events aggregate endpoint.

Drives the six AAVE chart kinds on the dashboard's Lending page. One
endpoint, six tables — the `event` query param picks which.

Returns a bucketed series with the same time/count/amount/value_usd
shape that the transfer chart uses, so the dashboard's chart components
can read the response without per-kind branching. Liquidations have a
slightly richer shape (separate debt + collateral amounts) and are
documented inline below.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import (
    is_chain_group,
    is_token_group,
    resolve_chain_group,
    resolve_token_group,
)
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("aave")

# Event key → CH table + the "amount" expression used in the per-bucket
# sum. All tables except liquidations have a `amount` column; liquidations
# uses `debt_to_cover` as the headline volume figure (the column users
# usually want when they think "how much was liquidated").
_EVENT_TABLES = {
    "deposit":     ("tradernick.aave_deposits",     "amount",        None),
    "withdraw":    ("tradernick.aave_withdrawals",  "amount",        None),
    "borrow":      ("tradernick.aave_borrows",      "amount",        None),
    "repay":       ("tradernick.aave_repays",       "amount",        None),
    "flashloan":   ("tradernick.aave_flashloans",   "amount",        None),
    # For liquidations the "token" axis is debt_token (what was repaid).
    # We expose the same `amount` field — populated from debt_to_cover so
    # the dashboard can render it the same way as the other 5 charts.
    "liquidation": ("tradernick.aave_liquidations", "debt_to_cover", "debt_token"),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())
_ETH_MARKETS = ("Core", "Prime", "EtherFi")

# Streams catalogue cache — (event, chain, token, eth_market) tuples that
# actually have data, refreshed lazily every TTL seconds. Drives the
# token/chain selectors on the lending page.
_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [v.strip() for v in s.split(",") if v.strip()]


@bp.get("/aave/aggregate")
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
    eth_markets = _parse_csv(request.args.get("eth_markets"))
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    # Resolve chain (singleton OR group) and token (singleton OR group). The
    # cross-product (chain × token) gets fed to a `(chain, token) IN (…)`
    # predicate so rows for any matched pair are summed in one CH pass.
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

    # ETH market filter — comma-separated list of Core / Prime / EtherFi.
    # Only applied if any chain in the resolved list is ETH.
    eth_markets = [m for m in eth_markets if m in _ETH_MARKETS]
    market_where = ""
    chain_set = {c.upper() for c in chains}
    if "ETH" in chain_set and eth_markets:
        markets_sql = ", ".join(f"'{m}'" for m in eth_markets)
        market_where = f" AND eth_market IN ({markets_sql})"

    # Build the (chain, token) predicate. Single-pair → equality (slightly
    # cheaper); otherwise a tuple-IN. Both forms are parameterised against
    # the chain prefix of the table's order key so they prune efficiently.
    def _q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    if len(chains) == 1 and len(tokens) == 1:
        ckt_where = f"chain = {{chain:String}} AND {token_col} = {{token:String}}"
        ch_params_extra = {"chain": chains[0], "token": tokens[0]}
    else:
        pairs = [(c, t) for c in chains for t in tokens]
        tup_sql = ", ".join(f"({_q(c)}, {_q(t)})" for (c, t) in pairs)
        ckt_where = f"(chain, {token_col}) IN ({tup_sql})"
        ch_params_extra = {}

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum({amount_col})                       AS sum_amount,
            sum(coalesce(value_usd, 0))             AS sum_value_usd,
            count()                                 AS count
        FROM {table} FINAL
        WHERE {ckt_where}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
          {market_where}
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
        **ch_params_extra,
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
    resp_body = {
        "event": event,
        "eth_markets": eth_markets,
        "interval": interval,
        "series": series,
    }
    if chain_group:
        resp_body["chain_group"] = chain_group
        resp_body["chains"] = chains
    else:
        resp_body["chain"] = chain
    if token_group:
        resp_body["token_group"] = token_group
        resp_body["tokens"] = tokens
    else:
        resp_body["token"] = token
    return response.json(resp_body)


# Top-wallets leaderboard. One CTE per event, UNION ALL'd into a single
# wallet-column with an `ev` tag; GROUP BY wallet + sumIf/countIf collapses
# into one row per wallet with all 7 metrics. ORDER BY is whitelisted on a
# fixed column-name map so user input never reaches SQL untrusted.
#
# Wallet semantics per event (V3 — matches the V3 contract event sigs):
#   deposit     → on_behalf_of  (beneficiary; user is the depositor)
#   withdraw    → user          (no on_behalf_of; user is position-holder)
#   borrow      → on_behalf_of  (whose debt actually increased)
#   repay       → user          (the borrower; `repayer` is the executor)
#   liquidation → owner         (the liquidated party)
_LEADERBOARD_EVENTS_V3 = [
    # (table, wallet_col, token_col, ev_label)
    ("tradernick.aave_deposits",     "on_behalf_of", "token",      "deposit"),
    ("tradernick.aave_withdrawals",  "user",         "token",      "withdraw"),
    ("tradernick.aave_borrows",      "on_behalf_of", "token",      "borrow"),
    ("tradernick.aave_repays",       "user",         "token",      "repay"),
    ("tradernick.aave_liquidations", "owner",        "debt_token", "liquidation"),
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


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _build_ckt_where(chains: list[str], tokens: list[str], token_col: str) -> str:
    """(chain, token_col) IN (...) predicate — pure literals, both inputs come
    from the validated chain/token group resolution, never from raw query
    strings."""
    pairs = [(c, t) for c in chains for t in tokens]
    tup = ", ".join(f"({_q(c)}, {_q(t)})" for (c, t) in pairs)
    return f"(chain, {token_col}) IN ({tup})"


@bp.get("/aave/wallets/leaderboard")
async def leaderboard(request):
    chain = request.args.get("chain")
    token = request.args.get("token")
    chain_group = request.args.get("chain_group")
    token_group = request.args.get("token_group")
    eth_markets = _parse_csv(request.args.get("eth_markets"))
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

    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    eth_markets = [m for m in eth_markets if m in _ETH_MARKETS]
    chain_set = {c.upper() for c in chains}
    market_where = ""
    if "ETH" in chain_set and eth_markets:
        markets_sql = ", ".join(_q(m) for m in eth_markets)
        market_where = f" AND eth_market IN ({markets_sql})"

    # Build one SELECT per event, UNION ALL them. All five share the same
    # time window + market filter; only the table, wallet column, and
    # token column vary. Chain/token tuples are baked into _build_ckt_where.
    legs = []
    for table, wallet_col, token_col, ev_label in _LEADERBOARD_EVENTS_V3:
        ckt = _build_ckt_where(chains, tokens, token_col)
        legs.append(f"""
            SELECT {wallet_col} AS wallet, coalesce(value_usd, 0) AS value_usd, '{ev_label}' AS ev
            FROM {table} FINAL
            WHERE {ckt}
              AND time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
              {market_where}
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
        "eth_markets": eth_markets,
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


@bp.get("/aave/streams")
async def streams(_request):
    """Distinct (event, chain, token) tuples + which eth_markets each has.
    Lazily cached for STREAMS_TTL_SECONDS so the per-event selectors on the
    lending page don't re-scan the tables on every page load."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for event_key, (table, _amount_col, token_col_override) in _EVENT_TABLES.items():
        token_col = token_col_override or "token"
        rows = await ch.query(f"""
            SELECT chain, {token_col} AS token, groupArrayDistinct(eth_market) AS markets, count() AS rows
            FROM {table} FINAL
            GROUP BY chain, {token_col}
            ORDER BY chain, {token_col}
        """)
        for chain, token, markets, n in rows.result_rows:
            out.append({
                "event": event_key,
                "chain": chain,
                "token": token,
                "eth_markets": sorted(m for m in markets if m),
                "rows": int(n),
            })
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})
