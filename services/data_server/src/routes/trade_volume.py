from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("trade_volume")

# Both tables have the same (time DateTime64(3), token, price Float64,
# amount Float64, buy Bool) shape, so the same buyer/seller + size-bucket
# SQL works for either source. Selector lets the bs/sz charts mirror the
# ohlcv pattern of one chart kind per exchange.
_TRADES_TABLE = {
    "binance": "tradernick.binance_raw_trades",
    "hl":      "tradernick.hl_trades",
}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/trade_volume")
async def trade_volume(request):
    token = request.args.get("token")
    exchange = request.args.get("exchange", "binance")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "5000"))
    if exchange not in _TRADES_TABLE:
        return response.json({"error": f"exchange must be one of {list(_TRADES_TABLE)}"}, status=400)
    try:
        under = float(request.args.get("under", "10000"))
        over = float(request.args.get("over", "100000"))
    except ValueError:
        return response.json({"error": "under/over must be numbers"}, status=400)

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if not (0 <= under < over):
        return response.json({"error": "require 0 <= under < over"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    table = _TRADES_TABLE[exchange]

    ch = await client()
    rows = await ch.query(
        f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND))           AS bucket,
            sumIf(amount * price, buy)                                                              AS buyer_taker_usd,
            sumIf(amount * price, NOT buy)                                                          AS seller_taker_usd,
            sumIf(amount * price, amount * price <  {{under:Float64}})                              AS small_usd,
            sumIf(amount * price, amount * price >= {{under:Float64}}
                                  AND amount * price <= {{over:Float64}})                          AS mid_usd,
            sumIf(amount * price, amount * price >  {{over:Float64}})                               AS large_usd,
            countIf(amount * price <  {{under:Float64}})                                            AS small_count,
            countIf(amount * price >= {{under:Float64}}
                    AND amount * price <= {{over:Float64}})                                         AS mid_count,
            countIf(amount * price >  {{over:Float64}})                                             AS large_count,
            countIf(buy)                                                                            AS buyer_count,
            countIf(NOT buy)                                                                        AS seller_count
        FROM {table} FINAL
        WHERE token = {{token:String}}
          AND time >= {{since:DateTime64(3)}}
          AND time <  {{until:DateTime64(3)}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
        """,
        parameters={
            "seconds": seconds,
            "token": token,
            "since": since_dt,
            "until": until_dt,
            "under": under,
            "over": over,
            "limit": limit,
        },
    )

    buckets = [
        {
            "time": int(r[0]),
            "buyer_taker_usd": float(r[1]),
            "seller_taker_usd": float(r[2]),
            "small_usd": float(r[3]),
            "mid_usd": float(r[4]),
            "large_usd": float(r[5]),
            "small_count": int(r[6]),
            "mid_count": int(r[7]),
            "large_count": int(r[8]),
            "buyer_count": int(r[9]),
            "seller_count": int(r[10]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "token": token,
        "exchange": exchange,
        "interval": interval,
        "under": under,
        "over": over,
        "buckets": buckets,
    })
