import asyncio
import json
import re
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.compounds import compound_pairs, get_compound
from routes.ohlcv import INTERVAL_SECONDS

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILTER_KEYS = (
    "sender_in", "sender_ex",
    "receiver_in", "receiver_ex",
    "involving_in", "involving_ex",
    "sender_entity_in", "sender_entity_ex",
    "receiver_entity_in", "receiver_entity_ex",
    "involving_entity_in", "involving_entity_ex",
)
_ENTITY_KEYS = (
    "sender_entity_in", "sender_entity_ex",
    "receiver_entity_in", "receiver_entity_ex",
    "involving_entity_in", "involving_entity_ex",
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
_EVM_CHAINS_SQL = "(" + ", ".join(f"'{c}'" for c in _EVM_CHAINS) + ")"


def _addr_expr(chain: str | None, col: str) -> str:
    """Return the SQL expression for a normalised address.

    - chain="ETH" / "ARB" / "BASE" / "BSC" / "POLYGON" → `lower(col)` (EVM)
    - chain="BTC" / "TRON" / ...                         → `col` (case-sensitive)
    - chain=None (compound / multi-chain query)         → per-row conditional:
          `if(chain IN (...EVM...), lower(col), col)`
      so EVM rows still hit the dict's lowercase keys and BTC/TRON rows pass
      through unchanged.
    """
    if chain is None:
        return f"if(chain IN {_EVM_CHAINS_SQL}, lower({col}), {col})"
    if chain.upper() in _EVM_CHAINS:
        return f"lower({col})"
    return col


def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [v.strip() for v in s.split(",") if v.strip()]


def _build_extra_sumif_clauses(chain: str | None, extras: list[dict]) -> tuple[list[str], dict]:
    """For each extra spec ({id, filters}), build a `sumIf(amount, <cond>) AS extra_amount_<id>`
    clause and the matching parameters. All clauses go into a single CH SELECT so the table
    is scanned once and every aggregation falls out of the same group-by.

    `chain=None` means the query spans multiple chains (compound mode) and the
    dict lookup must normalise the address per-row.
    """
    def dg(col: str) -> str:
        return (
            "dictGet('tradernick.wallet_labels', 'categories', "
            + _addr_expr(chain, col) + ")"
        )

    clauses: list[str] = []
    params: dict = {}
    # IMPORTANT: we use a positional SQL alias (extra_pos_<i>) — NOT the client
    # id — because client ids (e.g. crypto.randomUUID()) contain dashes which
    # CH parses as subtraction inside an identifier. The handler reads the
    # column by position when building the response so the client id is only
    # ever used as a JSON dict key, not as a SQL identifier.
    def dg_entity(col: str) -> str:
        return (
            "dictGet('tradernick.wallet_labels', 'entity', "
            + _addr_expr(chain, col) + ")"
        )

    for i, spec in enumerate(extras):
        cf = spec.get("filters") or {}
        preds: list[str] = []
        # Categories — list-of-string per wallet; case-insensitive hasAny.
        for side in ("sender", "receiver", "involving"):
            for action in ("in", "ex"):
                key = f"{side}_{action}"
                cats = cf.get(key)
                if not cats or not isinstance(cats, list):
                    continue
                cats = [str(c).lower() for c in cats if str(c).strip()]
                if not cats:
                    continue
                pname = f"e_pos_{i}_{side}_{action}"
                params[pname] = cats
                pphold = f"{{{pname}:Array(String)}}"

                def cat_match(col: str) -> str:
                    return f"hasAny(arrayMap(c -> lower(c), {dg(col)}), {pphold})"

                if side == "involving":
                    match = f"({cat_match('sender')} OR {cat_match('receiver')})"
                else:
                    match = cat_match(side)
                preds.append(match if action == "in" else f"NOT {match}")
        # Entities — single nullable string per wallet; case-insensitive IN.
        for side in ("sender", "receiver", "involving"):
            for action in ("in", "ex"):
                key = f"{side}_entity_{action}"
                vals = cf.get(key)
                if not vals or not isinstance(vals, list):
                    continue
                vals = [str(v).lower() for v in vals if str(v).strip()]
                if not vals:
                    continue
                pname = f"e_pos_{i}_{side}_ent_{action}"
                params[pname] = vals
                pphold = f"{{{pname}:Array(String)}}"

                def ent_match(col: str) -> str:
                    return f"lower(coalesce({dg_entity(col)}, '')) IN {pphold}"

                if side == "involving":
                    match = f"({ent_match('sender')} OR {ent_match('receiver')})"
                else:
                    match = ent_match(side)
                preds.append(match if action == "in" else f"NOT {match}")
        cond = " AND ".join(preds) if preds else "1"
        clauses.append(f"sumIf(amount, {cond}) AS extra_pos_{i}")
    return clauses, params


def _build_entity_predicate(
    chain: str | None, side: str, action: str, vals: list[str]
) -> tuple[str, str] | None:
    """Predicate over the dictionary's `entity` (Nullable String) attribute.

    Matching is case-insensitive: both the dictionary's entity and the user-
    supplied values are lower()'d before comparison. NULL / unknown entities
    coalesce to '' so the IN check stays well-defined.

    `chain=None` switches to per-row address normalisation for compound /
    multi-chain queries.
    """
    if not vals:
        return None
    param = f"{side}_{action}_ent"
    pphold = f"{{{param}:Array(String)}}"

    def match(col: str) -> str:
        return (
            f"lower(coalesce(dictGet('tradernick.wallet_labels', 'entity', "
            f"{_addr_expr(chain, col)}), '')) IN {pphold}"
        )

    if side == "involving":
        expr = f"({match('sender')} OR {match('receiver')})"
    else:
        expr = match(side)
    if action == "in":
        return expr, param
    return f"NOT {expr}", param


def _build_wallet_predicate(
    chain: str | None, side: str, action: str, cats: list[str]
) -> tuple[str, str] | None:
    """Construct a SQL predicate fragment + a unique parameter name for one filter.

    side   = 'sender' | 'receiver' | 'involving'
    action = 'in' | 'ex'
    chain  = a known chain string OR None for compound / multi-chain queries.

    Returns (sql_fragment, param_name) or None if cats is empty.
    """
    if not cats:
        return None
    param = f"{side}_{action}_cats"
    pphold = f"{{{param}:Array(String)}}"

    def match(col: str) -> str:
        # Case-insensitive: lower both the dictionary's category strings and the
        # caller's supplied values (lowered in Python before being passed in).
        return (
            f"hasAny(arrayMap(c -> lower(c), "
            f"dictGet('tradernick.wallet_labels', 'categories', "
            f"{_addr_expr(chain, col)})), {pphold})"
        )

    if side == "involving":
        match_expr = f"({match('sender')} OR {match('receiver')})"
    else:
        match_expr = match(side)
    if action == "in":
        return match_expr, param
    # action == 'ex' — must NOT overlap with the supplied categories
    return f"NOT {match_expr}", param


@bp.get("/transfers/aggregate")
async def aggregate(request):
    compound_name = request.args.get("compound")
    chain = request.args.get("chain")
    kind = request.args.get("kind")
    token = request.args.get("token")
    interval = request.args.get("interval", "1h")
    since = request.args.get("since")
    until = request.args.get("until")
    limit = int(request.args.get("limit", "10000"))

    # `compound=<name>` swaps the single (chain, kind, token) WHERE for an
    # `(chain, kind, token) IN ((...), ...)` predicate built from the
    # registry. The chain/kind/token query-string params are ignored when
    # `compound` is set.
    pairs: list[tuple[str, str, str]] | None = None
    if compound_name:
        spec = get_compound(compound_name)
        if spec is None:
            return response.json({"error": f"unknown compound {compound_name!r}"}, status=400)
        pairs = compound_pairs(compound_name)
        if not pairs:
            return response.json({"error": f"compound {compound_name!r} has no pairs"}, status=400)
    else:
        if not chain or not token or not kind:
            return response.json({"error": "missing chain/kind/token"}, status=400)

    if interval not in INTERVAL_SECONDS:
        return response.json({"error": f"invalid interval; allowed: {list(INTERVAL_SECONDS)}"}, status=400)
    if not since or not until:
        return response.json({"error": "missing since/until"}, status=400)

    seconds = INTERVAL_SECONDS[interval]
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    # Address-normalisation mode for the wallet-filter predicates: pass the
    # single chain in single-stream mode, None in compound mode (so the
    # helpers emit per-row `if(chain IN EVM, lower(addr), addr)`).
    addr_chain: str | None = None if pairs is not None else chain

    # ---- Legacy single-filter params (kept so direct curl calls still work) ----
    cat_specs = [
        ("sender",    "in", _parse_csv(request.args.get("sender_in"))),
        ("sender",    "ex", _parse_csv(request.args.get("sender_ex"))),
        ("receiver",  "in", _parse_csv(request.args.get("receiver_in"))),
        ("receiver",  "ex", _parse_csv(request.args.get("receiver_ex"))),
        ("involving", "in", _parse_csv(request.args.get("involving_in"))),
        ("involving", "ex", _parse_csv(request.args.get("involving_ex"))),
    ]
    entity_specs = [
        ("sender",    "in", _parse_csv(request.args.get("sender_entity_in"))),
        ("sender",    "ex", _parse_csv(request.args.get("sender_entity_ex"))),
        ("receiver",  "in", _parse_csv(request.args.get("receiver_entity_in"))),
        ("receiver",  "ex", _parse_csv(request.args.get("receiver_entity_ex"))),
        ("involving", "in", _parse_csv(request.args.get("involving_entity_in"))),
        ("involving", "ex", _parse_csv(request.args.get("involving_entity_ex"))),
    ]
    where_clauses: list[str] = []
    legacy_params: dict = {}
    for side, action, cats in cat_specs:
        pred = _build_wallet_predicate(addr_chain, side, action, cats)
        if pred is None:
            continue
        sql_frag, param_name = pred
        where_clauses.append("AND " + sql_frag)
        # Case-insensitive — pre-lowercase the user input so it matches the
        # `lower(c)` projection on the dictionary side.
        legacy_params[param_name] = [str(c).lower() for c in cats]
    for side, action, vals in entity_specs:
        pred = _build_entity_predicate(addr_chain, side, action, vals)
        if pred is None:
            continue
        sql_frag, param_name = pred
        where_clauses.append("AND " + sql_frag)
        legacy_params[param_name] = [str(v).lower() for v in vals]
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

    extra_clauses, extra_params = _build_extra_sumif_clauses(addr_chain, extras)
    extra_select_sql = (
        ",\n            " + ",\n            ".join(extra_clauses)
        if extra_clauses
        else ""
    )

    # Build the chain/kind/token predicate — single tuple or multi-tuple IN list.
    ch_params: dict = {
        "seconds": seconds,
        "since": since_dt,
        "until": until_dt,
        "limit": limit,
        **legacy_params,
        **extra_params,
    }
    if pairs is not None:
        # Pairs come from a server-defined registry, never user input — safe to
        # inline as a SQL tuple literal. Quoting is paranoia, not necessity.
        def _q(s: str) -> str:
            return "'" + s.replace("'", "''") + "'"
        tup_sql = ", ".join(
            f"({_q(c)}, {_q(k)}, {_q(t)})" for (c, k, t) in pairs
        )
        ckt_where = f"(chain, kind, token) IN ({tup_sql})"
    else:
        ckt_where = (
            "chain = {chain:String} AND kind = {kind:String} AND token = {token:String}"
        )
        ch_params["chain"] = chain
        ch_params["kind"] = kind
        ch_params["token"] = token

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum(amount)             AS sum_amount,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                 AS count{extra_select_sql}
        FROM tradernick.transfers
        WHERE {ckt_where}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
          {where_extra_sql}
        GROUP BY bucket
        ORDER BY bucket
        LIMIT {{limit:UInt32}}
    """

    ch = await client()
    rows = await ch.query(sql, parameters=ch_params)

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

    resp_body: dict = {
        "interval": interval,
        "extras": [e["id"] for e in extras],
        "series": series,
    }
    if pairs is not None:
        resp_body["compound"] = compound_name
        resp_body["pairs"] = [{"chain": c, "kind": k, "token": t} for (c, k, t) in pairs]
    else:
        resp_body["chain"] = chain
        resp_body["kind"] = kind
        resp_body["token"] = token
    return response.json(resp_body)


# --- /transfers/categories + /transfers/entities ------------------------------
_CATS_CACHE: dict = {"at": 0.0, "value": None}
_CATS_TTL_SECONDS = 300.0
_cats_lock = asyncio.Lock()

_ENTS_CACHE: dict = {"at": 0.0, "value": None}
_ENTS_TTL_SECONDS = 300.0
_ents_lock = asyncio.Lock()


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


async def _fetch_entities() -> list[dict]:
    ch = await client()
    rows = await ch.query(
        """
        SELECT entity AS e, count() AS n
        FROM tradernick.wallets FINAL
        WHERE entity IS NOT NULL AND entity != ''
        GROUP BY e
        ORDER BY n DESC
        LIMIT 200
        """
    )
    return [{"name": r[0], "count": int(r[1])} for r in rows.result_rows]


@bp.get("/transfers/entities")
async def entities(_request):
    now = time.monotonic()
    if _ENTS_CACHE["value"] is not None and now - _ENTS_CACHE["at"] < _ENTS_TTL_SECONDS:
        return response.json({"entities": _ENTS_CACHE["value"]})
    async with _ents_lock:
        now = time.monotonic()
        if _ENTS_CACHE["value"] is None or now - _ENTS_CACHE["at"] >= _ENTS_TTL_SECONDS:
            _ENTS_CACHE["value"] = await _fetch_entities()
            _ENTS_CACHE["at"] = now
    return response.json({"entities": _ENTS_CACHE["value"]})
