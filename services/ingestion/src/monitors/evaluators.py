"""Rule evaluators — one per rule `kind`.

An evaluator inspects ClickHouse and returns the subjects currently satisfying
its condition, as a list of firing items:

    {"entity": str, "message": str, "group": str | None}

`entity` is the independently-firing subject (a token / job_id / stream name) —
the unit edge+cooldown state is tracked against. `group` (admin rules only) tells
the engine which static admin topic to route to; user rules ignore it and use
the rule's own topic_id.

Evaluators return *currently-true* subjects only; the engine (evaluate.py) diffs
against notification_state to decide what actually fires.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

from clickhouse import async_client

log = logging.getLogger(__name__)

# data_server (same docker network) — the monitor reuses its group_snapshot
# endpoint for Positions Alert so the Live position logic isn't duplicated.
_DATA_SERVER_URL = os.environ.get("DATA_SERVER_URL", "http://data_server:8000").rstrip("/")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


def _fmt_price(p: float) -> str:
    """Magnitude-aware price: more decimals for cheaper tokens, never scientific."""
    a = abs(p)
    if a >= 1000:
        return f"${p:,.2f}"
    if a >= 1:
        return f"${p:,.3f}"
    if a >= 0.01:
        return f"${p:.4f}"
    if a >= 0.0001:
        return f"${p:.6f}"
    return f"${p:.8f}"


# ── price_change (user widgets) ────────────────────────────────────────────

async def eval_price_change(rule: dict, slot_epoch: int = 0) -> list[dict]:
    """Fire tokens whose close moved ≥ threshold_pct (abs) over window_s, from
    hl_ohlcv_1m. Reuses the argMax/argMin close-ratio approach from
    data_server routes/hyperliquid.py group_snapshot."""
    p = rule.get("params") or {}
    threshold = abs(float(p.get("threshold_pct", 10) or 10))
    window_s = int(p.get("window_s", 3600) or 3600)
    tokens = p.get("tokens") or []
    tokens = [str(t).strip().upper() for t in tokens if str(t).strip()]

    where = ["time >= now() - INTERVAL {w:UInt32} SECOND", "time <= now()"]
    params: dict = {"w": window_s}
    if tokens:
        where.append("token IN {toks:Array(String)}")
        params["toks"] = tokens

    ch = await async_client()
    res = await ch.query(
        "SELECT token, argMax(close, time) AS cur, argMin(close, time) AS past "
        "FROM tradernick.hl_ohlcv_1m "
        f"WHERE {' AND '.join(where)} GROUP BY token",
        parameters=params,
    )
    out: list[dict] = []
    win_label = _humanize_seconds(window_s)
    for token, cur, past in res.result_rows:
        if not past:
            continue
        pct = (float(cur) / float(past) - 1.0) * 100.0
        if abs(pct) >= threshold:
            direction = "up" if pct >= 0 else "down"
            out.append({
                "entity": token,
                "group": None,
                "message": (
                    f"⚠️ {token} {direction} {_fmt_pct(pct)} in {win_label}\n"
                    f"price {float(past):.6g} → {float(cur):.6g}"
                ),
            })
    return out


# ── price_alert (multi-condition widget) ───────────────────────────────────
# A Price Alert widget = one rule + one topic, holding a LIST of alert
# conditions (each: threshold_pct + window_s). All conditions fire into the
# widget's single topic. The rule itself runs at the base 1-min cadence, but
# each alert only does real work once per its own window (wall-clock bucket):
# a 1h alert checks once/hour, the other 59 minute-ticks are a dict lookup.
# Stateless — each due check evaluates the last window and fires matching
# tokens; the rolling non-overlapping windows naturally avoid repeat spam.

async def eval_price_alert(rule: dict, slot_epoch: int = 0, force: bool = False) -> list[dict]:
    p = rule.get("params") or {}
    alerts = p.get("alerts") or []
    title = str(rule.get("title") or "Price alert").strip() or "Price alert"
    global_tokens = [str(t).strip().upper() for t in (p.get("tokens") or []) if str(t).strip()]
    # Use the SLOT's wall-clock epoch (not now()) so a late-running slot still
    # fires the alerts that belong to this slot.
    now_epoch = int(slot_epoch or datetime.now(timezone.utc).timestamp())

    # Which alerts fire THIS slot: an alert fires on its own CADENCE boundary and
    # measures the price change over its (separate) WINDOW/lookback — e.g. a 1h
    # cadence checking the 1d change. cadence_s falls back to window_s for legacy
    # alerts that predate the split (window == cadence).
    due: list[dict] = []
    for a in alerts:
        aid = str(a.get("id") or "")
        window_s = int(a.get("window_s") or 0)
        cadence_s = int(a.get("cadence_s") or window_s or 0)
        threshold = abs(float(a.get("threshold_pct") or 0))
        if not aid or window_s <= 0 or cadence_s <= 0 or threshold <= 0:
            continue
        if not force and now_epoch % cadence_s >= 60:  # not this alert's firing slot
            continue
        due.append({"id": aid, "window_s": window_s, "threshold": threshold,
                    "limit": int(a.get("limit") or 0)})
    if not due:
        return []

    # One CH query per DISTINCT due window (multiple alerts may share a window).
    by_window: dict[int, list[dict]] = {}
    for a in due:
        by_window.setdefault(a["window_s"], []).append(a)

    ch = await async_client()
    out: list[dict] = []
    for window_s, alist in by_window.items():
        where = ["time >= now() - INTERVAL {w:UInt32} SECOND", "time <= now()"]
        params: dict = {"w": window_s}
        if global_tokens:
            where.append("token IN {toks:Array(String)}")
            params["toks"] = global_tokens
        res = await ch.query(
            "SELECT token, argMax(close, time) AS cur, argMin(close, time) AS past "
            "FROM tradernick.hl_ohlcv_1m "
            f"WHERE {' AND '.join(where)} GROUP BY token",
            parameters=params,
        )
        moves = []
        for token, cur, past in res.result_rows:
            if not past:
                continue
            moves.append((token, (float(cur) / float(past) - 1.0) * 100.0, float(cur)))
        wl = _humanize_seconds(window_s)
        for a in alist:
            thr = a["threshold"]
            limit = int(a.get("limit") or 0)  # 0 = report all
            hits = [(token, pct, price) for token, pct, price in moves if abs(pct) >= thr]
            if not hits:
                continue
            # ONE aggregated message per alert (entity == alert id → a single
            # dispatch per due check), formatted into gainers/losers sections.
            out.append({
                "entity": a["id"],
                "group": None,
                "message": _format_price_alert(title, thr, wl, hits, limit),
            })
    return out


def _format_price_alert(title: str, thr: float, wl: str,
                        hits: list[tuple[str, float, float]], limit: int = 0) -> str:
    """Readable multi-line Telegram message: a header, then Gainers / Losers
    sections (biggest move first), one token per line with a ▲/▼ arrow (the
    arrow shows direction, so the % carries no sign) and the current price in
    parentheses. `limit` (0 = all) is per-side — top-N gainers AND top-N losers.
    `hits` items are (token, pct, price)."""
    total = len(hits)
    all_ups = sorted([h for h in hits if h[1] >= 0], key=lambda x: -x[1])
    all_downs = sorted([h for h in hits if h[1] < 0], key=lambda x: x[1])
    ups = all_ups[:limit] if limit > 0 else all_ups
    downs = all_downs[:limit] if limit > 0 else all_downs
    lines = [f"🔔 {title}", f"≥{thr:g}% move in {wl} · {total} token{'s' if total != 1 else ''}"]
    if ups:
        lines += ["", f"📈 Gainers ({len(ups)})"]
        lines += [f"▲ {tok}  {abs(pct):.2f}% ({_fmt_price(price)})" for tok, pct, price in ups]
    if downs:
        lines += ["", f"📉 Losers ({len(downs)})"]
        lines += [f"▼ {tok}  {abs(pct):.2f}% ({_fmt_price(price)})" for tok, pct, price in downs]
    return "\n".join(lines)


# ── positions_alert (Group-Snapshot-based widget) ──────────────────────────
# One rule + one topic. On its own cadence it pulls the wallet group's current
# Live positions (reusing data_server's group_snapshot endpoint), ranks tokens
# by the chosen criteria (Net Long count or Net Size $), and reports the top-N
# most-long AND most-short as two sections. Stateless: it sends a fresh report
# each cadence tick (wall-clock aligned).

def _ratio(n_long: int, n_short: int) -> float:
    """Long-concentration ratio, Laplace-smoothed to avoid div-by-zero and to
    order sensibly (7/1 → 4 above 9/3 → 2.5, and 7/0 → 8 above 7/1)."""
    return (n_long + 1) / (n_short + 1)


def _fmt_money(n: float) -> str:
    a = abs(n)
    s = "-" if n < 0 else "+"
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.1f}K"
    return f"{s}${a:.0f}"


async def eval_positions_alert(rule: dict, slot_epoch: int = 0) -> list[dict]:
    # The monitor's slot scheduler decides WHEN this runs (at the report cadence);
    # this just builds the current report. `slot_epoch` is accepted for a uniform
    # evaluator signature but unused (the data is "as of now").
    p = rule.get("params") or {}
    group_id = str(p.get("group_id") or "").strip()
    if not group_id:
        return []
    criteria = "net_size" if str(p.get("criteria")) == "net_size" else "net_long"
    top_n = max(int(p.get("top_n") or 5), 1)
    staleness = str(p.get("staleness") or "1d")
    title = str(rule.get("title") or "Positions alert").strip() or "Positions alert"

    # Reuse the canonical Live group_snapshot aggregation (per-token n_long/
    # n_short/long_usd/short_usd/entry). No SQL duplication.
    url = (f"{_DATA_SERVER_URL}/hyperliquid/group_snapshot"
           f"?group={group_id}&staleness={staleness}&as_of=live")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            rows = resp.json().get("rows", []) or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("positions_alert fetch failed (%s): %s", url, exc)
        return []

    toks = []
    for row in rows:
        n_long = int(row.get("n_long") or 0)
        n_short = int(row.get("n_short") or 0)
        if n_long == 0 and n_short == 0:
            continue
        long_usd = float(row.get("long_usd") or 0)
        short_usd = float(row.get("short_usd") or 0)
        toks.append({
            "token": row.get("token", "?"),
            "n_long": n_long, "n_short": n_short,
            "net_long": n_long - n_short,
            "net_size": long_usd - short_usd,
            "entry": float(row.get("entry") or 0),
        })
    if not toks:
        return []

    if criteria == "net_size":
        keyfn = lambda t: (t["net_size"], _ratio(t["n_long"], t["n_short"]))
    else:  # net_long: primary net-long count, tiebreak by long/short ratio
        keyfn = lambda t: (t["net_long"], _ratio(t["n_long"], t["n_short"]))

    top_longs = sorted(toks, key=keyfn, reverse=True)[:top_n]
    long_set = {t["token"] for t in top_longs}
    top_shorts = [t for t in sorted(toks, key=keyfn) if t["token"] not in long_set][:top_n]

    msg = _format_positions_alert(title, criteria, top_n, top_longs, top_shorts)
    return [{"entity": rule["rule_id"], "group": None, "message": msg}]


def _format_positions_alert(title: str, criteria: str, top_n: int,
                            top_longs: list[dict], top_shorts: list[dict]) -> str:
    crit_label = "Net Size" if criteria == "net_size" else "Net Long"
    lines = [f"🔔 {title}", f"Top {top_n} by {crit_label}"]

    def line(t: dict) -> str:
        side = "long" if t["net_size"] >= 0 else "short"
        return (f"{t['token']}  {_fmt_money(t['net_size'])}  {side}  "
                f"{t['net_long']:+d} ({t['n_long']}/{t['n_short']})")

    if top_longs:
        lines += ["", "📈 Top Longs"]
        lines += [line(t) for t in top_longs]
    if top_shorts:
        lines += ["", "📉 Top Shorts"]
        lines += [line(t) for t in top_shorts]
    return "\n".join(lines)


# ── positions_change (Trading-Pit-Overview-based widget) ────────────────────
# One rule + one topic. On its own cadence it pulls the wallet group's
# position-change flow over a lookback window (reusing data_server's
# positions_change endpoint), ranks tokens by a criteria (Net Pos Change / Net
# Open Long / Net Flip) in $ or wallet-count terms, and reports the top-N most
# positive AND most negative as two sections. Each token line shows all three
# metrics as "$value (walletΔ)". Stateless, wall-clock-cadence-gated.

_PC_METRICS = {
    "net_pos_change": "Net Pos Change",
    "net_open_long": "Net Open Long",
    "net_flip": "Net Flip",
}


async def eval_positions_change(rule: dict, slot_epoch: int = 0) -> list[dict]:
    # The monitor's slot scheduler decides WHEN this runs (at the report cadence);
    # this just builds the current report over the lookback window.
    p = rule.get("params") or {}
    # group_id is OPTIONAL — empty = market-wide (all wallets), like the Trading
    # Pit widget's "All wallets". The endpoint omits the membership filter then.
    group_id = str(p.get("group_id") or "").strip()
    criteria = str(p.get("criteria") or "net_pos_change")
    if criteria not in _PC_METRICS:
        criteria = "net_pos_change"
    rank_by = "wallets" if str(p.get("rank_by")) == "wallets" else "usd"
    top_n = max(int(p.get("top_n") or 5), 1)
    window = str(p.get("window") or "15m")
    title = str(rule.get("title") or "Positions change").strip() or "Positions change"

    url = f"{_DATA_SERVER_URL}/hyperliquid/positions_change?lookback={window}"
    if group_id:
        url += f"&group={group_id}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as hc:
            resp = await hc.get(url)
            resp.raise_for_status()
            rows = resp.json().get("rows", []) or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("positions_change fetch failed (%s): %s", url, exc)
        return []

    def val(row: dict) -> float:
        return row.get(criteria, {}).get(rank_by, 0) or 0

    toks = [r for r in rows if val(r) != 0]
    if not toks:
        return []
    top_pos = [t for t in sorted(toks, key=val, reverse=True)[:top_n] if val(t) > 0]
    pos_set = {t["token"] for t in top_pos}
    top_neg = [t for t in sorted(toks, key=val) if t["token"] not in pos_set and val(t) < 0][:top_n]

    msg = _format_positions_change(title, criteria, rank_by, window, top_pos, top_neg)
    return [{"entity": rule["rule_id"], "group": None, "message": msg}]


def _format_positions_change(title: str, criteria: str, rank_by: str, window: str,
                             top_pos: list[dict], top_neg: list[dict]) -> str:
    basis = "$" if rank_by == "usd" else "wallets"
    lines = [f"🔔 {title}", f"Top by {_PC_METRICS[criteria]} ({basis}) · {window}"]

    def line(t: dict) -> str:
        a = t["net_pos_change"]; b = t["net_open_long"]; c = t["net_flip"]
        return (f"{t['token']}  Pos {_fmt_money(a['usd'])} ({a['wallets']:+d})  "
                f"Open {_fmt_money(b['usd'])} ({b['wallets']:+d})  "
                f"Flip {_fmt_money(c['usd'])} ({c['wallets']:+d})")

    if top_pos:
        lines += ["", "📈 Top Positive"]
        lines += [line(t) for t in top_pos]
    if top_neg:
        lines += ["", "📉 Top Negative"]
        lines += [line(t) for t in top_neg]
    return "\n".join(lines)


# ── backtracker_alert (Backtracker-Leaderboard-based widget) ────────────────
# One rule + one topic. On its own cadence it pulls the MARKET-WIDE (no wallet
# group) per-token flow over a lookback window (reusing data_server's
# backtracker_leaderboard endpoint), ranks tokens by Spot VD % or Vol Δ%, and
# reports the top-N most-positive AND most-negative as two sections. Each token
# line shows BOTH metrics regardless of the ranking criteria. Stateless,
# wall-clock-cadence-gated.

_BLA_METRICS = {"spot_vd_pct": "Spot VD %", "vol_pct": "Vol Δ%"}


async def eval_backtracker_alert(rule: dict, slot_epoch: int = 0) -> list[dict]:
    # The monitor's slot scheduler decides WHEN this runs (at the report cadence);
    # this just builds the current report over the lookback window.
    p = rule.get("params") or {}
    criteria = str(p.get("criteria") or "spot_vd_pct")
    if criteria not in _BLA_METRICS:
        criteria = "spot_vd_pct"
    top_n = max(int(p.get("top_n") or 5), 1)
    lookback = str(p.get("lookback") or "1h")
    title = str(rule.get("title") or "Backtracker alert").strip() or "Backtracker alert"

    # No group → market-wide leaderboard. as_of=now for the freshest flow.
    url = (f"{_DATA_SERVER_URL}/hyperliquid/backtracker_leaderboard"
           f"?lookback={lookback}&as_of=now")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as hc:
            resp = await hc.get(url)
            resp.raise_for_status()
            rows = resp.json().get("rows", []) or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("backtracker_alert fetch failed (%s): %s", url, exc)
        return []

    def val(row: dict):
        # rank by the chosen criteria; skip tokens where it's null (e.g. no
        # Binance spot pair → spot_vd_pct is None).
        v = row.get(criteria)
        return None if v is None else float(v)

    toks = [r for r in rows if val(r) is not None]
    if not toks:
        return []
    top_pos = [t for t in sorted(toks, key=val, reverse=True)[:top_n] if val(t) > 0]
    pos_set = {t["token"] for t in top_pos}
    top_neg = [t for t in sorted(toks, key=val) if t["token"] not in pos_set and val(t) < 0][:top_n]

    msg = _format_backtracker_alert(title, criteria, lookback, top_pos, top_neg)
    return [{"entity": rule["rule_id"], "group": None, "message": msg}]


def _bla_pct(v) -> str:
    """A percent field that may be null (missing Binance spot pair / zero denom)."""
    return "—" if v is None else f"{float(v):+.2f}%"


def _format_backtracker_alert(title: str, criteria: str, lookback: str,
                              top_pos: list[dict], top_neg: list[dict]) -> str:
    lines = [f"🔔 {title}", f"Top by {_BLA_METRICS[criteria]} · {lookback}"]

    def line(t: dict) -> str:
        return (f"{t['token']}  Spot VD {_bla_pct(t.get('spot_vd_pct'))}  "
                f"Vol Δ {_bla_pct(t.get('vol_pct'))}")

    if top_pos:
        lines += ["", "📈 Top Positive"]
        lines += [line(t) for t in top_pos]
    if top_neg:
        lines += ["", "📉 Top Negative"]
        lines += [line(t) for t in top_neg]
    return "\n".join(lines)


# ── modular_alert (Modular Token Leaderboard) ───────────────────────────────
# One rule + one topic holding a LIST of modules, each = one of the standalone
# notif kinds' data path. Every module yields a (longs, shorts) token set; the
# result is the INTERSECTION of all modules' longs and of all modules' shorts
# (cap-then-intersect: rank modules pre-cap each side at the shared top-N,
# price_move modules are pure threshold filters). The final lists are ordered by
# the PRIMARY module's signed metric (longs desc, shorts most-negative first) and
# truncated to top-N. Columns (≤4) are selected per module. Because it's an AND,
# longs/shorts counts are naturally asymmetric. Stateless, cadence-gated.
#
# Each module helper returns (longs:set, shorts:set, colvals:dict, rank:dict):
#   colvals[token] = {colKey: rendered_string}; rank[token] = signed float metric.

async def _mod_price_move(ch, m: dict, top_n: int):
    window_s = int(m.get("window_s") or 0)
    thr = abs(float(m.get("threshold_pct") or 0))
    res = await ch.query(
        "SELECT token, argMax(close, time) AS cur, argMin(close, time) AS past "
        "FROM tradernick.hl_ohlcv_1m "
        "WHERE time >= now() - INTERVAL {w:UInt32} SECOND AND time <= now() GROUP BY token",
        parameters={"w": window_s},
    )
    longs, shorts, colvals, rank = set(), set(), {}, {}
    for token, cur, past in res.result_rows:
        if not past:
            continue
        pct = (float(cur) / float(past) - 1.0) * 100.0
        rank[token] = pct
        colvals[token] = {"dpct": _fmt_pct(pct)}
        if pct >= thr:
            longs.add(token)
        elif pct <= -thr:
            shorts.add(token)
    return longs, shorts, colvals, rank


async def _mod_positions(hc, m: dict, top_n: int):
    url = (f"{_DATA_SERVER_URL}/hyperliquid/group_snapshot"
           f"?group={m.get('group_id')}&staleness={m.get('staleness')}&as_of=live")
    resp = await hc.get(url)
    resp.raise_for_status()
    rows = resp.json().get("rows", []) or []
    crit = m.get("criteria")
    toks = []
    for row in rows:
        n_long = int(row.get("n_long") or 0)
        n_short = int(row.get("n_short") or 0)
        if n_long == 0 and n_short == 0:
            continue
        long_usd = float(row.get("long_usd") or 0)
        short_usd = float(row.get("short_usd") or 0)
        toks.append({"token": row.get("token", "?"), "n_long": n_long, "n_short": n_short,
                     "net_long": n_long - n_short, "net_size": long_usd - short_usd})
    metric = (lambda t: t["net_size"]) if crit == "net_size" else (lambda t: t["net_long"])
    keyfn = lambda t: (metric(t), _ratio(t["n_long"], t["n_short"]))
    longs = {t["token"] for t in [x for x in sorted(toks, key=keyfn, reverse=True) if metric(x) > 0][:top_n]}
    shorts = {t["token"] for t in [x for x in sorted(toks, key=keyfn) if metric(x) < 0][:top_n]}
    colvals, rank = {}, {}
    for t in toks:
        rank[t["token"]] = float(metric(t))
        colvals[t["token"]] = {
            "net_long": f"{t['net_long']:+d}",
            "net_size": _fmt_money(t["net_size"]),
            "ls": f"{t['n_long']}/{t['n_short']}",
        }
    return longs, shorts, colvals, rank


def _mod_pc_cell(a: dict) -> str:
    return f"{_fmt_money(a.get('usd') or 0)} ({int(a.get('wallets') or 0):+d})"


async def _mod_positions_change(hc, m: dict, top_n: int):
    url = (f"{_DATA_SERVER_URL}/hyperliquid/positions_change"
           f"?lookback={m.get('window')}&group={m.get('group_id')}")
    resp = await hc.get(url)
    resp.raise_for_status()
    rows = resp.json().get("rows", []) or []
    crit, rank_by = m.get("criteria"), m.get("rank_by")
    val = lambda r: (r.get(crit, {}) or {}).get(rank_by, 0) or 0
    toks = [r for r in rows if val(r) != 0]
    longs = {r["token"] for r in [x for x in sorted(toks, key=val, reverse=True) if val(x) > 0][:top_n]}
    shorts = {r["token"] for r in [x for x in sorted(toks, key=val) if val(x) < 0][:top_n]}
    colvals, rank = {}, {}
    for r in toks:
        rank[r["token"]] = float(val(r))
        colvals[r["token"]] = {
            "net_pos_change": _mod_pc_cell(r.get("net_pos_change") or {}),
            "net_open_long": _mod_pc_cell(r.get("net_open_long") or {}),
            "net_flip": _mod_pc_cell(r.get("net_flip") or {}),
        }
    return longs, shorts, colvals, rank


async def _mod_spot_vd(hc, m: dict, top_n: int):
    url = (f"{_DATA_SERVER_URL}/hyperliquid/backtracker_leaderboard"
           f"?lookback={m.get('lookback')}&as_of=now")
    resp = await hc.get(url)
    resp.raise_for_status()
    rows = resp.json().get("rows", []) or []
    crit = m.get("criteria")
    val = lambda r: (None if r.get(crit) is None else float(r.get(crit)))
    toks = [r for r in rows if val(r) is not None]
    longs = {r["token"] for r in [x for x in sorted(toks, key=val, reverse=True) if val(x) > 0][:top_n]}
    shorts = {r["token"] for r in [x for x in sorted(toks, key=val) if val(x) < 0][:top_n]}
    colvals, rank = {}, {}
    for r in toks:
        rank[r["token"]] = float(val(r))
        colvals[r["token"]] = {
            "spot_vd_pct": _bla_pct(r.get("spot_vd_pct")),
            "vol_pct": _bla_pct(r.get("vol_pct")),
        }
    return longs, shorts, colvals, rank


_MOD_RUNNERS = {
    "positions": _mod_positions,
    "positions_change": _mod_positions_change,
    "spot_vd": _mod_spot_vd,
}


def _mod_col_label(m: dict, ck: str) -> str:
    t = m.get("type")
    if t == "price_move":
        return "Δ" + _humanize_seconds(int(m.get("window_s") or 0))
    return {
        "net_long": "NetL", "net_size": "NetSz", "ls": "L/S",
        "net_pos_change": "Pos", "net_open_long": "Open", "net_flip": "Flip",
        "spot_vd_pct": "SpotVD", "vol_pct": "VolΔ",
    }.get(ck, ck)


async def eval_modular_alert(rule: dict, slot_epoch: int = 0) -> list[dict]:
    p = rule.get("params") or {}
    modules = p.get("modules") or []
    if not modules:
        return []
    top_n = max(int(p.get("top_n") or 10), 1)
    columns = p.get("columns") or []
    primary = str(p.get("primary") or "")
    title = str(rule.get("title") or "Modular leaderboard").strip() or "Modular leaderboard"

    ch = await async_client()

    async def run(m: dict):
        t = m.get("type")
        if t == "price_move":
            return await _mod_price_move(ch, m, top_n)
        runner = _MOD_RUNNERS.get(t)
        if runner is None:
            return set(), set(), {}, {}
        return await runner(hc, m, top_n)

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as hc:
        results = await asyncio.gather(*[run(m) for m in modules], return_exceptions=True)

    # Any module that errored → abort the whole run. A missing module would
    # silently loosen the AND, so we'd rather send nothing this tick.
    mods: list[tuple[dict, tuple]] = []
    for m, r in zip(modules, results):
        if isinstance(r, Exception):
            log.warning("modular_alert module %s failed: %s", m.get("type"), r)
            return []
        mods.append((m, r))

    inter_long = set.intersection(*[r[0] for _, r in mods])
    inter_short = set.intersection(*[r[1] for _, r in mods])
    if not inter_long and not inter_short:
        return []

    # Primary module orders the final lists (longs desc, shorts most-negative first).
    prim = next((r for (m, r) in mods if m["id"] == primary), None) or mods[0][1]
    prank = prim[3]
    longs = sorted(inter_long, key=lambda t: prank.get(t, 0.0), reverse=True)[:top_n]
    shorts = sorted(inter_short, key=lambda t: prank.get(t, 0.0))[:top_n]

    msg = _format_modular(title, top_n, mods, columns, longs, shorts)
    return [{"entity": rule["rule_id"], "group": None, "message": msg}]


def _format_modular(title: str, top_n: int, mods: list[tuple[dict, tuple]],
                    columns: list, longs: list[str], shorts: list[str]) -> str:
    mby = {m["id"]: (m, r) for (m, r) in mods}
    cols = []  # (mid, ck, label)
    for c in columns:
        mid, _, ck = str(c).partition(":")
        if mid in mby:
            cols.append((mid, ck, _mod_col_label(mby[mid][0], ck)))
    n = len(mods)
    lines = [f"🔔 {title}", f"Top {top_n} · {n} module{'s' if n != 1 else ''} · ∩ (AND)"]

    def line(token: str) -> str:
        parts = [token]
        for mid, ck, label in cols:
            v = mby[mid][1][2].get(token, {}).get(ck, "—")
            parts.append(f"{label} {v}")
        return "  ".join(parts)

    if longs:
        lines += ["", f"📈 Longs ({len(longs)})"]
        lines += [line(t) for t in longs]
    if shorts:
        lines += ["", f"📉 Shorts ({len(shorts)})"]
        lines += [line(t) for t in shorts]
    return "\n".join(lines)


# ── admin_job_fail ─────────────────────────────────────────────────────────

async def eval_admin_job_fail(rule: dict, slot_epoch: int = 0) -> list[dict]:
    """Fire ingestion_jobs that have status='failed'. Scoped to failures within
    a recent lookback so the first run doesn't replay ancient history; each
    job_id then fires exactly once (it never flips back to non-failed)."""
    p = rule.get("params") or {}
    cadence_s = int(rule.get("cadence_s", 60) or 60)
    lookback_s = int(p.get("lookback_s", max(cadence_s * 5, 3600)))

    ch = await async_client()
    res = await ch.query(
        "SELECT job_id, job_type, error FROM tradernick.ingestion_jobs FINAL "
        "WHERE status = 'failed' "
        "AND coalesce(finished_at, toDateTime(updated_at)) >= now() - INTERVAL {lb:UInt32} SECOND",
        parameters={"lb": lookback_s},
    )
    out: list[dict] = []
    for job_id, job_type, error in res.result_rows:
        grp = _provider_group_for_job(job_type)
        err = (str(error)[:300] if error else "(no error text)")
        out.append({
            "entity": job_id,
            "group": grp,
            "message": (
                f"❌ Ingestion job failed\n"
                f"type: {job_type}\njob: {job_id}\n{err}"
            ),
        })
    return out


# ── admin_stale_data ───────────────────────────────────────────────────────

async def eval_admin_stale_data(rule: dict, slot_epoch: int = 0) -> list[dict]:
    """Fire enabled streams whose last successful tick is older than
    cadence_s + grace_s. Auto-covers every stream in the registry."""
    p = rule.get("params") or {}
    grace_s = int(p.get("grace_s", 300) or 300)

    try:
        from streams import STREAMS
        import ch_status
    except Exception as exc:  # noqa: BLE001
        log.warning("stale_data: streams/ch_status import failed: %s", exc)
        return []

    enabled_state = await ch_status.read_all_state()  # {name: enabled}
    status_rows = {r["name"]: r for r in await ch_status.read_all_status()}
    now = _utcnow()
    out: list[dict] = []
    for spec in STREAMS:
        enabled = enabled_state.get(spec.name, spec.enabled_default)
        if not enabled:
            continue
        st = status_rows.get(spec.name)
        if not st:
            continue  # never started — don't false-alarm on a fresh deploy
        # last healthy insert-capable tick; fall back to any tick.
        last_iso = st.get("last_success_at") or st.get("last_tick_at")
        if not last_iso:
            continue
        last = _parse_iso(last_iso)
        if last is None:
            continue
        age_s = (now - last).total_seconds()
        threshold = spec.cadence_s + grace_s
        if age_s > threshold:
            out.append({
                "entity": spec.name,
                "group": spec.group,
                "message": (
                    f"🕒 Stale data: {spec.name}\n"
                    f"no successful tick for {_humanize_seconds(int(age_s))} "
                    f"(cadence {spec.cadence_s}s + grace {grace_s}s)"
                ),
            })
    return out


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_iso(v) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    try:
        dt = datetime.fromisoformat(str(v))
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _humanize_seconds(s: int) -> str:
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s / 3600:.1f}h"


_PROVIDER_TO_GROUP: dict[str, str] | None = None


def _provider_group_for_job(job_type: str) -> str | None:
    """Map a backfill job_type → a representative admin topic group (via
    provider_registry). Multiple groups can share a provider (AAVE V2/V3/V4);
    the first group registered for that provider wins."""
    global _PROVIDER_TO_GROUP
    try:
        import provider_registry as pr
    except Exception:  # noqa: BLE001
        return None
    if _PROVIDER_TO_GROUP is None:
        inv: dict[str, str] = {}
        for grp, prov in pr.GROUP_TO_PROVIDER.items():
            inv.setdefault(prov, grp)
        _PROVIDER_TO_GROUP = inv
    provider = pr.JOB_TYPE_TO_PROVIDER.get(job_type)
    return _PROVIDER_TO_GROUP.get(provider) if provider else None


# kind → evaluator coroutine
EVALUATORS = {
    "price_alert": eval_price_alert,
    "positions_alert": eval_positions_alert,
    "positions_change": eval_positions_change,
    "backtracker_alert": eval_backtracker_alert,
    "modular_alert": eval_modular_alert,
    "price_change": eval_price_change,   # legacy single-condition; kept for compat
    "admin_job_fail": eval_admin_job_fail,
    "admin_stale_data": eval_admin_stale_data,
}

# Kinds whose evaluator handles its OWN cadence/dedup and returns only what
# should fire NOW → the engine dispatches directly, no edge/cooldown state.
STATELESS_KINDS = {"price_alert", "positions_alert", "positions_change",
                   "backtracker_alert", "modular_alert"}
