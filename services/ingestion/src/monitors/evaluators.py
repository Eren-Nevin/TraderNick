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

import logging
from datetime import datetime, timezone

from clickhouse import async_client

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


# ── price_change (user widgets) ────────────────────────────────────────────

async def eval_price_change(rule: dict) -> list[dict]:
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

_alert_buckets: dict[tuple, int] = {}


async def eval_price_alert(rule: dict) -> list[dict]:
    p = rule.get("params") or {}
    alerts = p.get("alerts") or []
    title = str(rule.get("title") or "Price alert").strip() or "Price alert"
    global_tokens = [str(t).strip().upper() for t in (p.get("tokens") or []) if str(t).strip()]
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    # Which alerts are DUE this tick (their wall-clock window bucket rolled over).
    due: list[dict] = []
    for a in alerts:
        aid = str(a.get("id") or "")
        window_s = int(a.get("window_s") or 0)
        threshold = abs(float(a.get("threshold_pct") or 0))
        if not aid or window_s <= 0 or threshold <= 0:
            continue
        # Only fire in the FIRST minute of each wall-clock window bucket, so
        # alerts align to round times (5m → :00/:05/…, 1h → top of the hour)
        # regardless of when the alert was created or the monitor last restarted.
        # (Unix epoch is aligned to these boundaries, so epoch // window_s and
        # epoch % window_s give clean wall-clock buckets.)
        if now_epoch % window_s >= 60:
            continue
        bucket = now_epoch // window_s
        key = (rule["rule_id"], aid)
        if _alert_buckets.get(key) == bucket:
            continue  # already fired for this bucket
        _alert_buckets[key] = bucket
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
            moves.append((token, (float(cur) / float(past) - 1.0) * 100.0))
        wl = _humanize_seconds(window_s)
        for a in alist:
            thr = a["threshold"]
            limit = int(a.get("limit") or 0)  # 0 = report all
            hits = [(token, pct) for token, pct in moves if abs(pct) >= thr]
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
                        hits: list[tuple[str, float]], limit: int = 0) -> str:
    """Readable multi-line Telegram message: a header, then Gainers / Losers
    sections (biggest move first), one token per line with a ▲/▼ arrow (the
    arrow shows direction, so the % carries no sign). `limit` (0 = all) is
    per-side — the top-N gainers AND the top-N losers."""
    total = len(hits)
    all_ups = sorted([h for h in hits if h[1] >= 0], key=lambda x: -x[1])
    all_downs = sorted([h for h in hits if h[1] < 0], key=lambda x: x[1])
    ups = all_ups[:limit] if limit > 0 else all_ups
    downs = all_downs[:limit] if limit > 0 else all_downs
    lines = [f"🔔 {title}", f"≥{thr:g}% move in {wl} · {total} token{'s' if total != 1 else ''}"]
    if ups:
        lines += ["", f"📈 Gainers ({len(ups)})"]
        lines += [f"▲ {tok}  {abs(pct):.2f}%" for tok, pct in ups]
    if downs:
        lines += ["", f"📉 Losers ({len(downs)})"]
        lines += [f"▼ {tok}  {abs(pct):.2f}%" for tok, pct in downs]
    return "\n".join(lines)


# ── admin_job_fail ─────────────────────────────────────────────────────────

async def eval_admin_job_fail(rule: dict) -> list[dict]:
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

async def eval_admin_stale_data(rule: dict) -> list[dict]:
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
    "price_change": eval_price_change,   # legacy single-condition; kept for compat
    "admin_job_fail": eval_admin_job_fail,
    "admin_stale_data": eval_admin_stale_data,
}

# Kinds whose evaluator handles its OWN cadence/dedup and returns only what
# should fire NOW → the engine dispatches directly, no edge/cooldown state.
STATELESS_KINDS = {"price_alert"}
