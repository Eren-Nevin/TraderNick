from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS
from throttle import throttled

bp = Blueprint("derivatives")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _validate(request):
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "5000"))
    if not token:
        return None, response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return None, response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return None, response.json({"error": "missing since/until"}, status=400)
    return {
        "token": token,
        "seconds": INTERVAL_SECONDS[interval],
        "interval": interval,
        "since": _parse_iso(since),
        "until": _parse_iso(until),
        "limit": limit,
    }, None


@bp.get("/open_interest")
async def open_interest(request):
    """Binance-only OI endpoint. HL OI is served by /hyperliquid/oi_split
    (which also carries long/short totals); the dashboard's OI chart
    routes HL fetches there directly."""
    args, err = _validate(request)
    if err is not None:
        return err
    ch = await client()
    rows = await ch.query(
        """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            argMax(open_interest,       time) AS open_interest,
            argMax(open_interest_value, time) AS open_interest_value
        FROM tradernick.binance_open_interest
        WHERE token = {token:String}
          AND time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
        """,
        parameters=args,
    )
    series = [
        {"time": int(r[0]), "open_interest": float(r[1]), "open_interest_value": float(r[2])}
        for r in rows.result_rows
    ]
    return response.json({
        "token": args["token"], "exchange": "binance",
        "interval": args["interval"], "series": series,
    })


@bp.get("/long_short_ratios")
@throttled("heavy")
async def long_short_ratios(request):
    """Long/short ratios for the ls + tt chart kinds.

    `exchange=binance` (default): pre-aggregated server-side from Binance
    Futures' top-trader & long/short endpoints — all four ratios are
    real.

    `exchange=hl`: only the two `ls`-chart ratios are computed (count
    from hl_position_history, taker volume from hl_fills); the two
    `top_trader_*` fields are returned as 0. The `tt` chart kind is
    intentionally NOT extended to HL — Binance's "top trader" is a
    product concept (top 20% of accounts by collateral) with no
    equivalent in HL's permissionless wallet-transparent design.
    """
    args, err = _validate(request)
    if err is not None:
        return err
    exchange = request.args.get("exchange", "binance")
    if exchange not in ("binance", "hl"):
        return response.json({"error": "exchange must be binance|hl"}, status=400)

    ch = await client()
    if exchange == "hl":
        # long_short_count_ratio: at the last snapshot in each bucket,
        # ratio of wallets currently long vs currently short. position_
        # history is state, so we collapse to per-snap counts first then
        # argMax to the latest snap within each bucket.
        # taker_long_short_vol_ratio: flow event — taker buys ($) /
        # taker sells ($) per bucket from hl_fills (crossed=1 marks
        # the taker side; side='B' = buy, side='A' = sell).
        sql = """
            WITH
              positions AS (
                SELECT
                  bucket,
                  argMax(longs,  snap) AS long_count,
                  argMax(shorts, snap) AS short_count
                FROM (
                  SELECT time AS snap,
                         toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND) AS bucket,
                         countIf(side='long')  AS longs,
                         countIf(side='short') AS shorts
                  FROM tradernick.hl_position_history FINAL
                  WHERE token = {token:String}
                    AND time >= {since:DateTime}
                    AND time <  {until:DateTime}
                  GROUP BY time
                )
                GROUP BY bucket
              ),
              takers AS (
                SELECT
                  toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND) AS bucket,
                  sumIf(size*price, crossed=1 AND side='B') AS taker_buy_vol,
                  sumIf(size*price, crossed=1 AND side='A') AS taker_sell_vol
                FROM tradernick.hl_fills FINAL
                WHERE token = {token:String}
                  AND time >= {since:DateTime}
                  AND time <  {until:DateTime}
                GROUP BY bucket
              )
            SELECT
              toUnixTimestamp(p.bucket) AS bucket,
              0.0 AS top_trader_count_ratio,
              0.0 AS top_trader_vol_ratio,
              if(p.short_count > 0,  p.long_count / p.short_count,        0) AS long_short_count_ratio,
              if(t.taker_sell_vol > 0, t.taker_buy_vol / t.taker_sell_vol, 0) AS taker_long_short_vol_ratio
            FROM positions p
            LEFT JOIN takers t ON p.bucket = t.bucket
            ORDER BY p.bucket
            LIMIT {limit:UInt32}
        """
    else:
        sql = """
            SELECT
                toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
                argMax(top_trader_count_ratio,     time) AS top_trader_count_ratio,
                argMax(top_trader_vol_ratio,       time) AS top_trader_vol_ratio,
                argMax(long_short_count_ratio,     time) AS long_short_count_ratio,
                argMax(taker_long_short_vol_ratio, time) AS taker_long_short_vol_ratio
            FROM tradernick.binance_long_short_ratios
            WHERE token = {token:String}
              AND time >= {since:DateTime}
              AND time <  {until:DateTime}
            GROUP BY bucket
            ORDER BY bucket
            LIMIT {limit:UInt32}
        """
    rows = await ch.query(sql, parameters=args)
    series = [
        {
            "time": int(r[0]),
            "top_trader_count_ratio": float(r[1]),
            "top_trader_vol_ratio": float(r[2]),
            "long_short_count_ratio": float(r[3]),
            "taker_long_short_vol_ratio": float(r[4]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "token": args["token"], "exchange": exchange,
        "interval": args["interval"], "series": series,
    })


# Per-exchange funding-rate source. Same response shape for both:
# {time, rate}. Aggregation differs because the row shapes do — binance
# has one (token, time) row at the funding-event boundary, so argMax
# picks the latest rate in the bucket; HL has per-wallet rows where
# every wallet at the same event-time carries the same rate, so avg()
# is equivalent to picking any one (and averages cleanly across
# multiple event-times in a longer bucket).
_FR_SOURCE = {
    "binance": ("tradernick.binance_funding_rate", "argMax(rate, time)"),
    "hl":      ("tradernick.hl_funding",           "avg(rate)"),
}


@bp.get("/funding_rate")
async def funding_rate(request):
    args, err = _validate(request)
    if err is not None:
        return err
    exchange = request.args.get("exchange", "binance")
    if exchange not in _FR_SOURCE:
        return response.json({"error": f"exchange must be one of {list(_FR_SOURCE)}"}, status=400)
    table, rate_expr = _FR_SOURCE[exchange]
    ch = await client()
    rows = await ch.query(
        f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            {rate_expr} AS rate
        FROM {table}
        WHERE token = {{token:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
        """,
        parameters=args,
    )
    series = [{"time": int(r[0]), "rate": float(r[1])} for r in rows.result_rows]
    return response.json({
        "token": args["token"], "exchange": exchange,
        "interval": args["interval"], "series": series,
    })
