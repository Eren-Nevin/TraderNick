from datetime import datetime, timezone, timedelta

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
    "binance":      "tradernick.binance_ohlcv_1m",
    "hl":           "tradernick.hl_ohlcv_1m",
    "binance_spot": "tradernick.binance_spot_ohlcv_1m",
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


@bp.get("/token_leaderboard")
async def token_leaderboard(_request):
    """Per-token snapshot for the Token Leaderboard tableview: current price,
    trailing-24h USD volume, trailing-24h average open interest (USD), and 24h
    / 7d price-change %. Binance-sourced — the fastest path and the canonical
    token roster. Covers every token with OHLCV in the trailing 8 days; the
    table sorts client-side, so we return the full set in token order.

    `argMaxIf(close, time, time <= now()-N)` picks each token's most-recent
    close at or before the lookback boundary as the change baseline; the inner
    subquery pre-multiplies volume*close because ClickHouse otherwise binds the
    columns inside `sum(volume*close)` to the outer aggregate aliases. FINAL on
    both source tables dedupes the ReplacingMergeTree rows so the volume sum and
    OI average don't double-count un-merged duplicates."""
    ch = await client()
    rows = await ch.query(
        """
        SELECT
            o.token                    AS token,
            o.price                    AS price,
            o.volume_24h_usd           AS volume_24h_usd,
            coalesce(oi.avg_oi_usd, 0) AS avg_oi_24h_usd,
            o.price_24h                AS price_24h,
            o.price_7d                 AS price_7d
        FROM (
            SELECT
                token,
                argMax(close, time)                                    AS price,
                sumIf(volume_usd_row, time >= now() - INTERVAL 24 HOUR) AS volume_24h_usd,
                argMaxIf(close, time, time <= now() - INTERVAL 24 HOUR) AS price_24h,
                argMaxIf(close, time, time <= now() - INTERVAL 7 DAY)   AS price_7d
            FROM (
                SELECT token, time, close, volume * close AS volume_usd_row
                FROM tradernick.binance_ohlcv_1m FINAL
                WHERE time >= now() - INTERVAL 8 DAY
            )
            GROUP BY token
        ) o
        LEFT JOIN (
            SELECT token, avg(open_interest_value) AS avg_oi_usd
            FROM tradernick.binance_open_interest FINAL
            WHERE time >= now() - INTERVAL 24 HOUR
            GROUP BY token
        ) oi ON o.token = oi.token
        ORDER BY o.token
        """
    )
    out = []
    for r in rows.result_rows:
        token, price, vol, oi, p24, p7 = r
        price = float(price)
        p24 = float(p24)
        p7 = float(p7)
        out.append(
            {
                "token": token,
                "price": price,
                "volume_24h_usd": float(vol),
                "avg_oi_24h_usd": float(oi),
                # null when no baseline candle exists (e.g. a token younger than
                # the lookback) so the table renders an em-dash rather than a
                # misleading move off a zero baseline.
                "pct_24h": ((price - p24) / p24 * 100.0) if p24 > 0 else None,
                "pct_7d": ((price - p7) / p7 * 100.0) if p7 > 0 else None,
            }
        )
    return response.json({"tokens": out})


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
    # Subquery computes per-row USD products before the outer aggregates
    # alias columns to their own column names — ClickHouse otherwise
    # binds `volume` / `close` inside `sum(volume * close)` to the outer
    # aliases and raises ILLEGAL_AGGREGATION.
    rows = await ch.query(
        f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            argMin(open,  time)         AS open,
            max(high)                   AS high,
            min(low)                    AS low,
            argMax(close, time)         AS close,
            sum(volume)                 AS volume,
            sum(volume_usd_row)         AS volume_usd,
            sum(buyer_taker_volume)     AS buyer_taker_volume,
            sum(buyer_taker_usd_row)    AS buyer_taker_volume_usd,
            sum(seller_taker_volume)    AS seller_taker_volume,
            sum(seller_taker_usd_row)   AS seller_taker_volume_usd,
            sum(trade_count)            AS trade_count
        FROM (
            SELECT
                time, open, high, low, close, volume,
                buyer_taker_volume, seller_taker_volume, trade_count,
                volume * close              AS volume_usd_row,
                buyer_taker_volume * close  AS buyer_taker_usd_row,
                seller_taker_volume * close AS seller_taker_usd_row
            FROM {table} FINAL
            WHERE token = {{token:String}}
              AND time >= {{since:DateTime}}
              AND time <  {{until:DateTime}}
        )
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
            "volume_usd": float(r[6]),
            "buyer_taker_volume": float(r[7]),
            "buyer_taker_volume_usd": float(r[8]),
            "seller_taker_volume": float(r[9]),
            "seller_taker_volume_usd": float(r[10]),
            "trade_count": int(r[11]),
        }
        for r in rows.result_rows
    ]
    return response.json({"token": token, "exchange": exchange, "interval": interval, "candles": candles})


@bp.get("/realized_price")
async def realized_price(request):
    """Cumulative volume-weighted average traded price (the market's "realized"
    / average entry price) per bucket, built from the 1-minute OHLCV as a fast
    approximation of the per-trade VWAP: Σ(close·volume) / Σ(volume), accumulated
    from the FIRST record we have (not just the requested window) — so each
    bucket's value reflects all history up to and including it.

    Returns one row per bucket in [since, until]:
      realized_price — cumulative Σ(close·volume)/Σ(volume) through this bucket
      current_price  — the bucket's last close (spot price then)
    The client renders realized + current, or realized + their % difference.
    Defaults to Binance spot; any _OHLCV_TABLE exchange works.
    """
    token = request.args.get("token")
    exchange = request.args.get("exchange", "binance_spot")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "200000"))

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if exchange not in _OHLCV_TABLE:
        return response.json({"error": f"exchange must be one of {list(_OHLCV_TABLE)}"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    # Lookback for the VWAP: 'all' (default, from inception) or a trailing window
    # of N days. A windowed lookback uses a RANGE frame (seconds offset, a SQL
    # literal since CH rejects params in frame bounds) and only needs data from
    # (since − lookback) onward; 'all' uses an unbounded prefix sum from
    # inception.
    lookback = request.args.get("lookback", "all")
    if lookback not in ("all", "1", "7", "14", "30", "90"):
        return response.json({"error": "lookback must be all|1|7|14|30|90"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    table = _OHLCV_TABLE[exchange]

    if lookback == "all":
        frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        lower_bound = ""
    else:
        frame = f"RANGE BETWEEN {int(lookback) * 86400} PRECEDING AND CURRENT ROW"
        lower_bound = " AND time >= {fetch_start:DateTime}"

    params = {
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    }
    if lookback != "all":
        params["fetch_start"] = since_dt - timedelta(days=int(lookback))

    ch = await client()
    # per_bucket: Σ(close·volume) + Σ(volume) per bucket. running: prefix/rolling
    # sums ordered by bucket TIMESTAMP (numeric, required for the RANGE frame).
    # The outer WHERE clips to the display window AFTER the sums are formed.
    rows = await ch.query(
        f"""
        SELECT
            ts                                            AS time,
            win_pv / nullIf(win_v, 0)                     AS realized_price,
            last_close                                    AS current_price
        FROM (
            SELECT
                ts, last_close,
                sum(pv) OVER (ORDER BY ts {frame}) AS win_pv,
                sum(v)  OVER (ORDER BY ts {frame}) AS win_v
            FROM (
                SELECT
                    toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS ts,
                    sum(close * volume)  AS pv,
                    sum(volume)          AS v,
                    argMax(close, time)  AS last_close
                FROM {table} FINAL
                WHERE token = {{token:String}} AND time < {{until:DateTime}}{lower_bound}
                GROUP BY ts
            )
        )
        WHERE ts >= toUnixTimestamp({{since:DateTime}})
        ORDER BY ts
        LIMIT {{limit:UInt32}}
        """,
        parameters=params,
    )

    series = [
        {
            "time": int(r[0]),
            "realized_price": (float(r[1]) if r[1] is not None else None),
            "current_price": float(r[2]),
        }
        for r in rows.result_rows
    ]
    return response.json({"token": token, "exchange": exchange, "interval": interval, "series": series})
