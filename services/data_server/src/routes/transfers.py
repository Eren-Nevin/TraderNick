from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("transfers")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


@bp.get("/transfers/streams")
async def streams(_request):
    ch = await client()
    rows = await ch.query(
        """
        SELECT kind, chain, token, count() AS rows, min(time) AS first_seen, max(time) AS last_seen
        FROM tradernick.transfers
        GROUP BY kind, chain, token
        ORDER BY chain, token
        """
    )
    return response.json({
        "streams": [
            {
                "kind": r[0],
                "chain": r[1],
                "token": r[2],
                "rows": int(r[3]),
                "first_seen": r[4].isoformat() if r[4] else None,
                "last_seen": r[5].isoformat() if r[5] else None,
            }
            for r in rows.result_rows
        ]
    })


@bp.get("/transfers/aggregate")
async def aggregate(request):
    chain = request.args.get("chain")
    kind = request.args.get("kind")
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not chain or not token or not kind:
        return response.json({"error": "missing chain/kind/token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    ch = await client()
    rows = await ch.query(
        """
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {seconds:UInt32} SECOND)) AS bucket,
            sum(amount)             AS sum_amount,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                 AS count
        FROM tradernick.transfers
        WHERE chain = {chain:String}
          AND kind  = {kind:String}
          AND token = {token:String}
          AND time >= {since:DateTime}
          AND time <  {until:DateTime}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {limit:UInt32}
        """,
        parameters={
            "seconds": seconds,
            "chain": chain,
            "kind": kind,
            "token": token,
            "since": since_dt,
            "until": until_dt,
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
        "chain": chain,
        "kind": kind,
        "token": token,
        "interval": interval,
        "series": series,
    })
