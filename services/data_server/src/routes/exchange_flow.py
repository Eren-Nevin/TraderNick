"""Pre-aggregated Exchange Flow rollups.

Source-of-truth: tradernick.exchange_flow_minute, a SummingMergeTree fed by
mv_exchange_flow (one MV that fans each transfer into the matching
(direction, exchange) buckets). Schema lives in clickhouse/init/01_schema.sql.

This route exists so the dashboard's Exchange Flow chart skips the heavy
~80s scan of the 971M-row transfers table and instead reads ~3M pre-rolled
rows for a 30-day × All-chain query — milliseconds at the tail end of the
ORDER BY (direction, exchange, chain, token, time) skip-scan.

Inflow/outflow filter semantics are baked into the MV's WHERE; the API just
projects out one (direction, exchange) at a time.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.groups import is_chain_group, is_token_group, resolve_chain_group, resolve_token_group
from routes.ohlcv import INTERVAL_SECONDS
from routes.transfers import _parse_iso  # ISO parser already validated elsewhere

bp = Blueprint("exchange_flow")

_VALID_DIRECTIONS = {"in", "out"}
_VALID_EXCHANGES = {"binance", "coinbase", "okx", "bybit", "hyperliquid"}


@bp.get("/exchange_flow/aggregate")
async def aggregate(request):
    direction = (request.args.get("direction") or "").lower()
    exchange = (request.args.get("exchange") or "").lower()
    if direction not in _VALID_DIRECTIONS:
        return response.json({"error": f"direction must be one of {sorted(_VALID_DIRECTIONS)}"}, status=400)
    if exchange not in _VALID_EXCHANGES:
        return response.json({"error": f"exchange must be one of {sorted(_VALID_EXCHANGES)}"}, status=400)

    interval = request.args.get("interval") or "1h"
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    seconds = INTERVAL_SECONDS[interval]

    since = request.args.get("since")
    until = request.args.get("until")
    if not since or not until:
        return response.json({"error": "since and until are required"}, status=400)
    try:
        since_dt = _parse_iso(since)
        until_dt = _parse_iso(until)
    except Exception:
        return response.json({"error": "since/until must be ISO 8601 datetimes"}, status=400)
    if since_dt >= until_dt:
        return response.json({"error": "since must be strictly before until"}, status=400)

    try:
        limit = max(1, min(int(request.args.get("limit", 10000)), 100000))
    except ValueError:
        return response.json({"error": "limit must be an integer"}, status=400)

    # Resolve chain + token (singleton or group) to concrete IN-lists.
    chain_group = request.args.get("chain_group")
    token_group = request.args.get("token_group")
    chain = request.args.get("chain")
    token = request.args.get("token")

    if chain_group and is_chain_group(chain_group):
        chains = await resolve_chain_group(chain_group)
    elif chain:
        chains = [chain]
    else:
        return response.json({"error": "chain or chain_group is required"}, status=400)
    if token_group and is_token_group(token_group):
        tokens = resolve_token_group(token_group)
    elif token:
        tokens = [token]
    else:
        return response.json({"error": "token or token_group is required"}, status=400)

    if not chains or not tokens:
        return response.json({"interval": interval, "series": [], "chains": chains, "tokens": tokens})

    # USD pricing: ASOF-join tradernick.binance_ohlcv_1m at query time and
    # multiply sum_amount × close at the minute boundary. Matches the OLD
    # /transfers/aggregate path byte-for-byte; sidesteps the
    # value_usd-is-NULL hole for native chain transfers (DeFiStream doesn't
    # price native moves since there's no swap to anchor against).
    #
    # Token rewrites for the OHLCV lookup:
    #   WETH → ETH, WBTC → BTC
    # Price is chain-agnostic — Binance only carries one mark per token, and
    # the wrapped variants trade at parity with the native (peg deviation is
    # bps-level and not chart-relevant).
    # Stablecoins ($1) get a fast-path before the join contributes anything.
    ch = await client()
    rows = await ch.query(
        """
        SELECT
            toUnixTimestamp(toStartOfInterval(t.time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sum(t.sum_amount) AS sum_amount,
            sum(t.sum_amount * if(
                t.token IN ('USDC', 'USDT', 'DAI', 'USDE'),
                1.0,
                coalesce(o.close, 0.0)
            )) AS sum_value_usd,
            sum(t.count) AS count
        FROM tradernick.exchange_flow_minute AS t
        ASOF LEFT JOIN tradernick.binance_ohlcv_1m AS o
          ON o.token = multiIf(t.token = 'WETH', 'ETH', t.token = 'WBTC', 'BTC', t.token)
         AND o.time <= t.time
        WHERE t.direction = {direction:String}
          AND t.exchange  = {exchange:String}
          AND t.chain IN {chains:Array(String)}
          AND t.token IN {tokens:Array(String)}
          AND t.time >= {since:DateTime}
          AND t.time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
        """,
        parameters={
            "seconds": seconds,
            "direction": direction,
            "exchange": exchange,
            "chains": chains,
            "tokens": tokens,
            "since": since_dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
            "until": until_dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
            "limit": limit,
        },
    )
    series = [
        {
            "time": int(r[0]),
            "sum_amount": float(r[1]),
            "sum_value_usd": float(r[2]),
            "count": int(r[3]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "interval": interval,
        "direction": direction,
        "exchange": exchange,
        "chains": chains,
        "tokens": tokens,
        "series": series,
    })
