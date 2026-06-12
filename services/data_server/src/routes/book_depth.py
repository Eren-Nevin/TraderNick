import math
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

bp = Blueprint("book_depth")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


# Percentage levels and the column suffix the dashboard expects. Negative
# percentages are the bid side (m = minus), positive are the ask side
# (p = plus). Suffixes are zero-padded to keep alphabetical order = price
# distance order — useful when iterating client-side.
_LEVELS = [
    (-500, "m500"), (-400, "m400"), (-300, "m300"), (-200, "m200"),
    (-100, "m100"), (-20,  "m020"),
    (20,   "p020"), (100,  "p100"), (200,  "p200"), (300,  "p300"),
    (400,  "p400"), (500,  "p500"),
]


def _agg(field: str) -> str:
    """Emit `avgIf(<field>, percentage = N) AS <field_letter>_<suffix>`
    clauses for every level. The dashboard reads these directly as
    `d_m500`, `v_p100`, etc."""
    prefix = "d" if field == "depth" else "v"
    parts = []
    for pct, sfx in _LEVELS:
        parts.append(f"avgIf({field}, percentage = {pct}) AS {prefix}_{sfx}")
    return ",\n            ".join(parts)


@bp.get("/book_depth")
async def book_depth(request):
    """Binance book-depth time series. One row per bucket with one column
    per (level, field) pair — the dashboard pivots into bid/ask totals,
    per-level lines, an imbalance ratio, or a stacked-band chart based
    on the per-instance mode selector."""
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "200000"))
    if not token:
        return response.json({"error": "missing token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    args = {
        "token": token,
        "seconds": INTERVAL_SECONDS[interval],
        "since": _parse_iso(since),
        "until": _parse_iso(until),
        "limit": limit,
    }
    ch = await client()
    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            {_agg("depth")},
            {_agg("value")}
        FROM tradernick.binance_book_depth FINAL
        WHERE token = {{token:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """
    rows = await ch.query(sql, parameters=args)

    cols = ["d_" + sfx for _, sfx in _LEVELS] + ["v_" + sfx for _, sfx in _LEVELS]
    series = []
    for r in rows.result_rows:
        bucket = {"time": int(r[0])}
        for i, c in enumerate(cols, start=1):
            # avgIf returns NaN/None for empty (percentage, bucket) pairs.
            # JSON has no NaN literal, so Sanic's default encoder would
            # emit the bare `NaN` token and the dashboard's `await
            # res.json()` would throw. Coerce to 0.0 in both cases.
            v = r[i]
            if v is None:
                bucket[c] = 0.0
            else:
                f = float(v)
                bucket[c] = 0.0 if math.isnan(f) else f
        series.append(bucket)
    return response.json({
        "token": args["token"], "exchange": "binance",
        "interval": interval, "series": series,
    })
