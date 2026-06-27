"""Wallet pins + groups, persisted in ClickHouse (replaces the old localStorage
store). A "pin" is a (wallet, group) membership; groups carry a name + colour.
Everything is scoped by `user_id`.

There is no auth yet, so all rows use a single constant user id (`CONST_USER_ID`).
When real users land, swap that for the authenticated id — the schema is already
user-scoped, so no migration is needed.

Both tables are ReplacingMergeTree(updated_at) with a soft-delete flag; reads use
FINAL + `deleted = 0`. Saving is a per-user snapshot replace: tombstone the
user's current rows, then insert the posted set (the insert carries a strictly
later updated_at so it wins the merge for surviving keys).
"""
from datetime import datetime, timedelta, timezone

from sanic import Blueprint, response

from clickhouse import client

bp = Blueprint("wallet_pins")

# Single-user placeholder until auth/users exist. Swap for the authed user id.
CONST_USER_ID = "local"

GROUPS_TABLE = "tradernick.wallet_groups"
PINS_TABLE = "tradernick.wallet_pins"


async def ensure_tables(ch) -> None:
    """Create the wallet groups + pins tables if absent. Safe on every startup."""
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {GROUPS_TABLE} (\n"
        "    user_id    String,\n"
        "    group_id   String,\n"
        "    name       String,\n"
        "    color      String,\n"           # '' = neutral / null
        "    sort       UInt32,\n"           # preserve display order
        "    updated_at DateTime64(3),\n"
        "    deleted    UInt8\n"
        ") ENGINE = ReplacingMergeTree(updated_at)\n"
        "ORDER BY (user_id, group_id)"
    )
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {PINS_TABLE} (\n"
        "    user_id    String,\n"
        "    address    String,\n"
        "    group_id   String,\n"
        "    added_at   DateTime64(3),\n"
        "    updated_at DateTime64(3),\n"
        "    deleted    UInt8\n"
        ") ENGINE = ReplacingMergeTree(updated_at)\n"
        "ORDER BY (user_id, address, group_id)"
    )


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_ms(dt) -> int:
    """Naive-UTC datetime → epoch milliseconds (matches the frontend's Date.now)."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _from_ms(ms, fallback) -> datetime:
    if not isinstance(ms, (int, float)):
        return fallback
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


@bp.get("/wallet_pins")
async def get_wallet_pins(_request):
    """Load the user's groups + pins in the frontend store's shape."""
    ch = await client()
    uid = CONST_USER_ID
    g_rows = await ch.query(
        f"SELECT group_id, name, color FROM {GROUPS_TABLE} FINAL "
        "WHERE user_id = {u:String} AND deleted = 0 ORDER BY sort, group_id",
        parameters={"u": uid},
    )
    groups = [{"id": r[0], "name": r[1], "color": (r[2] or None)} for r in g_rows.result_rows]

    p_rows = await ch.query(
        f"SELECT address, group_id, added_at FROM {PINS_TABLE} FINAL "
        "WHERE user_id = {u:String} AND deleted = 0",
        parameters={"u": uid},
    )
    by_addr: dict = {}
    for addr, gid, added in p_rows.result_rows:
        by_addr.setdefault(addr, []).append({"groupId": gid, "addedAt": _to_ms(added)})
    pins = [{"address": a, "groups": gs} for a, gs in by_addr.items()]
    return response.json({"groups": groups, "pins": pins})


@bp.post("/wallet_pins")
async def save_wallet_pins(request):
    """Snapshot-replace the user's groups + pins with the posted set."""
    body = request.json or {}
    groups = body.get("groups") or []
    pins = body.get("pins") or []
    ch = await client()
    uid = CONST_USER_ID
    t_tomb = _utcnow()
    t_ins = t_tomb + timedelta(milliseconds=1)  # strictly later → wins the merge

    # --- groups ---
    existing_g = await ch.query(
        f"SELECT group_id FROM {GROUPS_TABLE} FINAL WHERE user_id = {{u:String}} AND deleted = 0",
        parameters={"u": uid},
    )
    rows_g = [[uid, r[0], "", "", 0, t_tomb, 1] for r in existing_g.result_rows]
    for i, g in enumerate(groups):
        gid = str(g.get("id") or "")
        if not gid:
            continue
        rows_g.append([uid, gid, str(g.get("name") or ""), (g.get("color") or ""), i, t_ins, 0])
    if rows_g:
        await ch.insert(
            GROUPS_TABLE, rows_g,
            column_names=["user_id", "group_id", "name", "color", "sort", "updated_at", "deleted"],
        )

    # --- pins ---
    existing_p = await ch.query(
        f"SELECT address, group_id FROM {PINS_TABLE} FINAL WHERE user_id = {{u:String}} AND deleted = 0",
        parameters={"u": uid},
    )
    rows_p = [[uid, r[0], r[1], t_tomb, t_tomb, 1] for r in existing_p.result_rows]
    for p in pins:
        addr = str(p.get("address") or "").strip().lower()
        if not addr:
            continue
        for m in (p.get("groups") or []):
            gid = str(m.get("groupId") or "")
            if not gid:
                continue
            rows_p.append([uid, addr, gid, _from_ms(m.get("addedAt"), t_ins), t_ins, 0])
    if rows_p:
        await ch.insert(
            PINS_TABLE, rows_p,
            column_names=["user_id", "address", "group_id", "added_at", "updated_at", "deleted"],
        )

    return response.json({"ok": True})


# ── Granular writes ───────────────────────────────────────────────────────
# One row per operation (RMT versions by updated_at). Unlike the snapshot save,
# these NEVER touch rows they don't name, so a stale/partial in-memory state can
# never drop another wallet's pin.
_PIN_COLS = ["user_id", "address", "group_id", "added_at", "updated_at", "deleted"]
_GROUP_COLS = ["user_id", "group_id", "name", "color", "sort", "updated_at", "deleted"]


@bp.post("/wallet_pins/pin")
async def pin_one(request):
    """Add one (wallet, group) membership."""
    b = request.json or {}
    addr = str(b.get("address") or "").strip().lower()
    gid = str(b.get("groupId") or b.get("group_id") or "")
    if not addr or not gid:
        return response.json({"error": "missing address/groupId"}, status=400)
    ch = await client()
    now = _utcnow()
    await ch.insert(PINS_TABLE, [[CONST_USER_ID, addr, gid, _from_ms(b.get("addedAt"), now), now, 0]],
                    column_names=_PIN_COLS)
    return response.json({"ok": True})


@bp.post("/wallet_pins/unpin")
async def unpin_one(request):
    """Remove one (wallet, group) membership (tombstone)."""
    b = request.json or {}
    addr = str(b.get("address") or "").strip().lower()
    gid = str(b.get("groupId") or b.get("group_id") or "")
    if not addr or not gid:
        return response.json({"error": "missing address/groupId"}, status=400)
    ch = await client()
    now = _utcnow()
    await ch.insert(PINS_TABLE, [[CONST_USER_ID, addr, gid, now, now, 1]], column_names=_PIN_COLS)
    return response.json({"ok": True})


@bp.post("/wallet_pins/group")
async def upsert_group(request):
    """Create or update one group (name / color / sort)."""
    b = request.json or {}
    gid = str(b.get("id") or b.get("group_id") or "")
    if not gid:
        return response.json({"error": "missing id"}, status=400)
    ch = await client()
    now = _utcnow()
    sort = int(b.get("sort") or 0)
    await ch.insert(GROUPS_TABLE, [[CONST_USER_ID, gid, str(b.get("name") or ""),
                                    (b.get("color") or ""), sort, now, 0]],
                    column_names=_GROUP_COLS)
    return response.json({"ok": True})


@bp.post("/wallet_pins/group_delete")
async def delete_group(request):
    """Delete a group + tombstone every membership in it."""
    b = request.json or {}
    gid = str(b.get("id") or b.get("group_id") or "")
    if not gid:
        return response.json({"error": "missing id"}, status=400)
    ch = await client()
    now = _utcnow()
    await ch.insert(GROUPS_TABLE, [[CONST_USER_ID, gid, "", "", 0, now, 1]], column_names=_GROUP_COLS)
    rows = await ch.query(
        f"SELECT address FROM {PINS_TABLE} FINAL "
        "WHERE user_id = {u:String} AND group_id = {g:String} AND deleted = 0",
        parameters={"u": CONST_USER_ID, "g": gid},
    )
    if rows.result_rows:
        await ch.insert(
            PINS_TABLE, [[CONST_USER_ID, r[0], gid, now, now, 1] for r in rows.result_rows],
            column_names=_PIN_COLS)
    return response.json({"ok": True})
