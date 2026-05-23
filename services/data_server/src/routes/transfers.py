import asyncio
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("transfers")

# Distinct (kind, chain, token) tuples take ~2-5s to compute over the full
# transfers table once it has 100M+ rows. The list changes only when admin
# reconfigures ingestion, so cache aggressively with a TTL.
_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0
_streams_lock = asyncio.Lock()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


async def _fetch_streams() -> list[dict]:
    ch = await client()
    rows = await ch.query(
        """
        SELECT DISTINCT chain, token, kind
        FROM tradernick.transfers
        ORDER BY chain, token
        """
    )
    return [{"chain": r[0], "token": r[1], "kind": r[2]} for r in rows.result_rows]


@bp.get("/transfers/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    async with _streams_lock:
        # double-check after acquiring lock — concurrent waiters share the refresh
        now = time.monotonic()
        if _STREAMS_CACHE["value"] is None or now - _STREAMS_CACHE["at"] >= _STREAMS_TTL_SECONDS:
            _STREAMS_CACHE["value"] = await _fetch_streams()
            _STREAMS_CACHE["at"] = now
    return response.json({"streams": _STREAMS_CACHE["value"]})


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
