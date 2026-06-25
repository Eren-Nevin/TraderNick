"""Runtime-managed token batches, backed by ClickHouse.

Batches used to be env-only: `config.INGEST_TOKEN_BATCHES` was parsed from
`INGEST_TOKENS` / `INGEST_TOKENS_BATCH_N` / `INGEST_NAMED_BATCHES` at import,
and the flat union `config.INGEST_TOKENS` fed the live streams. That meant
every batch change needed a process restart.

This module moves the source of truth to `tradernick.ingestion_token_batches`
so the admin panel can add / edit / remove batches with no restart. Every
ingestion process reads through `get_batches()` / `get_ingest_tokens()` with a
short in-process TTL cache; the live groups re-read each poll cycle, so an
admin edit propagates within ~`_CACHE_TTL_S` + one cycle. Writes
(`upsert_batch` / `delete_batch`) bust the local cache immediately.

Seeding: the first read of an EMPTY table copies `config.INGEST_TOKEN_BATCHES`
(the env batches) into the store. After that the store is authoritative and
the env vars only matter for a fresh deployment.

Fallback: if ClickHouse is briefly unreachable we serve the last good cache,
or — if we never managed a read — the env batches. Live ingestion therefore
never loses its roster because of a transient CH blip.

Uses a SYNCHRONOUS clickhouse_connect client so `get_ingest_tokens()` is a
drop-in for the old `list(config.INGEST_TOKENS)` at every (async) call site.
Reads are cached, tiny, and infrequent, so the brief blocking call is fine.
"""
import logging
import threading
import time

import clickhouse_connect

import config

log = logging.getLogger(__name__)

TABLE = "tradernick.ingestion_token_batches"
OVERRIDES_TABLE = "tradernick.ingestion_token_overrides"
_CACHE_TTL_S = 30.0

_lock = threading.Lock()
_client_obj = None
_table_ready = False
_overrides_table_ready = False
_seed_checked = False
# batches: list[tuple[str, list[str]]] | None ; ts: monotonic seconds
_cache = {"batches": None, "ts": 0.0}
# overrides: (deprecated: set, renamed: dict) | None ; ts: monotonic seconds
_ovr_cache = {"value": None, "ts": 0.0}


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


def _ensure_table(ch):
    """Create the store if it's missing. The schema also lives in
    clickhouse/init/01_schema.sql, but init only runs on a fresh CH volume —
    this covers an already-running cluster on first deploy."""
    global _table_ready
    if _table_ready:
        return
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            name        String,
            tokens      String,
            position    UInt32,
            deleted     UInt8          DEFAULT 0,
            updated_at  DateTime64(3)  DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY name
        """
    )
    _table_ready = True


def _parse_csv(raw: str) -> list[str]:
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _env_batches() -> list[tuple[str, list[str]]]:
    """The env-seeded batches (Batch 1/2 + INGEST_NAMED_BATCHES). Fallback +
    seed source, in the same shape as get_batches()."""
    return [(name, list(toks)) for name, toks in config.INGEST_TOKEN_BATCHES]


def _seed_if_empty(ch):
    global _seed_checked
    if _seed_checked:
        return
    rows = ch.query(f"SELECT count() FROM {TABLE}").result_rows
    if rows and rows[0][0] > 0:
        _seed_checked = True
        return
    seed = _env_batches()
    if seed:
        data = [[name, ",".join(toks), pos, 0]
                for pos, (name, toks) in enumerate(seed)]
        ch.insert(TABLE, data, column_names=["name", "tokens", "position", "deleted"])
        log.info("token_batches: seeded %d batches from env", len(data))
    _seed_checked = True


def _read_store() -> list[tuple[str, list[str]]]:
    ch = _client()
    _ensure_table(ch)
    _seed_if_empty(ch)
    rows = ch.query(
        f"SELECT name, tokens, position FROM {TABLE} FINAL "
        f"WHERE deleted = 0 ORDER BY position, name"
    ).result_rows
    return [(r[0], _parse_csv(r[1])) for r in rows]


def get_batches(force: bool = False) -> list[tuple[str, list[str]]]:
    """Ordered list of (name, [tokens]). Cached ~30s; on CH error serves the
    last good cache, else the env batches."""
    now = time.monotonic()
    if not force:
        with _lock:
            if _cache["batches"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
                return _cache["batches"]
    try:
        batches = _read_store()
        with _lock:
            _cache["batches"] = batches
            _cache["ts"] = time.monotonic()
        return batches
    except Exception as exc:  # noqa: BLE001
        with _lock:
            cached = _cache["batches"]
        if cached is not None:
            log.warning("token_batches read failed (%s); serving cached batches", exc)
            return cached
        log.warning("token_batches read failed (%s); falling back to env batches", exc)
        return _env_batches()


def get_ingest_tokens() -> list[str]:
    """Flat, de-duplicated union across all batches (first occurrence wins,
    order preserved). Drop-in replacement for the old config.INGEST_TOKENS."""
    seen: set[str] = set()
    out: list[str] = []
    for _name, toks in get_batches():
        for t in toks:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _bust_cache():
    with _lock:
        _cache["ts"] = 0.0


def upsert_batch(name: str, tokens, position=None) -> dict:
    """Create or replace a batch (keyed by name). `tokens` may be a CSV string
    or a list. Appends after the current max position when position is None."""
    name = (name or "").strip()
    if not name:
        raise ValueError("batch name required")
    if isinstance(tokens, str):
        tokens = _parse_csv(tokens)
    tokens = [str(t).strip() for t in (tokens or []) if str(t).strip()]
    ch = _client()
    _ensure_table(ch)
    if position is None:
        rows = ch.query(
            f"SELECT max(position) FROM {TABLE} FINAL WHERE deleted = 0"
        ).result_rows
        cur_max = rows[0][0] if rows and rows[0][0] is not None else None
        position = int(cur_max) + 1 if cur_max is not None else 0
    ch.insert(
        TABLE,
        [[name, ",".join(tokens), int(position), 0]],
        column_names=["name", "tokens", "position", "deleted"],
    )
    _bust_cache()
    return {"name": name, "tokens": tokens, "count": len(tokens), "position": int(position)}


def delete_batch(name: str) -> dict:
    """Soft-delete a batch: re-insert it with deleted=1 (RMT keeps the latest
    row per name by updated_at, so the FINAL read drops it)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("batch name required")
    ch = _client()
    _ensure_table(ch)
    rows = ch.query(
        f"SELECT tokens, position FROM {TABLE} FINAL WHERE name = {{n:String}}",
        parameters={"n": name},
    ).result_rows
    if not rows:
        return {"name": name, "deleted": False, "reason": "not found"}
    tokens, position = rows[0][0], rows[0][1]
    ch.insert(
        TABLE,
        [[name, tokens, int(position), 1]],
        column_names=["name", "tokens", "position", "deleted"],
    )
    _bust_cache()
    return {"name": name, "deleted": True}


# ── Token overrides (deprecated / renamed) ─────────────────────────────────
# Adjust the batch union differently for live vs backfill (all exchanges):
#   deprecated → dropped from the LIVE roster, kept for backfill.
#   renamed    → live swaps old→new; backfill keeps BOTH old and new.
# Stored in OVERRIDES_TABLE, admin-managed, same cache/fallback model as batches.

def _ensure_overrides_table(ch):
    global _overrides_table_ready
    if _overrides_table_ready:
        return
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {OVERRIDES_TABLE} (
            kind        String,
            token       String,
            new_token   String         DEFAULT '',
            deleted     UInt8          DEFAULT 0,
            updated_at  DateTime64(3)  DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (kind, token)
        """
    )
    _overrides_table_ready = True


def _read_overrides() -> tuple[set, dict]:
    ch = _client()
    _ensure_overrides_table(ch)
    rows = ch.query(
        f"SELECT kind, token, new_token FROM {OVERRIDES_TABLE} FINAL "
        f"WHERE deleted = 0"
    ).result_rows
    deprecated: set[str] = set()
    renamed: dict[str, str] = {}
    for kind, token, new_token in rows:
        token = str(token).strip()
        if not token:
            continue
        if kind == "deprecated":
            deprecated.add(token)
        elif kind == "renamed" and str(new_token).strip():
            renamed[token] = str(new_token).strip()
    return deprecated, renamed


def get_overrides(force: bool = False) -> tuple[set, dict]:
    """(deprecated: set[str], renamed: dict[old→new]). Cached ~30s; on CH error
    serves the last good cache, else empty (no overrides) so jobs keep running."""
    now = time.monotonic()
    if not force:
        with _lock:
            if _ovr_cache["value"] is not None and (now - _ovr_cache["ts"]) < _CACHE_TTL_S:
                return _ovr_cache["value"]
    try:
        value = _read_overrides()
        with _lock:
            _ovr_cache["value"] = value
            _ovr_cache["ts"] = time.monotonic()
        return value
    except Exception as exc:  # noqa: BLE001
        with _lock:
            cached = _ovr_cache["value"]
        if cached is not None:
            log.warning("token_overrides read failed (%s); serving cached overrides", exc)
            return cached
        log.warning("token_overrides read failed (%s); proceeding with no overrides", exc)
        return set(), {}


def get_live_tokens() -> list[str]:
    """Roster for LIVE jobs: the batch union with deprecated tokens dropped and
    renamed tokens swapped old→new (de-duped, order preserved)."""
    deprecated, renamed = get_overrides()
    seen: set[str] = set()
    out: list[str] = []
    for t in get_ingest_tokens():
        if t in deprecated:
            continue
        t2 = renamed.get(t, t)
        if t2 in deprecated or t2 in seen:
            continue
        seen.add(t2)
        out.append(t2)
    return out


def expand_backfill_tokens(tokens) -> list[str]:
    """Roster for BACKFILL jobs: the given tokens PLUS the renamed-new name for
    any old token present (de-duped). Deprecated are NOT removed — their history
    is still fetchable."""
    _deprecated, renamed = get_overrides()
    out: list[str] = []
    seen: set[str] = set()
    for t in (tokens or []):
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    for t in list(out):
        new = renamed.get(t)
        if new and new not in seen:
            seen.add(new)
            out.append(new)
    return out


def get_backfill_tokens() -> list[str]:
    """Full backfill roster = the batch union expanded with renamed-new names."""
    return expand_backfill_tokens(get_ingest_tokens())


def _bust_overrides_cache():
    with _lock:
        _ovr_cache["ts"] = 0.0


def upsert_override(kind: str, token: str, new_token: str = "") -> dict:
    """Create/replace an override. kind ∈ {'deprecated','renamed'}; 'renamed'
    requires a non-empty new_token."""
    kind = (kind or "").strip().lower()
    token = (token or "").strip()
    new_token = (new_token or "").strip()
    if kind not in ("deprecated", "renamed"):
        raise ValueError("kind must be 'deprecated' or 'renamed'")
    if not token:
        raise ValueError("token required")
    if kind == "renamed" and not new_token:
        raise ValueError("renamed requires new_token")
    if kind == "deprecated":
        new_token = ""
    ch = _client()
    _ensure_overrides_table(ch)
    ch.insert(
        OVERRIDES_TABLE,
        [[kind, token, new_token, 0]],
        column_names=["kind", "token", "new_token", "deleted"],
    )
    _bust_overrides_cache()
    return {"kind": kind, "token": token, "new_token": new_token}


def delete_override(kind: str, token: str) -> dict:
    """Soft-delete an override (re-insert with deleted=1)."""
    kind = (kind or "").strip().lower()
    token = (token or "").strip()
    if not kind or not token:
        raise ValueError("kind and token required")
    ch = _client()
    _ensure_overrides_table(ch)
    rows = ch.query(
        f"SELECT new_token FROM {OVERRIDES_TABLE} FINAL "
        f"WHERE kind = {{k:String}} AND token = {{t:String}}",
        parameters={"k": kind, "t": token},
    ).result_rows
    if not rows:
        return {"kind": kind, "token": token, "deleted": False, "reason": "not found"}
    ch.insert(
        OVERRIDES_TABLE,
        [[kind, token, rows[0][0], 1]],
        column_names=["kind", "token", "new_token", "deleted"],
    )
    _bust_overrides_cache()
    return {"kind": kind, "token": token, "deleted": True}
