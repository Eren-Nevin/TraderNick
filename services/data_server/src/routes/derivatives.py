from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

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
    args, err = _validate(request)
    if err is not None:
        return err
    exchange = request.args.get("exchange", "binance")
    if exchange not in ("binance", "hl"):
        return response.json({"error": "exchange must be binance|hl"}, status=400)

    ch = await client()
    if exchange == "hl":
        # HL OI = sum of every wallet's open position notional (long + short)
        # at the latest snapshot in each bucket. position_history is STATE
        # not flow, so per-wallet argMax(*, time) collapses to one row per
        # (bucket, wallet, side) before summing — same correctness pattern
        # as /hyperliquid/unrealized_pnl.
        # open_interest       = sum(amount) — total token units held
        # open_interest_value = sum(size)   — total USD notional
        sql = """
            SELECT
                toUnixTimestamp(bucket)   AS bucket,
                sum(latest_amount)        AS open_interest,
                sum(latest_size)          AS open_interest_value
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
    else:
        sql = """
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
        """
    rows = await ch.query(sql, parameters=args)
    series = [
        {"time": int(r[0]), "open_interest": float(r[1]), "open_interest_value": float(r[2])}
        for r in rows.result_rows
    ]
    return response.json({
        "token": args["token"], "exchange": exchange,
        "interval": args["interval"], "series": series,
    })


@bp.get("/long_short_ratios")
async def long_short_ratios(request):
    args, err = _validate(request)
    if err is not None:
        return err
    ch = await client()
    rows = await ch.query(
        """
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
        """,
        parameters=args,
    )
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
    return response.json({"token": args["token"], "interval": args["interval"], "series": series})


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
