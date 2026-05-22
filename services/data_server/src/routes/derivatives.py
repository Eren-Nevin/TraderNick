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
    return response.json({"token": args["token"], "interval": args["interval"], "series": series})


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


@bp.get("/funding_rate")
async def funding_rate(request):
    args, err = _validate(request)
    if err is not None:
        return err
    ch = await client()
    rows = await ch.query(
        """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            argMax(rate, time) AS rate
        FROM tradernick.binance_funding_rate
        WHERE token = {token:String}
          AND time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
        """,
        parameters=args,
    )
    series = [{"time": int(r[0]), "rate": float(r[1])} for r in rows.result_rows]
    return response.json({"token": args["token"], "interval": args["interval"], "series": series})
