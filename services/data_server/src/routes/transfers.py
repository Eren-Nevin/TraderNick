import asyncio
import json
import re
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILTER_KEYS = (
    "sender_in", "sender_ex",
    "receiver_in", "receiver_ex",
    "involving_in", "involving_ex",
)
_MAX_EXTRAS = 3

bp = Blueprint("transfers")

# Distinct (kind, chain, token) tuples take ~2-5s to compute over the full
# transfers table once it has 100M+ rows. The list changes only when admin
# reconfigures ingestion, so cache aggressively with a TTL.
_STREAMS_CACHE: dict = {"at": 0.0, "value": None}
_STREAMS_TTL_SECONDS = 60.0
_streams_lock = asyncio.Lock()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


async def _fetch_streams() -> list[dict]:
    ch = await client()
    rows = await ch.query(
        """
        SELECT DISTINCT chain, token, kind
        FROM tradernick.transfers
        ORDER BY chain, token
        """
    )
    return [{"chain": r[0], "token": r[1], "kind": r[2]} for r in rows.result_rows]


@bp.get("/transfers/streams")
async def streams(_request):
    now = time.monotonic()
    if _STREAMS_CACHE["value"] is not None and now - _STREAMS_CACHE["at"] < _STREAMS_TTL_SECONDS:
        return response.json({"streams": _STREAMS_CACHE["value"]})
    async with _streams_lock:
        # double-check after acquiring lock — concurrent waiters share the refresh
        now = time.monotonic()
        if _STREAMS_CACHE["value"] is None or now - _STREAMS_CACHE["at"] >= _STREAMS_TTL_SECONDS:
            _STREAMS_CACHE["value"] = await _fetch_streams()
            _STREAMS_CACHE["at"] = now
    return response.json({"streams": _STREAMS_CACHE["value"]})


# EVM chains where addresses are case-insensitive — we lower(sender/receiver)
# before looking up the wallet_labels dictionary. Other chains (BTC, TRON) preserve case.
_EVM_CHAINS = ("ETH", "ARB", "POLYGON", "BASE", "BSC", "OP", "AVAX")


def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [v.strip() for v in s.split(",") if v.strip()]


def _build_extra_sumif_clauses(chain: str, extras: list[dict]) -> tuple[list[str], dict]:
    """For each extra spec ({id, filters}), build a `sumIf(amount, <cond>) AS extra_amount_<id>`
    clause and the matching parameters. All clauses go into a single CH SELECT so the table
    is scanned once and every aggregation falls out of the same group-by.
    """
    chain_evm = chain.upper() in _EVM_CHAINS
    addr_expr = "lower({col})" if chain_evm else "{col}"

    def dg(col: str) -> str:
        return (
            "dictGet('tradernick.wallet_labels', 'categories', "
            + addr_expr.format(col=col) + ")"
        )

    clauses: list[str] = []
    params: dict = {}
    # IMPORTANT: we use a positional SQL alias (extra_pos_<i>) — NOT the client
    # id — because client ids (e.g. crypto.randomUUID()) contain dashes which
    # CH parses as subtraction inside an identifier. The handler reads the
    # column by position when building the response so the client id is only
    # ever used as a JSON dict key, not as a SQL identifier.
    for i, spec in enumerate(extras):
        cf = spec.get("filters") or {}
        preds: list[str] = []
        for side in ("sender", "receiver", "involving"):
            for action in ("in", "ex"):
                key = f"{side}_{action}"
                cats = cf.get(key)
                if not cats or not isinstance(cats, list):
                    continue
                cats = [str(c) for c in cats if str(c).strip()]
                if not cats:
                    continue
                pname = f"e_pos_{i}_{side}_{action}"
                params[pname] = cats
                pphold = f"{{{pname}:Array(String)}}"
                if side == "involving":
                    match = f"(hasAny({dg('sender')}, {pphold}) OR hasAny({dg('receiver')}, {pphold}))"
                else:
                    match = f"hasAny({dg(side)}, {pphold})"
                preds.append(match if action == "in" else f"NOT {match}")
        cond = " AND ".join(preds) if preds else "1"
        clauses.append(f"sumIf(amount, {cond}) AS extra_pos_{i}")
    return clauses, params


def _build_wallet_predicate(chain: str, side: str, action: str, cats: list[str]) -> tuple[str, str] | None:
    """Construct a SQL predicate fragment + a unique parameter name for one filter.

    side   = 'sender' | 'receiver' | 'involving'
    action = 'in' | 'ex'

    Returns (sql_fragment, param_name) or None if cats is empty.
    """
    if not cats:
        return None
    addr_expr = (
        "lower({col})" if chain.upper() in _EVM_CHAINS else "{col}"
    )
    param = f"{side}_{action}_cats"
    if side == "involving":
        sender_expr = (
            f"hasAny(dictGet('tradernick.wallet_labels', 'categories', "
            f"{addr_expr.format(col='sender')}), {{{param}:Array(String)}})"
        )
        receiver_expr = (
            f"hasAny(dictGet('tradernick.wallet_labels', 'categories', "
            f"{addr_expr.format(col='receiver')}), {{{param}:Array(String)}})"
        )
        match_expr = f"({sender_expr} OR {receiver_expr})"
    else:
        col = side  # 'sender' or 'receiver'
        match_expr = (
            f"hasAny(dictGet('tradernick.wallet_labels', 'categories', "
            f"{addr_expr.format(col=col)}), {{{param}:Array(String)}})"
        )
    if action == "in":
        return match_expr, param
    # action == 'ex' — must NOT overlap with the supplied categories
    return f"NOT {match_expr}", param


@bp.get("/transfers/aggregate")
async def aggregate(request):
    chain = request.args.get("chain")
    kind = request.args.get("kind")
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    if not chain or not token or not kind:
        return response.json({"error": "missing chain/kind/token"}, status=400)
    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    # ---- Legacy single-filter params (kept so direct curl calls still work) ----
    filter_specs = [
        ("sender",    "in", _parse_csv(request.args.get("sender_in"))),
        ("sender",    "ex", _parse_csv(request.args.get("sender_ex"))),
        ("receiver",  "in", _parse_csv(request.args.get("receiver_in"))),
        ("receiver",  "ex", _parse_csv(request.args.get("receiver_ex"))),
        ("involving", "in", _parse_csv(request.args.get("involving_in"))),
        ("involving", "ex", _parse_csv(request.args.get("involving_ex"))),
    ]
    where_clauses: list[str] = []
    legacy_params: dict = {}
    for side, action, cats in filter_specs:
        pred = _build_wallet_predicate(chain, side, action, cats)
        if pred is None:
            continue
        sql_frag, param_name = pred
        where_clauses.append("AND " + sql_frag)
        legacy_params[param_name] = cats
    where_extra_sql = "\n          ".join(where_clauses)

    # ---- New extras param: list of {id, filters} → one sumIf per extra ----
    extras_raw = request.args.get("extras")
    extras: list[dict] = []
    if extras_raw:
        try:
            parsed = json.loads(extras_raw)
        except Exception:
            return response.json({"error": "extras: invalid JSON"}, status=400)
        if not isinstance(parsed, list):
            return response.json({"error": "extras: expected JSON array"}, status=400)
        for spec in parsed:
            if not isinstance(spec, dict):
                continue
            eid = spec.get("id")
            if not isinstance(eid, str) or not _SAFE_ID.fullmatch(eid):
                return response.json({"error": f"extras: invalid id {eid!r}"}, status=400)
            filters = spec.get("filters") or {}
            if not isinstance(filters, dict):
                continue
            # whitelist filter keys
            cleaned_filters = {k: filters[k] for k in _FILTER_KEYS if k in filters}
            extras.append({"id": eid, "filters": cleaned_filters})
            if len(extras) >= _MAX_EXTRAS:
                break

    extra_clauses, extra_params = _build_extra_sumif_clauses(chain, extras)
    extra_select_sql = (
        ",\n            " + ",\n            ".join(extra_clauses)
        if extra_clauses
        else ""
    )

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum(amount)             AS sum_amount,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                 AS count{extra_select_sql}
        FROM tradernick.transfers
        WHERE chain = {{chain:String}}
          AND kind  = {{kind:String}}
          AND token = {{token:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
          {where_extra_sql}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(
        sql,
        parameters={
            "seconds": seconds,
            "chain": chain,
            "kind": kind,
            "token": token,
            "since": since_dt,
            "until": until_dt,
            "limit": limit,
            **legacy_params,
            **extra_params,
        },
    )

    n_static = 4  # bucket, sum_amount, sum_value_usd, count
    series = []
    for r in rows.result_rows:
        row = {
            "time": int(r[0]),
            "sum_amount": float(r[1]),
            "sum_value_usd": float(r[2]),
            "count": int(r[3]),
        }
        for i, spec in enumerate(extras):
            v = r[n_static + i]
            row[f"extra_amount_{spec['id']}"] = float(v) if v is not None else 0.0
        series.append(row)

    return response.json({
        "chain": chain,
        "kind": kind,
        "token": token,
        "interval": interval,
        "extras": [e["id"] for e in extras],
        "series": series,
    })


# --- /transfers/categories -----------------------------------------------------
_CATS_CACHE: dict = {"at": 0.0, "value": None}
_CATS_TTL_SECONDS = 300.0
_cats_lock = asyncio.Lock()


async def _fetch_categories() -> list[dict]:
    ch = await client()
    rows = await ch.query(
        """
        SELECT arrayJoin(categories) AS c, count() AS n
        FROM tradernick.wallets FINAL
        GROUP BY c
        ORDER BY n DESC
        LIMIT 200
        """
    )
    return [{"name": r[0], "count": int(r[1])} for r in rows.result_rows]


@bp.get("/transfers/categories")
async def categories(_request):
    now = time.monotonic()
    if _CATS_CACHE["value"] is not None and now - _CATS_CACHE["at"] < _CATS_TTL_SECONDS:
        return response.json({"categories": _CATS_CACHE["value"]})
    async with _cats_lock:
        now = time.monotonic()
        if _CATS_CACHE["value"] is None or now - _CATS_CACHE["at"] >= _CATS_TTL_SECONDS:
            _CATS_CACHE["value"] = await _fetch_categories()
            _CATS_CACHE["at"] = now
    return response.json({"categories": _CATS_CACHE["value"]})
