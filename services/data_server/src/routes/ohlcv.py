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

# Spot-CVD cumulative accumulation windows → seconds (intraday 1h/4h + day-based).
CVD_LOOKBACK_SECONDS = {
    "1h": 3600, "4h": 14400,
    "1": 86400, "7": 604800, "14": 1209600, "30": 2592000, "90": 7776000,
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


@bp.get("/spot_cvd_leaderboard")
async def spot_cvd_leaderboard(request):
    """Per-token cumulative spot CVD ranking over a lookback window — powers the
    Spot CVD tableview. One row per Binance-spot token; the client sorts/limits.

    Per token over [since, until):
      cvd_token = Σ(buyer_taker_volume − seller_taker_volume)
      cvd_usd   = Σ((buyer_taker_volume − seller_taker_volume)·close)
      avg daily volume = Σ(volume)/days-traded   (USD: Σ(volume·close)/days)
      pct_24h  = close-change vs the most-recent close ≤ 24h ago
      pct_lb   = close-change vs the earliest close in the window
      ratio_*  = cvd_* / avg_volume_*  (× 100)

    The inner subquery materialises per-row products before the outer aggregates
    so ClickHouse doesn't bind columns to the outer aggregate aliases. FINAL
    dedupes the ReplacingMergeTree rows so sums don't double-count.
    """
    exchange = request.args.get("exchange", "binance_spot")
    lookback = request.args.get("lookback", "all")
    multi = request.args.get("multi") in ("1", "true", "yes")
    if exchange not in _OHLCV_TABLE:
        return response.json({"error": f"exchange must be one of {list(_OHLCV_TABLE)}"}, status=400)
    if lookback not in ("all", "1", "7", "14", "30", "90"):
        return response.json({"error": "lookback must be all|1|7|14|30|90"}, status=400)
    table = _OHLCV_TABLE[exchange]

    if multi:
        # Multi-period comparison: per token, CVD (token + USD) and $CVD/avg-daily-
        # $vol for each of 1d/7d/14d, computed in ONE 14-day scan via conditional
        # sums (cheaper than three separate windowed queries — the 14d scan covers
        # 1d/7d too). Powers the Spot CVD table's "All" view.
        ch = await client()
        rows = await ch.query(
            f"""
            SELECT
                token,
                argMax(close, time)                                     AS price,
                argMaxIf(close, time, time <= now() - INTERVAL 24 HOUR)  AS price_24h,
                sumIf(cvd_token_row, time >= now() - INTERVAL 1 DAY)     AS cvd_t_1,
                sumIf(cvd_usd_row,   time >= now() - INTERVAL 1 DAY)     AS cvd_u_1,
                sumIf(vol_usd_row,   time >= now() - INTERVAL 1 DAY)     AS vol_u_1,
                uniqExactIf(toDate(time), time >= now() - INTERVAL 1 DAY) AS days_1,
                sumIf(cvd_token_row, time >= now() - INTERVAL 7 DAY)     AS cvd_t_7,
                sumIf(cvd_usd_row,   time >= now() - INTERVAL 7 DAY)     AS cvd_u_7,
                sumIf(vol_usd_row,   time >= now() - INTERVAL 7 DAY)     AS vol_u_7,
                uniqExactIf(toDate(time), time >= now() - INTERVAL 7 DAY) AS days_7,
                sum(cvd_token_row)                                      AS cvd_t_14,
                sum(cvd_usd_row)                                        AS cvd_u_14,
                sum(vol_usd_row)                                        AS vol_u_14,
                uniqExact(toDate(time))                                 AS days_14
            FROM (
                SELECT
                    token, time, close,
                    (buyer_taker_volume - seller_taker_volume)         AS cvd_token_row,
                    (buyer_taker_volume - seller_taker_volume) * close AS cvd_usd_row,
                    volume * close                                     AS vol_usd_row
                FROM {table} FINAL
                WHERE token != '' AND time >= now() - INTERVAL 14 DAY
            )
            GROUP BY token
            ORDER BY token
            """,
        )

        def _ratio(cvd_u, vol_u, days):
            avg = float(vol_u) / (int(days) or 1)
            return (float(cvd_u) / avg * 100.0) if avg else None

        out = []
        for r in rows.result_rows:
            token, price, price_24h = r[0], float(r[1]), float(r[2])
            ct1, cu1, vu1, d1 = r[3], r[4], r[5], r[6]
            ct7, cu7, vu7, d7 = r[7], r[8], r[9], r[10]
            ct14, cu14, vu14, d14 = r[11], r[12], r[13], r[14]
            out.append({
                "token": token, "price": price,
                "pct_24h": ((price - price_24h) / price_24h * 100.0) if price_24h > 0 else None,
                "cvd_token_1": float(ct1), "cvd_usd_1": float(cu1), "ratio_usd_1": _ratio(cu1, vu1, d1),
                "cvd_token_7": float(ct7), "cvd_usd_7": float(cu7), "ratio_usd_7": _ratio(cu7, vu7, d7),
                "cvd_token_14": float(ct14), "cvd_usd_14": float(cu14), "ratio_usd_14": _ratio(cu14, vu14, d14),
            })
        return response.json({"exchange": exchange, "multi": True, "tokens": out})

    # 'all' → no lower time bound; else trailing N days. now()-24h baseline for
    # pct_24h is only meaningful when the window covers ≥24h (lookback ≥ 1d).
    since_clause = "" if lookback == "all" else "AND time >= now() - INTERVAL {days:UInt32} DAY"
    params = {} if lookback == "all" else {"days": int(lookback)}

    ch = await client()
    rows = await ch.query(
        f"""
        SELECT
            token,
            argMax(close, time)                                    AS price,
            sum(cvd_token_row)                                     AS cvd_token,
            sum(cvd_usd_row)                                       AS cvd_usd,
            sum(volume)                                            AS vol_token_total,
            sum(vol_usd_row)                                       AS vol_usd_total,
            uniqExact(toDate(time))                                AS days,
            argMaxIf(close, time, time <= now() - INTERVAL 24 HOUR) AS price_24h,
            argMin(close, time)                                    AS price_first
        FROM (
            SELECT
                token, time, close, volume,
                (buyer_taker_volume - seller_taker_volume)         AS cvd_token_row,
                (buyer_taker_volume - seller_taker_volume) * close AS cvd_usd_row,
                volume * close                                     AS vol_usd_row
            FROM {table} FINAL
            WHERE token != '' {since_clause}
        )
        GROUP BY token
        ORDER BY token
        """,
        parameters=params,
    )

    out = []
    for r in rows.result_rows:
        (token, price, cvd_token, cvd_usd, vol_token_total, vol_usd_total,
         days, price_24h, price_first) = r
        price = float(price)
        days = int(days) or 1
        avg_vol_token = float(vol_token_total) / days
        avg_vol_usd = float(vol_usd_total) / days
        cvd_token = float(cvd_token)
        cvd_usd = float(cvd_usd)
        p24 = float(price_24h)
        pf = float(price_first)
        out.append({
            "token": token,
            "price": price,
            "avg_volume_token": avg_vol_token,
            "avg_volume_usd": avg_vol_usd,
            "cvd_token": cvd_token,
            "cvd_usd": cvd_usd,
            # null when no baseline candle exists → table renders an em-dash.
            "pct_24h": ((price - p24) / p24 * 100.0) if p24 > 0 else None,
            "pct_lookback": ((price - pf) / pf * 100.0) if pf > 0 else None,
            "ratio_token": (cvd_token / avg_vol_token * 100.0) if avg_vol_token else None,
            "ratio_usd": (cvd_usd / avg_vol_usd * 100.0) if avg_vol_usd else None,
        })
    return response.json({"exchange": exchange, "lookback": lookback, "tokens": out})


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


@bp.get("/spot_cvd")
async def spot_cvd(request):
    """Spot Cumulative Volume Delta — taker buy-minus-sell volume from the
    1-minute spot OHLCV (buyer_taker / seller_taker columns).

      per-row delta = buyer_taker_volume - seller_taker_volume          (token)
                    = (buyer_taker_volume - seller_taker_volume)*close   (USD)

    mode='cumulative' (default): running sum of delta through each bucket — a
      LINE. `lookback` caps the accumulation window: 'all' (from the first
      record) or a trailing N days.
    mode='periodic': per-bucket delta sum — a BAR at the chosen `interval`.
    unit='usd' (default) or 'token'. Defaults to Binance spot; any _OHLCV_TABLE
    exchange works so more can be added later.
    """
    token = request.args.get("token")
    exchange = request.args.get("exchange", "binance_spot")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    mode = request.args.get("mode", "cumulative")
    unit = request.args.get("unit", "usd")
    lookback = request.args.get("lookback", "all")
    limit = int(request.args.get("limit", "200000"))

    if not token:
        return response.json({"error": "missing token"}, status=400)
    if exchange not in _OHLCV_TABLE:
        return response.json({"error": f"exchange must be one of {list(_OHLCV_TABLE)}"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)
    if mode not in ("cumulative", "periodic"):
        return response.json({"error": "mode must be cumulative|periodic"}, status=400)
    if unit not in ("usd", "token"):
        return response.json({"error": "unit must be usd|token"}, status=400)
    if lookback != "all" and lookback not in CVD_LOOKBACK_SECONDS:
        return response.json({"error": f"lookback must be all|{'|'.join(CVD_LOOKBACK_SECONDS)}"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    table = _OHLCV_TABLE[exchange]

    # Fixed expression (unit is validated, not interpolated user text). The
    # subquery materialises the per-row delta before the outer aggregate so
    # ClickHouse doesn't bind the columns to the outer aggregate aliases.
    row_delta = "(buyer_taker_volume - seller_taker_volume)"
    if unit == "usd":
        row_delta = f"{row_delta} * close"

    ch = await client()
    params = {
        "seconds": seconds, "token": token,
        "since": since_dt, "until": until_dt, "limit": limit,
    }

    if mode == "periodic":
        rows = await ch.query(
            f"""
            SELECT
                toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS ts,
                sum(d) AS value
            FROM (
                SELECT time, {row_delta} AS d
                FROM {table} FINAL
                WHERE token = {{token:String}}
                  AND time >= {{since:DateTime}} AND time < {{until:DateTime}}
            )
            GROUP BY ts
            ORDER BY ts
            LIMIT {{limit:UInt32}}
            """,
            parameters=params,
        )
    else:
        # Cumulative: prefix/rolling sum ordered by bucket timestamp (numeric,
        # required for the RANGE frame). Outer WHERE clips to the display window
        # AFTER the running sum is formed. Same frame logic as /realized_price.
        if lookback == "all":
            frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
            lower_bound = ""
        else:
            lb_sec = CVD_LOOKBACK_SECONDS[lookback]
            frame = f"RANGE BETWEEN {lb_sec} PRECEDING AND CURRENT ROW"
            lower_bound = " AND time >= {fetch_start:DateTime}"
            params["fetch_start"] = since_dt - timedelta(seconds=lb_sec)
        # The running sum must be computed in an INNER subquery so the display
        # clip (`WHERE ts >= since`) is applied AFTER it — SQL evaluates WHERE
        # before window functions, so clipping at the same level would make
        # 'all' accumulate only from `since` instead of from inception.
        rows = await ch.query(
            f"""
            SELECT ts, value
            FROM (
                SELECT
                    ts,
                    sum(bucket_delta) OVER (ORDER BY ts {frame}) AS value
                FROM (
                    SELECT
                        toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS ts,
                        sum(d) AS bucket_delta
                    FROM (
                        SELECT time, {row_delta} AS d
                        FROM {table} FINAL
                        WHERE token = {{token:String}} AND time < {{until:DateTime}}{lower_bound}
                    )
                    GROUP BY ts
                )
            )
            WHERE ts >= toUnixTimestamp({{since:DateTime}})
            ORDER BY ts
            LIMIT {{limit:UInt32}}
            """,
            parameters=params,
        )

    series = [{"time": int(r[0]), "value": float(r[1])} for r in rows.result_rows]
    return response.json({
        "token": token, "exchange": exchange, "interval": interval,
        "mode": mode, "unit": unit, "series": series,
    })
