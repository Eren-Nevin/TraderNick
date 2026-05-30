from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client

bp = Blueprint("ohlcv")

INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


# Per-exchange source table. The columns are identical (we modelled
# hl_ohlcv_1m on the binance shape) so the same aggregation SQL works
# for both — we just swap the table name.
_OHLCV_TABLE = {
    "binance": "tradernick.binance_ohlcv_1m",
    "hl":      "tradernick.hl_ohlcv_1m",
}


@bp.get("/tokens")
async def tokens(_request):
    """Distinct tokens that have binance OHLCV — the canonical source for
    the dashboard's token dropdowns. HL has the same roster (driven from
    INGEST_TOKENS) so a single tokens list serves both exchanges."""
    ch = await client()
    rows = await ch.query(
        "SELECT DISTINCT token FROM tradernick.binance_ohlcv_1m ORDER BY token"
    )
    return response.json({"tokens": [r[0] for r in rows.result_rows]})


@bp.get("/ohlcv")
async def ohlcv(request):
    token = request.args.get("token")
    exchange = request.args.get("exchange", "binance")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "5000"))

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if exchange not in _OHLCV_TABLE:
        return response.json({"error": f"exchange must be one of {list(_OHLCV_TABLE)}"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    table = _OHLCV_TABLE[exchange]

    ch = await client()
    rows = await ch.query(
        f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            argMin(open,  time)       AS open,
            max(high)                 AS high,
            min(low)                  AS low,
            argMax(close, time)       AS close,
            sum(volume)               AS volume,
            sum(buyer_taker_volume)   AS buyer_taker_volume,
            sum(seller_taker_volume)  AS seller_taker_volume,
            sum(trade_count)          AS trade_count
        FROM {table}
        WHERE token = {{token:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
        """,
        parameters={
            "seconds": seconds,
            "token": token,
            "since": since_dt,
            "until": until_dt,
            "limit": limit,
        },
    )

    candles = [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "buyer_taker_volume": float(r[6]),
            "seller_taker_volume": float(r[7]),
            "trade_count": int(r[8]),
        }
        for r in rows.result_rows
    ]
    return response.json({"token": token, "exchange": exchange, "interval": interval, "candles": candles})
