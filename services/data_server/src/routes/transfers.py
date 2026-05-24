import asyncio
import time
from datetime import datetime, timezone

from sanic import Blueprint, response

from clickhouse import client
from routes.ohlcv import INTERVAL_SECONDS

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

    # Wallet-label filter inputs. Each is a comma-separated list of category names.
    filter_specs = [
        ("sender",    "in", _parse_csv(request.args.get("sender_in"))),
        ("sender",    "ex", _parse_csv(request.args.get("sender_ex"))),
        ("receiver",  "in", _parse_csv(request.args.get("receiver_in"))),
        ("receiver",  "ex", _parse_csv(request.args.get("receiver_ex"))),
        ("involving", "in", _parse_csv(request.args.get("involving_in"))),
        ("involving", "ex", _parse_csv(request.args.get("involving_ex"))),
    ]

    extra_clauses: list[str] = []
    extra_params: dict = {}
    for side, action, cats in filter_specs:
        pred = _build_wallet_predicate(chain, side, action, cats)
        if pred is None:
            continue
        sql_frag, param_name = pred
        extra_clauses.append("AND " + sql_frag)
        extra_params[param_name] = cats

    extra_sql = "\n          ".join(extra_clauses)

    sql = f"""
        SELECT
            toUnixTimestamp(toStartOfInterval(time, INTERVAL {{seconds:UInt32}} SECOND)) AS bucket,
            sum(amount)             AS sum_amount,
            sum(coalesce(value_usd, 0)) AS sum_value_usd,
            count()                 AS count
        FROM tradernick.transfers
        WHERE chain = {{chain:String}}
          AND kind  = {{kind:String}}
          AND token = {{token:String}}
          AND time >= {{since:DateTime}}
          AND time <  {{until:DateTime}}
          {extra_sql}
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
            **extra_params,
        },
    )

    series = [
        {
            "time": int(r[0]),
            "sum_amount": float(r[1]),
            "sum_value_usd": float(r[2]),
            "count": int(r[3]),
        }
        for r in rows.result_rows
    ]
    return response.json({
        "chain": chain,
        "kind": kind,
        "token": token,
        "interval": interval,
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
