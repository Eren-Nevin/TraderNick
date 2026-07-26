"""Notification service config + state, backed by ClickHouse.

The single Python module that owns the 6 notification tables. Shared by three
processes: the admin_server (bots + admin-rule CRUD), the monitor
(monitors/evaluate.py — reads rules, writes state, reads subscribers), and the
bot listener (monitors/bot.py — reads topics, writes subscriptions + auth).

Pattern is lifted from token_batches.py: a SYNCHRONOUS clickhouse_connect
client (so admin_server's async handlers can call these directly, and the
monitor/bot loops just call them — every query here is tiny), ReplacingMergeTree
+ soft-delete tables read with FINAL + `deleted = 0`, and a short TTL cache on
the hot-path reads (rules + bot tokens) so an admin edit propagates within
`_CACHE_TTL_S` with no restart. Writers bust the local cache immediately.

Tables (all created here by _ensure_all, and mirrored in
clickhouse/init/01_schema.sql for fresh volumes):
  notification_bots           — 2 rows (user/admin) → bot token
  notification_topics         — subscribe targets (widget=dynamic, admin=static)
  notification_subscriptions  — chat_id ⇄ topic
  notification_admin_auth     — admin chats that passed the secret gate
  notification_rules          — the monitor's source of truth
  notification_state          — per-(rule,entity) edge+cooldown state
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import clickhouse_connect

import config

log = logging.getLogger(__name__)

BOTS_TABLE = "tradernick.notification_bots"
TOPICS_TABLE = "tradernick.notification_topics"
SUBS_TABLE = "tradernick.notification_subscriptions"
AUTH_TABLE = "tradernick.notification_admin_auth"
RULES_TABLE = "tradernick.notification_rules"
STATE_TABLE = "tradernick.notification_state"
LAST_FIRED_TABLE = "tradernick.notification_last_fired"
# Manual "trigger now" requests (debug): the dashboard inserts a row; the monitor
# polls and fires that rule immediately, bypassing the cadence. Append-only, 1d TTL.
TRIGGERS_TABLE = "tradernick.notification_triggers"

_CACHE_TTL_S = 15.0

_lock = threading.Lock()
_client_obj = None
_tables_ready = False
_seeded = False
_rules_cache = {"value": None, "ts": 0.0}
_bots_cache = {"value": None, "ts": 0.0}


def _client():
    global _client_obj
    if _client_obj is None:
        _client_obj = clickhouse_connect.get_client(
            host=config.CLICKHOUSE_HOST,
            port=config.CLICKHOUSE_PORT,
            username=config.CLICKHOUSE_USER,
            password=config.CLICKHOUSE_PASSWORD,
            database=config.CLICKHOUSE_DB,
        )
    return _client_obj


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── table creation + seeding ───────────────────────────────────────────────

_DDL = [
    f"""CREATE TABLE IF NOT EXISTS {BOTS_TABLE} (
        bot String, token String,
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY bot""",
    f"""CREATE TABLE IF NOT EXISTS {TOPICS_TABLE} (
        topic_id String, bot String, kind String, title String,
        grp String DEFAULT '', enabled UInt8 DEFAULT 1,
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY topic_id""",
    f"""CREATE TABLE IF NOT EXISTS {SUBS_TABLE} (
        bot String, topic_id String, chat_id String, tg_username String DEFAULT '',
        subscribed UInt8 DEFAULT 1,
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY (bot, topic_id, chat_id)""",
    f"""CREATE TABLE IF NOT EXISTS {AUTH_TABLE} (
        chat_id String, authed UInt8 DEFAULT 1,
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY chat_id""",
    f"""CREATE TABLE IF NOT EXISTS {RULES_TABLE} (
        rule_id String, topic_id String DEFAULT '', kind String, scope String,
        enabled UInt8 DEFAULT 1, cadence_s UInt32 DEFAULT 300, cooldown_s UInt32 DEFAULT 0,
        params String DEFAULT '{{}}', title String DEFAULT '',
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY rule_id""",
    f"""CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
        rule_id String, entity String, state UInt8 DEFAULT 0,
        last_fired_at DateTime64(3) DEFAULT toDateTime64(0, 3),
        updated_at DateTime64(3) DEFAULT now64(3), deleted UInt8 DEFAULT 0
    ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY (rule_id, entity)""",
    # Last time each topic actually fired (dispatched), regardless of whether
    # anyone was subscribed — so a widget can show "last triggered" on its own.
    # RMT(fired_at) keeps only the most recent row per topic.
    f"""CREATE TABLE IF NOT EXISTS {LAST_FIRED_TABLE} (
        topic_id String, message String, sent_count UInt32 DEFAULT 0,
        fired_at DateTime64(3) DEFAULT now64(3)
    ) ENGINE = ReplacingMergeTree(fired_at) ORDER BY topic_id""",
    f"""CREATE TABLE IF NOT EXISTS {TRIGGERS_TABLE} (
        rule_id String, requested_at DateTime64(3) DEFAULT now64(3)
    ) ENGINE = MergeTree ORDER BY requested_at
      TTL toDateTime(requested_at) + INTERVAL 1 DAY""",
]

# Static admin rules seeded (disabled) so they appear ready to configure in the
# admin panel. cadence_s / grace tunable there.
_ADMIN_RULE_SEEDS = [
    ("admin_job_fail",   "admin_job_fail",   60,  {}),
    ("admin_stale_data", "admin_stale_data", 300, {"grace_s": 300}),
]


def _ensure_all(ch):
    global _tables_ready
    if _tables_ready:
        return
    for ddl in _DDL:
        ch.command(ddl)
    _tables_ready = True


def _distinct_stream_groups() -> list[str]:
    """Distinct StreamSpec.group values — the static admin topic set. Import is
    cheap (pure dataclass list); guarded so a missing streams module (e.g. in a
    minimal test env) doesn't break seeding."""
    try:
        from streams import STREAMS  # local import; no CH/DS side effects
    except Exception:  # noqa: BLE001
        return []
    seen: list[str] = []
    for s in STREAMS:
        if s.group not in seen:
            seen.append(s.group)
    return seen


def seed_defaults(ch=None):
    """Idempotently create the static admin topics (one per stream group) and
    the two built-in admin rules if they don't already exist. Safe to call on
    every process startup."""
    global _seeded
    if _seeded:
        return
    ch = ch or _client()
    _ensure_all(ch)
    now = _now()
    # admin topics — one per stream group
    existing = {
        r[0] for r in ch.query(
            f"SELECT topic_id FROM {TOPICS_TABLE} FINAL WHERE bot = 'admin' AND deleted = 0"
        ).result_rows
    }
    rows = []
    for grp in _distinct_stream_groups():
        tid = f"admin:{grp}"
        if tid not in existing:
            rows.append([tid, "admin", "admin", grp, grp, 1, now, 0])
    if rows:
        ch.insert(TOPICS_TABLE, rows, column_names=[
            "topic_id", "bot", "kind", "title", "grp", "enabled", "updated_at", "deleted"])
        log.info("notification_config: seeded %d admin topics", len(rows))
    # admin rules
    existing_rules = {
        r[0] for r in ch.query(
            f"SELECT rule_id FROM {RULES_TABLE} FINAL WHERE scope = 'admin' AND deleted = 0"
        ).result_rows
    }
    rrows = []
    for rule_id, kind, cadence_s, params in _ADMIN_RULE_SEEDS:
        if rule_id not in existing_rules:
            rrows.append([rule_id, "", kind, "admin", 0, cadence_s, 0,
                          json.dumps(params), rule_id.replace("_", " ").title(), now, 0])
    if rrows:
        ch.insert(RULES_TABLE, rrows, column_names=[
            "rule_id", "topic_id", "kind", "scope", "enabled", "cadence_s",
            "cooldown_s", "params", "title", "updated_at", "deleted"])
        log.info("notification_config: seeded %d admin rules", len(rrows))
    _seeded = True


# ── bots ───────────────────────────────────────────────────────────────────

def get_bot_tokens(force: bool = False) -> dict[str, str]:
    """{bot: token} for bots with a non-empty token. Cached ~15s."""
    now = time.monotonic()
    if not force:
        with _lock:
            if _bots_cache["value"] is not None and (now - _bots_cache["ts"]) < _CACHE_TTL_S:
                return _bots_cache["value"]
    try:
        ch = _client()
        _ensure_all(ch)
        rows = ch.query(
            f"SELECT bot, token FROM {BOTS_TABLE} FINAL WHERE deleted = 0"
        ).result_rows
        value = {r[0]: r[1] for r in rows if r[1]}
        with _lock:
            _bots_cache["value"] = value
            _bots_cache["ts"] = time.monotonic()
        return value
    except Exception as exc:  # noqa: BLE001
        with _lock:
            cached = _bots_cache["value"]
        if cached is not None:
            log.warning("notification bots read failed (%s); serving cache", exc)
            return cached
        log.warning("notification bots read failed (%s); no bots", exc)
        return {}


def get_bot_token(bot: str) -> str | None:
    return get_bot_tokens().get(bot)


def list_bots_masked() -> list[dict]:
    """For the admin UI: which bots are configured, token masked."""
    tokens = get_bot_tokens(force=True)
    out = []
    for bot in ("user", "admin"):
        tok = tokens.get(bot, "")
        out.append({
            "bot": bot,
            "configured": bool(tok),
            "token_masked": (tok[:6] + "…" + tok[-4:]) if len(tok) > 12 else ("•" * len(tok)),
        })
    return out


def set_bot_token(bot: str, token: str) -> dict:
    bot = (bot or "").strip().lower()
    if bot not in ("user", "admin"):
        raise ValueError("bot must be 'user' or 'admin'")
    token = (token or "").strip()
    ch = _client()
    _ensure_all(ch)
    ch.insert(BOTS_TABLE, [[bot, token, _now(), 0]],
              column_names=["bot", "token", "updated_at", "deleted"])
    with _lock:
        _bots_cache["ts"] = 0.0
    return {"bot": bot, "configured": bool(token)}


# ── topics ─────────────────────────────────────────────────────────────────

def get_topics(bot: str | None = None, kind: str | None = None,
               enabled_only: bool = True) -> list[dict]:
    ch = _client()
    _ensure_all(ch)
    where = ["deleted = 0"]
    params: dict = {}
    if bot:
        where.append("bot = {bot:String}")
        params["bot"] = bot
    if kind:
        where.append("kind = {kind:String}")
        params["kind"] = kind
    if enabled_only:
        where.append("enabled = 1")
    rows = ch.query(
        f"SELECT topic_id, bot, kind, title, grp, enabled FROM {TOPICS_TABLE} FINAL "
        f"WHERE {' AND '.join(where)} ORDER BY grp, title",
        parameters=params,
    ).result_rows
    return [{"topic_id": r[0], "bot": r[1], "kind": r[2], "title": r[3],
             "grp": r[4], "enabled": bool(r[5])} for r in rows]


def get_topic(topic_id: str) -> dict | None:
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT topic_id, bot, kind, title, grp, enabled FROM {TOPICS_TABLE} FINAL "
        f"WHERE topic_id = {{t:String}} AND deleted = 0",
        parameters={"t": topic_id},
    ).result_rows
    if not rows:
        return None
    r = rows[0]
    return {"topic_id": r[0], "bot": r[1], "kind": r[2], "title": r[3],
            "grp": r[4], "enabled": bool(r[5])}


def upsert_topic(topic_id: str, bot: str, kind: str, title: str,
                 grp: str = "", enabled: int = 1) -> dict:
    topic_id = (topic_id or "").strip()
    if not topic_id:
        raise ValueError("topic_id required")
    ch = _client()
    _ensure_all(ch)
    ch.insert(TOPICS_TABLE,
              [[topic_id, bot, kind, title or topic_id, grp, int(bool(enabled)), _now(), 0]],
              column_names=["topic_id", "bot", "kind", "title", "grp", "enabled",
                            "updated_at", "deleted"])
    return {"topic_id": topic_id, "title": title}


def delete_topic(topic_id: str) -> dict:
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT bot, kind, title, grp FROM {TOPICS_TABLE} FINAL WHERE topic_id = {{t:String}}",
        parameters={"t": topic_id},
    ).result_rows
    if not rows:
        return {"topic_id": topic_id, "deleted": False}
    bot, kind, title, grp = rows[0]
    ch.insert(TOPICS_TABLE, [[topic_id, bot, kind, title, grp, 0, _now(), 1]],
              column_names=["topic_id", "bot", "kind", "title", "grp", "enabled",
                            "updated_at", "deleted"])
    return {"topic_id": topic_id, "deleted": True}


# ── subscriptions ──────────────────────────────────────────────────────────

def get_subscribers(bot: str, topic_id: str) -> list[dict]:
    """Chats currently subscribed to a topic (the monitor's fan-out list)."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT chat_id, tg_username FROM {SUBS_TABLE} FINAL "
        f"WHERE bot = {{b:String}} AND topic_id = {{t:String}} AND subscribed = 1 AND deleted = 0",
        parameters={"b": bot, "t": topic_id},
    ).result_rows
    return [{"chat_id": r[0], "tg_username": r[1]} for r in rows]


def get_chat_subscriptions(bot: str, chat_id: str) -> set[str]:
    """Set of topic_ids a chat is currently subscribed to."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT topic_id FROM {SUBS_TABLE} FINAL "
        f"WHERE bot = {{b:String}} AND chat_id = {{c:String}} AND subscribed = 1 AND deleted = 0",
        parameters={"b": bot, "c": str(chat_id)},
    ).result_rows
    return {r[0] for r in rows}


def set_subscription(bot: str, topic_id: str, chat_id: str,
                     subscribed: bool, tg_username: str = "") -> dict:
    ch = _client()
    _ensure_all(ch)
    ch.insert(SUBS_TABLE,
              [[bot, topic_id, str(chat_id), tg_username or "", int(bool(subscribed)), _now(), 0]],
              column_names=["bot", "topic_id", "chat_id", "tg_username",
                            "subscribed", "updated_at", "deleted"])
    return {"topic_id": topic_id, "subscribed": bool(subscribed)}


# ── admin auth ─────────────────────────────────────────────────────────────

def is_admin_authed(chat_id: str) -> bool:
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT authed FROM {AUTH_TABLE} FINAL WHERE chat_id = {{c:String}} AND deleted = 0",
        parameters={"c": str(chat_id)},
    ).result_rows
    return bool(rows and rows[0][0])


def set_admin_authed(chat_id: str, authed: bool = True) -> None:
    ch = _client()
    _ensure_all(ch)
    ch.insert(AUTH_TABLE, [[str(chat_id), int(bool(authed)), _now(), 0]],
              column_names=["chat_id", "authed", "updated_at", "deleted"])


# ── rules ──────────────────────────────────────────────────────────────────

def _row_to_rule(r) -> dict:
    try:
        params = json.loads(r[7]) if r[7] else {}
    except (ValueError, TypeError):
        params = {}
    return {
        "rule_id": r[0], "topic_id": r[1], "kind": r[2], "scope": r[3],
        "enabled": bool(r[4]), "cadence_s": int(r[5]), "cooldown_s": int(r[6]),
        "params": params, "title": r[8],
    }


_RULE_COLS = ("rule_id, topic_id, kind, scope, enabled, cadence_s, cooldown_s, params, title")


def get_rules(force: bool = False, enabled_only: bool = True) -> list[dict]:
    """All rules (parsed). Cached ~15s so the monitor hot-reloads edits."""
    now = time.monotonic()
    if not force and enabled_only:
        with _lock:
            if _rules_cache["value"] is not None and (now - _rules_cache["ts"]) < _CACHE_TTL_S:
                return _rules_cache["value"]
    try:
        ch = _client()
        _ensure_all(ch)
        where = "deleted = 0" + (" AND enabled = 1" if enabled_only else "")
        rows = ch.query(
            f"SELECT {_RULE_COLS} FROM {RULES_TABLE} FINAL WHERE {where} ORDER BY rule_id"
        ).result_rows
        value = [_row_to_rule(r) for r in rows]
        if enabled_only:
            with _lock:
                _rules_cache["value"] = value
                _rules_cache["ts"] = time.monotonic()
        return value
    except Exception as exc:  # noqa: BLE001
        with _lock:
            cached = _rules_cache["value"]
        if cached is not None and enabled_only:
            log.warning("notification rules read failed (%s); serving cache", exc)
            return cached
        log.warning("notification rules read failed (%s); no rules", exc)
        return []


def get_rule(rule_id: str) -> dict | None:
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT {_RULE_COLS} FROM {RULES_TABLE} FINAL "
        f"WHERE rule_id = {{r:String}} AND deleted = 0",
        parameters={"r": rule_id},
    ).result_rows
    return _row_to_rule(rows[0]) if rows else None


# ── manual triggers (debug "trigger now") ──────────────────────────────────

def trigger_watermark() -> datetime:
    """Newest existing trigger time — the poller starts here so pre-startup rows
    (and anything left within the 1d TTL across a restart) are ignored."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(f"SELECT max(requested_at) FROM {TRIGGERS_TABLE}").result_rows
    ts = rows[0][0] if rows else None
    # empty table → CH returns the 1970 epoch sentinel; treat as "now".
    if isinstance(ts, datetime) and ts.year >= 2000:
        return ts.replace(tzinfo=None)
    return _now()


def read_pending_triggers(since: datetime) -> list[tuple[str, datetime]]:
    """(rule_id, newest requested_at) for triggers requested AFTER `since`,
    deduped per rule (a double-click fires once)."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT rule_id, max(requested_at) FROM {TRIGGERS_TABLE} "
        f"WHERE requested_at > {{s:DateTime64(3)}} GROUP BY rule_id",
        parameters={"s": since},
    ).result_rows
    out = []
    for r in rows:
        t = r[1]
        out.append((r[0], t.replace(tzinfo=None) if isinstance(t, datetime) else _now()))
    return out


def upsert_rule(rule_id: str, *, kind: str, scope: str, enabled: bool = True,
                cadence_s: int = 300, cooldown_s: int = 0, params=None,
                title: str = "", topic_id: str = "") -> dict:
    rule_id = (rule_id or "").strip()
    if not rule_id:
        raise ValueError("rule_id required")
    if isinstance(params, str):
        try:
            params = json.loads(params) if params else {}
        except (ValueError, TypeError):
            raise ValueError("params must be valid JSON")
    params_json = json.dumps(params or {})
    ch = _client()
    _ensure_all(ch)
    ch.insert(RULES_TABLE,
              [[rule_id, topic_id, kind, scope, int(bool(enabled)),
                int(cadence_s), int(cooldown_s), params_json, title, _now(), 0]],
              column_names=["rule_id", "topic_id", "kind", "scope", "enabled",
                            "cadence_s", "cooldown_s", "params", "title",
                            "updated_at", "deleted"])
    with _lock:
        _rules_cache["ts"] = 0.0
    return get_rule(rule_id) or {"rule_id": rule_id}


def delete_rule(rule_id: str) -> dict:
    ch = _client()
    _ensure_all(ch)
    r = get_rule(rule_id)
    if not r:
        return {"rule_id": rule_id, "deleted": False}
    ch.insert(RULES_TABLE,
              [[rule_id, r["topic_id"], r["kind"], r["scope"], int(r["enabled"]),
                r["cadence_s"], r["cooldown_s"], json.dumps(r["params"]), r["title"], _now(), 1]],
              column_names=["rule_id", "topic_id", "kind", "scope", "enabled",
                            "cadence_s", "cooldown_s", "params", "title",
                            "updated_at", "deleted"])
    with _lock:
        _rules_cache["ts"] = 0.0
    return {"rule_id": rule_id, "deleted": True}


# ── state (edge + cooldown) ────────────────────────────────────────────────

def get_states(rule_id: str) -> dict[str, dict]:
    """{entity: {state: bool, last_fired_at: datetime}} for a rule."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT entity, state, last_fired_at FROM {STATE_TABLE} FINAL "
        f"WHERE rule_id = {{r:String}} AND deleted = 0",
        parameters={"r": rule_id},
    ).result_rows
    return {r[0]: {"state": bool(r[1]), "last_fired_at": r[2]} for r in rows}


def set_state(rule_id: str, entity: str, state: bool, last_fired_at: datetime) -> None:
    ch = _client()
    _ensure_all(ch)
    ch.insert(STATE_TABLE,
              [[rule_id, entity, int(bool(state)), last_fired_at, _now(), 0]],
              column_names=["rule_id", "entity", "state", "last_fired_at",
                            "updated_at", "deleted"])


# ── last-fired (per topic) ─────────────────────────────────────────────────

def record_fired(topic_id: str, message: str, sent_count: int = 0) -> None:
    """Record that `topic_id` just fired. Logged regardless of subscriber count,
    so a widget/admin can see the last trigger time even with no subscribers.
    RMT(fired_at) keeps only the newest row per topic."""
    ch = _client()
    _ensure_all(ch)
    ch.insert(LAST_FIRED_TABLE,
              [[topic_id, (message or "")[:2000], int(sent_count), _now()]],
              column_names=["topic_id", "message", "sent_count", "fired_at"])


def get_last_fired() -> dict[str, dict]:
    """{topic_id: {message, sent_count, fired_at}} — latest fire per topic."""
    ch = _client()
    _ensure_all(ch)
    rows = ch.query(
        f"SELECT topic_id, message, sent_count, fired_at FROM {LAST_FIRED_TABLE} FINAL"
    ).result_rows
    return {r[0]: {"message": r[1], "sent_count": int(r[2]), "fired_at": r[3]} for r in rows}
