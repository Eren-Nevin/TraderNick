"""Hyperliquid aggregate endpoint. 8 events on HL (a perp DEX).

The shape of HL data is unique among our protocols — every event carries
a wallet identity. The aggregate endpoint accepts optional `wallet=` and
`wallet_category=` filters that constrain the aggregation to a single
wallet or a labelled wallet category (CEX / Smart-Money / Bridge / …)
sourced from the existing tradernick.wallet_labels dictionary.

A dedicated /hyperliquid/wallets/leaderboard endpoint returns the
already-pre-aggregated trader performance from hl_trade_history for the
table-chart kind on the dashboard.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("hyperliquid")

# Event → (table, amount-expression, value-expression, optional wallet_col,
#          optional agg_func — defaults to "sum").
#
# wallet_col is the column on which to apply the wallet filter; some events
# (trades, vaults) have a different column name than "wallet".
#
# agg_func overrides the default sum() aggregation. Used by `funding` to
# average the rate column instead of summing the per-wallet amount — the
# sum of per-wallet amount is mathematically always ~0 (zero-sum transfer
# between longs and shorts), so it makes a useless chart. The avg(rate)
# gives the canonical funding-rate metric.
# 6-tuple per event: (table, amount_expr, value_expr, wallet_col, agg_func, extra_where).
# extra_where is appended to the WHERE clause for this event only — used by
# `fills` to filter to taker-side rows only (one trade emits two fill rows
# on HL: one for the taker with crossed=1 and one for the maker; summing
# all fills double-counts volume).
_EVENT_TABLES = {
    # OHLCV is special-cased — has window-bucketed shape, no aggregation.
    "ohlcv":            ("tradernick.hl_ohlcv_1m",        "volume",            "volume",          None,    "sum",  ""),
    # Trades: amount = total volume per bucket (sum), value = sum(price*amount)
    "trades":           ("tradernick.hl_trades",          "amount",            "price*amount",    None,    "sum",  ""),
    # Fills: amount = sum(size), value = sum(price*size). Filter to crossed=1
    # so we only count the taker side of each match (the maker side is the
    # mirror image and would double the volume).
    "fills":            ("tradernick.hl_fills",           "size",              "price*size",      "wallet","sum",  "crossed = 1"),
    # Funding: chart plots the funding RATE (avg per bucket). Positive rate
    # = longs paying shorts; negative = shorts paying longs. HL fires
    # funding hourly so `rate` is the hourly funding rate at that event.
    "funding":          ("tradernick.hl_funding",         "rate",              "rate",            "wallet","avg",  ""),
    # position_history deferred — see note in clickhouse.py HL_EVENTS.
    # Trade history: already pre-aggregated. amount = sum(volume), value = sum(net_pnl).
    "trade_history":    ("tradernick.hl_trade_history",   "volume",            "net_pnl",         "wallet","sum",  ""),
    "transfers":        ("tradernick.hl_transfers",       "amount",            "amount",          "wallet","sum",  ""),
    "vaults":           ("tradernick.hl_vaults",          "amount",            "amount",          "wallet","sum",  ""),
}
_EVENT_KEYS = tuple(_EVENT_TABLES.keys())

_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0


def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/hyperliquid/aggregate")
async def aggregate(request):
    event = request.args.get("event")
    if event not in _EVENT_TABLES:
        return response.json({"error": f"event must be one of {list(_EVENT_KEYS)}"}, status=400)
    table, amount_expr, value_expr, wallet_col, agg_func, extra_where = _EVENT_TABLES[event]

    token = request.args.get("token")            # optional; if absent, sums across tokens
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    # Optional wallet filters. wallet = an EVM address (lowercased before
    # lookup since wallet_labels dict was loaded both-cases for 0x-prefixed).
    wallet = request.args.get("wallet")
    wallet_category = request.args.get("wallet_category")

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    where_parts = ["time >= {since:DateTime}", "time <  {until:DateTime}"]
    if extra_where:
        where_parts.append(extra_where)
    params: dict = {"seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit}

    if token:
        # Not all events have a token column (transfers, vaults). The dispatch
        # table covers six of eight; the two that don't simply ignore the filter.
        if event not in ("transfers", "vaults"):
            where_parts.append("token = {token:String}")
            params["token"] = token

    # Wallet filtering. Only applicable if the event table has a wallet column.
    if wallet_col is not None:
        if wallet:
            where_parts.append(f"lower({wallet_col}) = {{wallet:String}}")
            params["wallet"] = wallet.lower()
        elif wallet_category:
            # dictHas + dictGet against the wallet_labels dictionary loaded
            # in /flows work — labels are an Array(String) of categories.
            where_parts.append(
                f"has(dictGet('tradernick.wallet_labels', 'categories', lower({wallet_col})), {{category:String}})"
            )
            params["category"] = wallet_category

    where_sql = " AND ".join(where_parts)

    # FINAL forces the ReplacingMergeTree merge at query time, so
    # un-merged duplicate rows from force=false backfills don't double the
    # aggregates. ~10-30% query overhead on un-merged ranges, near-zero
    # once background merges have run.
    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            {agg_func}({amount_expr}) AS sum_amount,
            {agg_func}({value_expr})  AS sum_value_usd,
            count()                   AS count
        FROM {table} FINAL
        WHERE {where_sql}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {"time": int(r[0]), "sum_amount": float(r[1]), "sum_value_usd": float(r[2]), "count": int(r[3])}
        for r in rows.result_rows
    ]
    body = {"event": event, "interval": interval, "series": series}
    if token: body["token"] = token
    if wallet: body["wallet"] = wallet
    if wallet_category: body["wallet_category"] = wallet_category
    return response.json(body)


@bp.get("/hyperliquid/streams")
async def streams(_request):
    """Distinct (event, token) tuples with row counts. Cached 60s.
    Powers the per-chart token selector on the /hyperliquid page."""
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    ch = await client()
    out: list[dict] = []
    for ev, (table, _a, _v, _w, _af, _ew) in _EVENT_TABLES.items():
        # transfers + vaults have no token dimension — skip them in streams.
        if ev in ("transfers", "vaults"):
            continue
        rows = await ch.query(f"""
            SELECT token, count() AS rows
            FROM {table} FINAL
            WHERE token != ''
            GROUP BY token
            ORDER BY rows DESC
        """)
        for tok, n in rows.result_rows:
            out.append({"event": ev, "token": tok, "rows": int(n)})
    # position_history is intentionally not in _EVENT_TABLES (state, not
    # flow — see /hyperliquid/unrealized_pnl), but the chart picker still
    # needs to know which tokens have snapshots available.
    rows = await ch.query("""
        SELECT token, count() AS rows
        FROM tradernick.hl_position_history FINAL
        WHERE token != ''
        GROUP BY token
        ORDER BY rows DESC
    """)
    for tok, n in rows.result_rows:
        out.append({"event": "position_history", "token": tok, "rows": int(n)})
    _STREAMS_CACHE["value"] = out
    _STREAMS_CACHE["at"] = now
    return response.json({"streams": out})


@bp.get("/hyperliquid/wallets/leaderboard")
async def leaderboard(request):
    """Top-N traders by PnL or volume, optionally filtered to a single
    token, over a [since, until] window. Read from the pre-aggregated
    hl_trade_history table (small, fast)."""
    token = request.args.get("token")
    since = request.args.get("since")
    until = request.args.get("until")
    order_by = request.args.get("order_by", "net_pnl")
    limit = int(request.args.get("limit", "50"))

    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if order_by not in ("net_pnl", "pnl", "volume", "trade_count"):
        return response.json({"error": "order_by must be net_pnl|pnl|volume|trade_count"}, status=400)

    since_dt = _parse_iso(since); until_dt = _parse_iso(until)
    params: dict = {"since": since_dt, "until": until_dt, "limit": limit}
    where = ["time >= {since:DateTime}", "time <  {until:DateTime}"]
    if token:
        where.append("token = {token:String}")
        params["token"] = token
    where_sql = " AND ".join(where)

    sql = f"""
        SELECT
            wallet,
            sum(net_pnl)  AS net_pnl,
            sum(pnl)      AS pnl,
            sum(fees)     AS fees,
            sum(volume)   AS volume,
            sum(buy_volume) AS buy_volume,
            sum(sell_volume) AS sell_volume,
            sum(trade_count) AS trade_count,
            -- Surface wallet labels (Array(String)) for the badge on the
            -- table chart; empty array for unlabelled wallets.
            dictGet('tradernick.wallet_labels', 'categories', lower(wallet)) AS categories
        FROM tradernick.hl_trade_history FINAL
        WHERE {where_sql}
        GROUP BY wallet
        ORDER BY {order_by} DESC
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters=params)
    leaders = [
        {
            "wallet": r[0],
            "net_pnl": float(r[1]),
            "pnl": float(r[2]),
            "fees": float(r[3]),
            "volume": float(r[4]),
            "buy_volume": float(r[5]),
            "sell_volume": float(r[6]),
            "trade_count": int(r[7]),
            "categories": list(r[8]) if r[8] else [],
        }
        for r in rows.result_rows
    ]
    return response.json({
        "order_by": order_by, "limit": limit,
        "token": token, "since": since, "until": until,
        "leaders": leaders,
    })


@bp.get("/hyperliquid/oi_split")
async def oi_split(request):
    """Per-bucket Open Interest on HL, split into long / short / total.

    Same state-aware aggregation pattern as /hyperliquid/unrealized_pnl:
    per-wallet argMax(*, time) per (bucket, wallet, side) collapses to
    one row per snapshot before summing across wallets — avoids
    double-counting carry-forward position rows.

    Returns BOTH token-unit totals (long_oi/short_oi/total_oi) and USD
    notional totals (*_value), so the chart can plot either dimension
    without a second request.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            toUnixTimestamp(bucket)                AS bucket,
            sumIf(latest_amount, side='long')      AS long_oi,
            sumIf(latest_amount, side='short')     AS short_oi,
            sum(latest_amount)                     AS total_oi,
            sumIf(latest_size,   side='long')      AS long_oi_value,
            sumIf(latest_size,   side='short')     AS short_oi_value,
            sum(latest_size)                       AS total_oi_value
        FROM (
            SELECT
                toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND) AS bucket,
                wallet, side,
                argMax(amount, time) AS latest_amount,
                argMax(size,   time) AS latest_size
            FROM tradernick.hl_position_history FINAL
            WHERE token = {token:String}
              AND time >= {since:DateTime}
              AND time <  {until:DateTime}
            GROUP BY bucket, wallet, side
        )
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "long_oi": float(r[1]),
            "short_oi": float(r[2]),
            "total_oi": float(r[3]),
            "long_oi_value": float(r[4]),
            "short_oi_value": float(r[5]),
            "total_oi_value": float(r[6]),
        }
        for r in rows.result_rows
    ]
    return response.json({"token": token, "interval": interval, "series": series})


@bp.get("/hyperliquid/bridge_flows")
async def bridge_flows(request):
    """Per-bucket USDC flow across the HL Arbitrum bridge: deposit (in),
    withdrawal (out), and net = deposit - withdrawal.

    Both deposit and withdrawal are returned as POSITIVE magnitudes so
    the operator can compare the two side-by-side. Net is the signed
    difference and floats through zero showing the directional bias.
    """
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sumIf(amount, direction='deposit')     AS deposit,
            sumIf(amount, direction='withdrawal')  AS withdrawal,
            sumIf(amount, direction='deposit')
              - sumIf(amount, direction='withdrawal') AS net,
            countIf(direction='deposit')           AS deposit_count,
            countIf(direction='withdrawal')        AS withdrawal_count
        FROM tradernick.hl_transfers FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "deposit": float(r[1]),
            "withdrawal": float(r[2]),
            "net": float(r[3]),
            "deposit_count": int(r[4]),
            "withdrawal_count": int(r[5]),
        }
        for r in rows.result_rows
    ]
    return response.json({"interval": interval, "series": series})


@bp.get("/hyperliquid/vault_flow")
async def vault_flow(request):
    """Per-bucket vault deposit / withdraw / net flow (USDC).

    Mirrors the bridge_flows shape — deposit and withdraw are both
    positive magnitudes for easy side-by-side comparison; net is the
    signed difference (deposit - withdraw). distribution is included
    as a fourth field but the chart only plots the three core lines.
    """
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sumIf(amount, action='deposit')      AS deposit,
            sumIf(amount, action='withdraw')     AS withdraw,
            sumIf(amount, action='deposit')
              - sumIf(amount, action='withdraw') AS net,
            sumIf(amount, action='distribution') AS distribution,
            countIf(action='deposit')            AS deposit_count,
            countIf(action='withdraw')           AS withdraw_count
        FROM tradernick.hl_vaults FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "seconds": seconds, "since": since_dt, "until": until_dt, "limit": limit,
    })
    series = [
        {
            "time": int(r[0]),
            "deposit": float(r[1]),
            "withdraw": float(r[2]),
            "net": float(r[3]),
            "distribution": float(r[4]),
            "deposit_count": int(r[5]),
            "withdraw_count": int(r[6]),
        }
        for r in rows.result_rows
    ]
    return response.json({"interval": interval, "series": series})


_VAULT_SORT_KEYS = {
    "net":          "net DESC",
    "deposits":     "deposits DESC",
    "withdrawals":  "withdrawals DESC",
    "commission":   "commission DESC",
    "total_pnl":    "total_pnl DESC",
    "realized_pnl": "realized_pnl DESC",
    "roe":          "roe DESC",
}


# Shared SQL fragments — vault performance JOINs against position_history
# (open notional + unrealized PnL at the latest snapshot) and trade_history
# (realized PnL + trade volume over the window). Both wrapped in CTEs
# so the main top_vaults / vault_detail queries can LEFT JOIN without
# duplicating the aggregation logic.
#
# `vault_positions` uses a recent 30-min window to identify "currently
# open" positions — position_history is carry-forward so any open
# position appears in every snapshot. argMax-per-(wallet,token,side)
# then collapses to one row per slot (latest values), and the outer
# sum aggregates per wallet across all tokens / both sides.
_VAULT_PERF_CTE = """
  vault_positions AS (
    SELECT wallet AS vault,
      sum(latest_size) AS open_notional,
      sum(latest_upnl) AS unrealized_pnl
    FROM (
      SELECT wallet, token, side,
        argMax(size, time)            AS latest_size,
        argMax(unrealized_pnl, time)  AS latest_upnl
      FROM tradernick.hl_position_history FINAL
      WHERE time >= now() - INTERVAL 30 MINUTE
      GROUP BY wallet, token, side
    )
    GROUP BY wallet
  ),
  vault_realized AS (
    SELECT wallet AS vault,
      sum(net_pnl)     AS realized_pnl,
      sum(volume)      AS trade_volume,
      sum(trade_count) AS trade_count_total
    FROM tradernick.hl_trade_history FINAL
    WHERE time >= {since:DateTime}
      AND time <  {until:DateTime}
    GROUP BY wallet
  )
"""


@bp.get("/hyperliquid/top_vaults")
async def top_vaults(request):
    """Leaderboard of vaults over a [since, until] window. Sort key
    selects which metric ranks the table.

    Returns per-vault aggregates: deposits / withdrawals / net /
    commission earned by leader / distributions paid to LPs /
    distinct LP count / event count / age (first event in window).
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "20"))
    order_by = request.args.get("order_by", "net")
    if order_by not in _VAULT_SORT_KEYS:
        return response.json({"error": f"order_by must be one of {list(_VAULT_SORT_KEYS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = f"""
        WITH
          vault_stats AS (
            SELECT
              vault,
              sumIf(amount, action='deposit')      AS deposits,
              sumIf(amount, action='withdraw')     AS withdrawals,
              sumIf(amount, action='deposit')
                - sumIf(amount, action='withdraw') AS net,
              sumIf(commission, action='withdraw') AS commission,
              sumIf(amount, action='distribution') AS distributions,
              uniqExact(wallet)                    AS lp_count,
              count()                              AS event_count
            FROM tradernick.hl_vaults FINAL
            WHERE time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
            GROUP BY vault
          ),
          {_VAULT_PERF_CTE}
        SELECT
          s.vault, s.deposits, s.withdrawals, s.net,
          s.commission, s.distributions, s.lp_count, s.event_count,
          COALESCE(p.open_notional, 0)                                 AS open_notional,
          COALESCE(p.unrealized_pnl, 0)                                AS unrealized_pnl,
          COALESCE(r.realized_pnl, 0)                                  AS realized_pnl,
          COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)  AS total_pnl,
          COALESCE(r.trade_volume, 0)                                  AS trade_volume,
          COALESCE(r.trade_count_total, 0)                             AS trade_count_total,
          -- RoE = total PnL / open notional × 100. Open notional is the
          -- proxy for "capital deployed right now". Returns 0 when no
          -- positions are open (commonly: vaults that only have deposit/
          -- withdraw activity, no actual trading).
          if(p.open_notional > 0,
             (COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)) / p.open_notional * 100,
             0)                                                        AS roe
        FROM vault_stats s
        LEFT JOIN vault_positions p ON p.vault = s.vault
        LEFT JOIN vault_realized  r ON r.vault = s.vault
        ORDER BY {_VAULT_SORT_KEYS[order_by]}
        LIMIT {{limit:UInt32}}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    vaults = [
        {
            "rank": idx + 1,
            "vault": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "commission": float(r[4]),
            "distributions": float(r[5]),
            "lp_count": int(r[6]),
            "event_count": int(r[7]),
            "open_notional": float(r[8]),
            "unrealized_pnl": float(r[9]),
            "realized_pnl": float(r[10]),
            "total_pnl": float(r[11]),
            "trade_volume": float(r[12]),
            "trade_count_total": int(r[13]),
            "roe": float(r[14]),
        }
        for idx, r in enumerate(rows.result_rows)
    ]
    return response.json({
        "order_by": order_by, "since": since, "until": until,
        "vaults": vaults,
    })


@bp.get("/hyperliquid/top_vault_lps")
async def top_vault_lps(request):
    """Top LPs by net USDC deposited into HL vaults over [since, until].

    Action filter to deposit + withdraw (distribution is the vault paying
    its LPs and 'create' is the vault leader's own initial seed, neither
    is an LP action). Categories surfaces wallet labels for whale tagging.
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "20"))
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    sql = """
        SELECT
            wallet,
            sumIf(amount, action='deposit')      AS deposits,
            sumIf(amount, action='withdraw')     AS withdrawals,
            sumIf(amount, action='deposit')
              - sumIf(amount, action='withdraw') AS net,
            uniqExact(vault)                     AS vaults_used,
            count()                              AS event_count,
            dictGet('tradernick.wallet_labels', 'categories', lower(wallet)) AS categories
        FROM tradernick.hl_vaults FINAL
        WHERE time >= {since:DateTime}
          AND time <  {until:DateTime}
          AND action IN ('deposit', 'withdraw')
        GROUP BY wallet
        ORDER BY net DESC
        LIMIT {limit:UInt32}
    """
    ch = await client()
    rows = await ch.query(sql, parameters={
        "since": since_dt, "until": until_dt, "limit": limit,
    })
    lps = [
        {
            "rank": idx + 1,
            "wallet": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "vaults_used": int(r[4]),
            "event_count": int(r[5]),
            "categories": list(r[6]) if r[6] else [],
        }
        for idx, r in enumerate(rows.result_rows)
    ]
    return response.json({
        "since": since, "until": until,
        "lps": lps,
    })


@bp.get("/hyperliquid/vault_detail")
async def vault_detail(request):
    """Top-N vaults by gross flow + each vault's most-recent activity
    log in a single response. Mirrors the top_positions UX so flipping
    the vault selector on the dashboard is instant (no re-fetch).

    Per-event row includes time / action / wallet (LP) / amount /
    commission / fee — the full event detail. Activity list is capped
    at recent_n per vault (default 50) so the response stays bounded
    even with very active vaults.
    """
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10"))
    recent_n = int(request.args.get("recent_n", "50"))
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)

    ch = await client()

    # Step 1 — top-N vaults by gross flow, plus perf metrics from the
    # shared CTEs (open notional / UPnL / realized PnL / trade volume).
    top_rows = await ch.query(
        f"""
        WITH
          vault_stats AS (
            SELECT
                vault,
                sumIf(amount, action='deposit')      AS deposits,
                sumIf(amount, action='withdraw')     AS withdrawals,
                sumIf(amount, action='deposit')
                  - sumIf(amount, action='withdraw') AS net,
                sumIf(commission, action='withdraw') AS commission,
                sumIf(amount, action='distribution') AS distributions,
                uniqExact(wallet)                    AS lp_count,
                count()                              AS event_count,
                toUnixTimestamp(min(time))           AS first_event_at,
                toUnixTimestamp(max(time))           AS last_event_at
            FROM tradernick.hl_vaults FINAL
            WHERE time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
            GROUP BY vault
            ORDER BY (sumIf(amount, action='deposit') + sumIf(amount, action='withdraw')) DESC
            LIMIT {{limit:UInt32}}
          ),
          {_VAULT_PERF_CTE}
        SELECT
          s.vault, s.deposits, s.withdrawals, s.net,
          s.commission, s.distributions, s.lp_count, s.event_count,
          s.first_event_at, s.last_event_at,
          COALESCE(p.open_notional, 0)                                AS open_notional,
          COALESCE(p.unrealized_pnl, 0)                               AS unrealized_pnl,
          COALESCE(r.realized_pnl, 0)                                 AS realized_pnl,
          COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0) AS total_pnl,
          COALESCE(r.trade_volume, 0)                                 AS trade_volume,
          COALESCE(r.trade_count_total, 0)                            AS trade_count_total,
          if(p.open_notional > 0,
             (COALESCE(r.realized_pnl, 0) + COALESCE(p.unrealized_pnl, 0)) / p.open_notional * 100,
             0)                                                       AS roe
        FROM vault_stats s
        LEFT JOIN vault_positions p ON p.vault = s.vault
        LEFT JOIN vault_realized  r ON r.vault = s.vault
        ORDER BY (s.deposits + s.withdrawals) DESC
        """,
        parameters={"since": since_dt, "until": until_dt, "limit": limit},
    )
    if not top_rows.result_rows:
        return response.json({"since": since, "until": until, "vaults": []})

    vault_list = [r[0] for r in top_rows.result_rows]
    vault_meta: dict = {
        r[0]: {
            "vault": r[0],
            "deposits": float(r[1]),
            "withdrawals": float(r[2]),
            "net": float(r[3]),
            "commission": float(r[4]),
            "distributions": float(r[5]),
            "lp_count": int(r[6]),
            "event_count": int(r[7]),
            "first_event_at": int(r[8]),
            "last_event_at": int(r[9]),
            "open_notional": float(r[10]),
            "unrealized_pnl": float(r[11]),
            "realized_pnl": float(r[12]),
            "total_pnl": float(r[13]),
            "trade_volume": float(r[14]),
            "trade_count_total": int(r[15]),
            "roe": float(r[16]),
            "events": [],
        }
        for r in top_rows.result_rows
    }

    # Step 2 — pull the last recent_n events per top vault. Using
    # row_number() partitioned by vault gives a bounded per-vault tail
    # without a separate query per vault.
    event_rows = await ch.query(
        """
        SELECT vault, ts, wallet, action, amount, commission, fee FROM (
            SELECT
                vault,
                toUnixTimestamp(time) AS ts,
                wallet, action, amount, commission, fee,
                row_number() OVER (PARTITION BY vault ORDER BY time DESC) AS rn
            FROM tradernick.hl_vaults FINAL
            WHERE vault IN {vaults:Array(String)}
              AND time >= {since:DateTime}
              AND time <  {until:DateTime}
        )
        WHERE rn <= {recent_n:UInt32}
        ORDER BY vault, ts DESC
        """,
        parameters={
            "vaults": vault_list, "since": since_dt, "until": until_dt,
            "recent_n": recent_n,
        },
    )
    for r in event_rows.result_rows:
        v = r[0]
        if v not in vault_meta:
            continue
        vault_meta[v]["events"].append({
            "time": int(r[1]),
            "wallet": r[2],
            "action": r[3],
            "amount": float(r[4]),
            "commission": float(r[5]),
            "fee": float(r[6]),
        })

    vaults_out = []
    for rank, v in enumerate(vault_list, start=1):
        entry = vault_meta[v]
        entry["rank"] = rank
        vaults_out.append(entry)

    return response.json({
        "since": since, "until": until,
        "vaults": vaults_out,
    })


@bp.get("/hyperliquid/top_positions")
async def top_positions(request):
    """Top 10 wallets by current unrealized PnL, plus each wallet's full
    position breakdown (all tokens, both sides).

    `token` is optional:
      - specified: rank wallets by their unrealized_pnl in that token
        (sum over sides)
      - omitted:  rank wallets by sum(unrealized_pnl) across ALL tokens

    Either way, the per-wallet `positions` list contains every open
    position the wallet currently holds — not just the ranked-on token.
    That lets the dashboard show the full portfolio of a top trader
    even when filtered to a single token's leaderboard.

    'Current' = the most recent snapshot in the table (capped at the
    last 2h). Within a 1h lookback window we argMax to the latest row
    per (wallet, token, side) — handles wallets whose latest tick is
    a minute or two stale without missing them.
    """
    token = request.args.get("token") or None
    limit = int(request.args.get("limit", "10"))

    # The latest_t lookup is intentionally scoped to the last 2h so a
    # stale table (e.g. fresh restart, gap-fill behind) doesn't claim
    # 'current' from days ago.
    latest_where = ["time >= now() - INTERVAL 2 HOUR"]
    latest_params: dict = {}
    if token:
        latest_where.append("token = {token:String}")
        latest_params["token"] = token

    ch = await client()
    latest_rows = await ch.query(
        f"""
        SELECT toUnixTimestamp(max(time)) AS t
        FROM tradernick.hl_position_history FINAL
        WHERE {' AND '.join(latest_where)}
        """,
        parameters=latest_params,
    )
    if not latest_rows.result_rows or not latest_rows.result_rows[0][0]:
        return response.json({
            "token": token, "as_of": None, "wallets": [],
            "note": "no recent position_history data — backfill or live tick not caught up yet",
        })
    as_of = int(latest_rows.result_rows[0][0])

    # Combined query — top-N + their full positions in one pass via JOIN
    # against the same per-(wallet,token,side) latest set.
    where_p = [
        "time >= toDateTime({as_of:UInt32}) - INTERVAL 1 HOUR",
        "time <= toDateTime({as_of:UInt32})",
    ]
    params: dict = {"as_of": as_of, "limit": limit}
    rank_filter = ""
    if token:
        rank_filter = "WHERE token = {token:String}"
        params["token"] = token

    sql = f"""
        WITH positions AS (
            SELECT wallet, token, side,
                   argMax(unrealized_pnl, time) AS unrealized_pnl,
                   argMax(size, time)           AS size,
                   argMax(amount, time)         AS amount,
                   argMax(avg_entry, time)      AS avg_entry,
                   argMax(mark_price, time)     AS mark_price,
                   argMax(funding, time)        AS funding,
                   argMax(fee, time)            AS fee,
                   toUnixTimestamp(argMax(opened_at, time)) AS opened_at,
                   toUnixTimestamp(max(time))               AS row_as_of
            FROM tradernick.hl_position_history FINAL
            WHERE {' AND '.join(where_p)}
            GROUP BY wallet, token, side
        ),
        ranked AS (
            SELECT wallet, sum(unrealized_pnl) AS score
            FROM positions
            {rank_filter}
            GROUP BY wallet
            ORDER BY score DESC
            LIMIT {{limit:UInt32}}
        )
        SELECT
            r.wallet,
            r.score,
            dictGet('tradernick.wallet_labels', 'categories', lower(r.wallet)) AS categories,
            p.token, p.side, p.unrealized_pnl, p.size, p.amount,
            p.avg_entry, p.mark_price, p.funding, p.fee, p.opened_at, p.row_as_of
        FROM ranked r
        LEFT JOIN positions p ON p.wallet = r.wallet
        ORDER BY r.score DESC, abs(p.unrealized_pnl) DESC
    """

    rows = await ch.query(sql, parameters=params)

    # Group rows by wallet preserving the score-DESC order
    by_wallet: dict = {}
    order: list[str] = []
    for r in rows.result_rows:
        w = r[0]
        if w not in by_wallet:
            by_wallet[w] = {
                "wallet": w,
                "score_unrealized_pnl": float(r[1]),
                "categories": list(r[2]) if r[2] else [],
                "positions": [],
            }
            order.append(w)
        # r[3..] = per-position fields; some wallets may have empty positions
        # if the LEFT JOIN landed a NULL row (no positions in window) — skip.
        if r[3] is None:
            continue
        by_wallet[w]["positions"].append({
            "token": r[3],
            "side": r[4],
            "unrealized_pnl": float(r[5]),
            "size": float(r[6]),
            "amount": float(r[7]),
            "avg_entry": float(r[8]),
            "mark_price": float(r[9]),
            "funding": float(r[10]),
            "fee": float(r[11]),
            "opened_at": int(r[12]) if r[12] is not None else None,
            "as_of": int(r[13]) if r[13] is not None else as_of,
        })

    wallets = []
    for rank, w in enumerate(order, start=1):
        entry = by_wallet[w]
        entry["rank"] = rank
        wallets.append(entry)

    return response.json({
        "token": token,
        "as_of": as_of,
        "wallets": wallets,
    })


@bp.get("/hyperliquid/unrealized_pnl")
async def unrealized_pnl(request):
    """Per-bucket unrealized PnL totals for a token (long + short + net).

    position_history rows are STATE (mark-to-market at each 5m snapshot),
    not flow events — so summing raw rows across multiple snapshots in a
    bucket double-counts. The inner sub-query collapses to one row per
    (bucket, wallet, side) by taking argMax(unrealized_pnl, time), then
    the outer aggregate sums across wallets.

    Returns long_pnl / short_pnl / net_pnl plus wallet counts so the
    chart can show 'how many longs are underwater' tooltips later.
    """
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))
    wallet = request.args.get("wallet")

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since); until_dt = _parse_iso(until)
    params: dict = {
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    }
    inner_where = [
        "token = {token:String}",
        "time >= {since:DateTime}",
        "time <  {until:DateTime}",
    ]
    if wallet:
        inner_where.append("lower(wallet) = {wallet:String}")
        params["wallet"] = wallet.lower()
    inner_where_sql = " AND ".join(inner_where)

    # No FINAL: argMax(unrealized_pnl, time) inside the inner GROUP BY already
    # collapses duplicate (bucket, wallet, side) rows to the latest snapshot in
    # the bucket. FINAL would only matter if duplicates shared an identical
    # `time` and we needed `ingested_at` as a tiebreaker — that's not the case
    # for a state stream at fixed 5m cadence. FINAL on a 1B-row ReplacingMT
    # forces a merge-on-read across parts and is the slowest part of the query.
    sql = f"""
        SELECT
            toUnixTimestamp(bucket)         AS bucket,
            sumIf(latest_pnl, side='long')  AS long_pnl,
            sumIf(latest_pnl, side='short') AS short_pnl,
            sum(latest_pnl)                 AS net_pnl,
            countIf(side='long')            AS long_wallets,
            countIf(side='short')           AS short_wallets
        FROM (
            SELECT
                toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND) AS bucket,
                wallet, side,
                argMax(unrealized_pnl, time) AS latest_pnl
            FROM tradernick.hl_position_history
            WHERE {inner_where_sql}
            GROUP BY bucket, wallet, side
        )
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters=params)
    series = [
        {
            "time": int(r[0]),
            "long_pnl": float(r[1]),
            "short_pnl": float(r[2]),
            "net_pnl": float(r[3]),
            "long_wallets": int(r[4]),
            "short_wallets": int(r[5]),
        }
        for r in rows.result_rows
    ]
    body = {"token": token, "interval": interval, "series": series}
    if wallet: body["wallet"] = wallet
    return response.json(body)
