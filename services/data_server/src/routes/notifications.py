"""User (dashboard) side of the notification service.

The NotificationWidget lives in the dashboard; its rule must be readable by the
monitor cron, which can't see browser localStorage — so the widget syncs its
rule here, to ClickHouse. This blueprint is the dashboard-facing CRUD for
**user-scope** rules; the admin bot tokens + admin-scope rules are managed by
the ingestion admin_server. All three writers share the same RMT + soft-delete
tables (concurrent writes are safe), and the monitor only reads.

A widget rule is 1:1 with a 'widget' topic — both keyed by the widget instance
UUID (`rule_id == topic_id`), which guarantees a unique topic even when the user
drops several NotificationWidgets of the same kind on a page. Writing a rule
upserts both rows; deleting soft-deletes both.

There is no auth yet (single-user), matching wallet_pins.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client

bp = Blueprint("notifications")

RULES_TABLE = "tradernick.notification_rules"
TOPICS_TABLE = "tradernick.notification_topics"
BOTS_TABLE = "tradernick.notification_bots"
LAST_FIRED_TABLE = "tradernick.notification_last_fired"

_RULE_COLS = ["rule_id", "topic_id", "kind", "scope", "enabled", "cadence_s",
              "cooldown_s", "params", "title", "updated_at", "deleted"]
_TOPIC_COLS = ["topic_id", "bot", "kind", "title", "grp", "enabled",
               "updated_at", "deleted"]


async def ensure_tables(ch) -> None:
    """Create the rules + topics tables if absent (the monitor/admin_server also
    create them; this covers data_server starting first on a fresh cluster)."""
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {RULES_TABLE} (\n"
        "  rule_id String, topic_id String DEFAULT '', kind String, scope String,\n"
        "  enabled UInt8 DEFAULT 1, cadence_s UInt32 DEFAULT 300, cooldown_s UInt32 DEFAULT 0,\n"
        "  params String DEFAULT '{}', title String DEFAULT '',\n"
        "  updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0\n"
        ") ENGINE = ReplacingMergeTree(updated_at) ORDER BY rule_id"
    )
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {TOPICS_TABLE} (\n"
        "  topic_id String, bot String, kind String, title String,\n"
        "  grp String DEFAULT '', enabled UInt8 DEFAULT 1,\n"
        "  updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0\n"
        ") ENGINE = ReplacingMergeTree(updated_at) ORDER BY topic_id"
    )
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {LAST_FIRED_TABLE} (\n"
        "  topic_id String, message String, sent_count UInt32 DEFAULT 0,\n"
        "  fired_at DateTime64(3) DEFAULT now64(3)\n"
        ") ENGINE = ReplacingMergeTree(fired_at) ORDER BY topic_id"
    )


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_tokens(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(t).strip().upper() for t in (raw or []) if str(t).strip()]


@bp.get("/notifications/rules")
async def get_rules(_request):
    """All user-scope rules with their topic title (the widget rehydrates from
    localStorage but can reconcile against this)."""
    ch = await client()
    rows = await ch.query(
        "SELECT r.rule_id, r.topic_id, r.kind, r.enabled, r.cadence_s, r.cooldown_s,"
        " r.params, r.title, t.title, lf.fired_at, lf.message, lf.sent_count"
        f" FROM {RULES_TABLE} AS r FINAL"
        f" LEFT JOIN (SELECT topic_id, title FROM {TOPICS_TABLE} FINAL WHERE deleted = 0) AS t"
        " ON r.topic_id = t.topic_id"
        f" LEFT JOIN (SELECT topic_id, fired_at, message, sent_count FROM {LAST_FIRED_TABLE} FINAL) AS lf"
        " ON r.topic_id = lf.topic_id"
        " WHERE r.scope = 'user' AND r.deleted = 0"
    )
    out = []
    for r in rows.result_rows:
        try:
            params = json.loads(r[6]) if r[6] else {}
        except (ValueError, TypeError):
            params = {}
        # fired_at is DateTime64 (or the 1970 epoch zero when never fired).
        fired = r[9]
        fired_ms = None
        if fired is not None:
            ts = int(fired.replace(tzinfo=timezone.utc).timestamp() * 1000)
            fired_ms = ts if ts > 0 else None
        out.append({
            "rule_id": r[0], "topic_id": r[1], "kind": r[2],
            "enabled": bool(r[3]), "cadence_s": int(r[4]), "cooldown_s": int(r[5]),
            "params": params, "title": r[7], "topic_title": r[8] or r[7],
            "last_fired_at": fired_ms, "last_message": (r[10] or None) if fired_ms else None,
            "last_sent_count": int(r[11]) if fired_ms else 0,
        })
    return response.json({"rules": out})


# Price-alert timeframe labels → seconds. The alert's window doubles as its
# check cadence in the monitor (a 1h alert is evaluated once/hour).
_WINDOW_S = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
# The rule always runs at the base 1-min cadence; per-alert gating does the rest.
_BASE_CADENCE_S = 60
# Positions-alert report cadence labels → seconds, and allowed staleness values.
_PA_CADENCE_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
_PA_STALENESS = {"1h", "4h", "1d", "3d", "7d", "14d", "30d"}
# Positions-change lookback windows + report cadence + criteria.
_PCHG_WINDOWS = {"5m", "15m", "30m", "1h", "4h"}
_PCHG_CADENCE_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
_PCHG_CRITERIA = {"net_pos_change", "net_open_long", "net_flip"}


def _clean_alerts(raw) -> list[dict]:
    """Normalize the widget's alert list → [{id, threshold_pct, window_s, limit}].
    limit is how many tokens the message includes (0 = all)."""
    out: list[dict] = []
    for a in (raw or []):
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        window_s = _WINDOW_S.get(str(a.get("window") or ""))
        try:
            thr = abs(float(a.get("threshold") or 0))
        except (TypeError, ValueError):
            thr = 0.0
        lim_raw = str(a.get("limit") or "all")
        limit = int(lim_raw) if lim_raw in ("5", "10", "20") else 0
        if aid and window_s and thr > 0:
            out.append({"id": aid, "threshold_pct": thr, "window_s": window_s, "limit": limit})
    return out


@bp.put("/notifications/rules")
async def put_rule(request):
    """Create/replace a notification-widget rule + its 1:1 topic. rule_id is the
    widget instance UUID (== topic_id, so a rename keeps subscriptions intact).
    `type` selects the widget:
      price_alert (default): {alerts: [{id, threshold, window, limit}], tokens?}
      positions_alert:       {group_id, criteria, top_n, staleness, cadence}
    A widget is enabled iff it's configured AND not paused."""
    b = request.json or {}
    rule_id = str(b.get("rule_id") or "").strip()
    if not rule_id:
        return response.json({"error": "rule_id required"}, status=400)
    paused = bool(b.get("paused"))
    wtype = str(b.get("type") or "price_alert")

    if wtype == "positions_alert":
        title = str(b.get("title") or "Positions alert").strip() or "Positions alert"
        group_id = str(b.get("group_id") or "").strip()
        criteria = "net_size" if b.get("criteria") == "net_size" else "net_long"
        top_n = min(max(int(b.get("top_n") or 5), 1), 50)
        staleness = str(b.get("staleness") or "1d")
        if staleness not in _PA_STALENESS:
            staleness = "1d"
        cadence_s = _PA_CADENCE_S.get(str(b.get("cadence") or "5m"), 300)
        params = {"group_id": group_id, "criteria": criteria, "top_n": top_n,
                  "staleness": staleness}
        enabled = 1 if (group_id and not paused) else 0
        kind = "positions_alert"
        extra = {"group_id": group_id}
    elif wtype == "positions_change":
        title = str(b.get("title") or "Positions change").strip() or "Positions change"
        group_id = str(b.get("group_id") or "").strip()
        criteria = str(b.get("criteria") or "net_pos_change")
        if criteria not in _PCHG_CRITERIA:
            criteria = "net_pos_change"
        rank_by = "wallets" if b.get("rank_by") == "wallets" else "usd"
        top_n = min(max(int(b.get("top_n") or 5), 1), 50)
        window = str(b.get("window") or "15m")
        if window not in _PCHG_WINDOWS:
            window = "15m"
        cadence_s = _PCHG_CADENCE_S.get(str(b.get("cadence") or "15m"), 900)
        params = {"group_id": group_id, "criteria": criteria, "rank_by": rank_by,
                  "top_n": top_n, "window": window}
        enabled = 1 if (group_id and not paused) else 0
        kind = "positions_change"
        extra = {"group_id": group_id}
    else:
        title = str(b.get("title") or "Price alert").strip() or "Price alert"
        alerts = _clean_alerts(b.get("alerts"))
        params = {"alerts": alerts, "tokens": _clean_tokens(b.get("tokens"))}
        enabled = 1 if (alerts and not paused) else 0
        kind = "price_alert"
        cadence_s = _BASE_CADENCE_S
        extra = {"alerts": len(alerts)}

    now = _utcnow()
    ch = await client()
    await ch.insert(
        RULES_TABLE,
        [[rule_id, rule_id, kind, "user", enabled, cadence_s, 0,
          json.dumps(params), title, now, 0]],
        column_names=_RULE_COLS,
    )
    await ch.insert(
        TOPICS_TABLE,
        [[rule_id, "user", "widget", title, "", 1, now, 0]],
        column_names=_TOPIC_COLS,
    )
    return response.json({"ok": True, "rule_id": rule_id, "enabled": bool(enabled), **extra})


@bp.delete("/notifications/rules/<rule_id>")
async def delete_rule(_request, rule_id: str):
    """Soft-delete a widget's rule + topic (called when the widget is removed)."""
    from urllib.parse import unquote
    rule_id = unquote(rule_id)
    now = _utcnow()
    ch = await client()
    # read current so the tombstone row preserves the other columns
    r = await ch.query(
        f"SELECT kind, cadence_s, cooldown_s, params, title FROM {RULES_TABLE} FINAL"
        " WHERE rule_id = {r:String}", parameters={"r": rule_id})
    if r.result_rows:
        kind, cadence_s, cooldown_s, params, title = r.result_rows[0]
        await ch.insert(
            RULES_TABLE,
            [[rule_id, rule_id, kind, "user", 0, int(cadence_s), int(cooldown_s),
              params, title, now, 1]],
            column_names=_RULE_COLS,
        )
        await ch.insert(
            TOPICS_TABLE,
            [[rule_id, "user", "widget", title, "", 0, now, 1]],
            column_names=_TOPIC_COLS,
        )
    return response.json({"ok": True, "deleted": bool(r.result_rows)})


@bp.get("/notifications/bots")
async def get_bots(_request):
    """Whether the user bot is configured (for the widget's subscribe hint). No
    token is ever returned."""
    ch = await client()
    rows = await ch.query(
        f"SELECT bot, token != '' FROM {BOTS_TABLE} FINAL WHERE deleted = 0")
    configured = {r[0]: bool(r[1]) for r in rows.result_rows}
    return response.json({"user_bot_configured": configured.get("user", False)})
