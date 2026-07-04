"""Fresh HL position reconstruction: a known snapshot carried forward with fills.

`hl_position_history` (raw) publishes ~25 min late from DeFiStream, and its `_15m`
/`_1h` rollups lag even more. `hl_fills` lags only ~2 min. A wallet's position
CHANGE over a window equals its net fills over that window, so:

    position(at_time) = snapshot(base_bucket)  +  net fills over [base_bucket, at_time)

gives a position fresh to ~fills-lag, anchored on the last available snapshot.
Use this anywhere the position-history lag is intolerable (the Backtracker dialog's
most-recent bar is the first caller; OI/position-over-time endpoints could adopt it
for their trailing bucket too).

All amounts are SIGNED (long +, short −). `size_usd` is valued at a mark price
(the amount is exact; the notional is amount × mark).
"""
from __future__ import annotations

from datetime import datetime


async def latest_snapshot_bucket(ch, token: str, at_or_before: datetime) -> datetime | None:
    """Latest raw hl_position_history snapshot time ≤ at_or_before for `token`
    (snapshots are on the 15-min grid). None if the token has no snapshot in range.
    Cheap — token-first ORDER BY makes max(time) an index probe."""
    r = await ch.query(
        "SELECT max(time) FROM tradernick.hl_position_history "
        "WHERE token = {tok:String} AND time <= {t:DateTime}",
        parameters={"tok": token, "t": at_or_before},
    )
    return (r.result_rows[0][0]
            if (r.result_rows and r.result_rows[0][0] is not None) else None)


async def mark_price(ch, token: str, at_time: datetime) -> float:
    """Last hl_ohlcv_1m close ≤ at_time for `token` (0 if none)."""
    r = await ch.query(
        "SELECT argMaxIf(close, time, time <= {t:DateTime}) "
        "FROM tradernick.hl_ohlcv_1m WHERE token = {tok:String}",
        parameters={"tok": token, "t": at_time},
    )
    return (float(r.result_rows[0][0])
            if (r.result_rows and r.result_rows[0][0] is not None) else 0.0)


async def positions_at(
    ch, *, token: str, at_time: datetime,
    base_bucket: datetime | None = None,
    wallets: list[str] | None = None,
    member: str = "",
    price: float | None = None,
) -> dict[str, dict]:
    """Per-wallet SIGNED net position in `token` at `at_time`, reconstructed as
    snapshot(base_bucket) + net fills over [base_bucket, at_time).

    - base_bucket: the snapshot anchor. Defaults to the latest snapshot ≤ at_time
      (so `at_time` in the future-vs-snapshots gap is filled from fills).
    - wallets: optional allowlist (recommended — the caller usually knows which
      wallets matter, e.g. those that traded).
    - member: optional membership SQL predicate on `wallet` (group filter).
    - price: mark to value size_usd (defaults to the last ohlcv close ≤ at_time).

    Returns {wallet: {amount, size_usd, base_amount, base_size_usd, base_unrealized}}
    where base_* are the snapshot at base_bucket and (amount, size_usd) are as-of
    at_time. base_* + a fill delta of 0 ⇒ amount == base_amount (no reconstruction).
    """
    if base_bucket is None:
        base_bucket = await latest_snapshot_bucket(ch, token, at_time)
        if base_bucket is None:
            return {}
    if price is None:
        price = await mark_price(ch, token, at_time)

    params: dict = {"tok": token, "b": base_bucket, "at": at_time}
    wsql = ""
    if wallets is not None:
        if not wallets:
            return {}
        wsql = " AND wallet IN {ws:Array(String)}"
        params["ws"] = list(wallets)

    # 's' = the base snapshot (signed amount/size + unrealized); 'f' = net fills
    # (signed tokens) after the snapshot up to at_time. Per-wallet sum → position.
    rows = await ch.query(
        """
        SELECT wallet,
               sum(if(tag = 's', a, 0)) AS base_amount,
               sum(if(tag = 's', u, 0)) AS base_size,
               sum(if(tag = 's', p, 0)) AS base_upnl,
               sum(if(tag = 's', a, 0)) + sum(if(tag = 'f', a, 0)) AS amount
        FROM (
            SELECT wallet, 's' AS tag,
                   argMax(amount, time) * if(side = 'long', 1, -1) AS a,
                   argMax(size,   time) * if(side = 'long', 1, -1) AS u,
                   argMax(unrealized_pnl, time)                    AS p
            FROM tradernick.hl_position_history
            WHERE token = {tok:String}
              AND time >= {b:DateTime} AND time < {b:DateTime} + INTERVAL 900 SECOND"""
        + member + wsql + """
            GROUP BY wallet, side
            UNION ALL
            SELECT wallet, 'f' AS tag,
                   sum(if(side = 'B', size, -size)) AS a, 0.0 AS u, 0.0 AS p
            FROM tradernick.hl_fills FINAL
            WHERE token = {tok:String}
              AND time >= {b:DateTime} AND time < {at:DateTime}"""
        + member + wsql + """
            GROUP BY wallet
        )
        GROUP BY wallet
        """,
        parameters=params,
    )
    return {
        w: {"amount": float(amt), "size_usd": float(amt) * price,
            "base_amount": float(ba), "base_size_usd": float(bs),
            "base_unrealized": float(bp)}
        for (w, ba, bs, bp, amt) in rows.result_rows
    }
