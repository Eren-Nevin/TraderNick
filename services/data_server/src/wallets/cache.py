"""Read-through cache for resolved smart-wallet leaderboards.

The expensive half of a smart-money request is `SmartSelector.build_cte()`
resolving the per-day wallet set. That output is deterministic — for a given
filter + token + day the wallets never change — and daily-granular, so a wide
window and a narrow window share their overlapping days.

This module caches the resolved `(filter_hash, token, day) -> wallets[]` rows
in a ClickHouse table and serves `smart_wallets(day, wallets[])` from it,
filling only the days it has never seen. The OI query joins the result exactly
as it joined `build_cte`'s output before — same (cte_sql, name, params) shape.

Freshness / eviction:
  * Days within SMART_CACHE_SETTLE_DAYS of today are still being ingested, so
    they're resolved LIVE on every request and never cached.
  * Older days are immutable and cached.
  * Rows evict SMART_CACHE_TTL_DAYS after they were last computed (TTL on
    `computed_at`). Because editing a filter changes its content hash, an
    abandoned variant's rows are never refreshed and age out on their own —
    the cache self-trims under rapid filter editing instead of growing.

Cache key = full content hash of the filter (lookback / top_n / scope /
sort_by / criteria incl. per-criterion lookback / refs), plus token. The same
filter on a different token is a DIFFERENT entry, because token-scope criteria
resolve against that token. A filter that uses NO token scope is token-
independent, so it's cached once under '*' and shared across all tokens.
"""
from datetime import date, datetime, time, timedelta

import config

TABLE = "tradernick.smart_wallets_cache"


async def ensure_table(ch) -> None:
    """Create the cache table if absent. Safe to call on every startup."""
    await ch.command(
        f"CREATE TABLE IF NOT EXISTS {TABLE} (\n"
        "    filter_hash  String,\n"
        "    token        LowCardinality(String),\n"
        "    day          Date,\n"
        "    wallets      Array(String),\n"
        "    computed_at  DateTime DEFAULT now()\n"
        ") ENGINE = ReplacingMergeTree(computed_at)\n"
        "ORDER BY (filter_hash, token, day)\n"
        f"TTL computed_at + INTERVAL {int(config.SMART_CACHE_TTL_DAYS)} DAY"
    )


def _cache_token(selector, token: str | None) -> str:
    """'*' for token-independent (global-only) filters so they share one entry
    across tokens; the chart token otherwise."""
    return (token or "") if selector.uses_token_scope() else "*"


def _strip_with(cte_sql: str) -> str:
    """`build_cte` returns 'WITH\\n        <blocks>'. Strip the leading WITH so
    the blocks can be merged into a single composed WITH clause."""
    return cte_sql[len("WITH"):].lstrip("\n ")


async def _fill(ch, selector, h: str, ctok: str,
                since_d: date, settled_end: date) -> None:
    """Resolve and insert any settled days in [since_d, settled_end) that the
    cache doesn't yet have. Gated days (no leaderboard) are stored as empty
    arrays so they count as 'covered' and aren't re-resolved every request."""
    res = await ch.query(
        f"SELECT day FROM {TABLE} "
        "WHERE filter_hash = {h:String} AND token = {t:String} "
        "AND day >= {s:Date} AND day < {e:Date}",
        parameters={"h": h, "t": ctok, "s": since_d, "e": settled_end},
    )
    cached = {r[0] for r in res.result_rows}
    all_days = [since_d + timedelta(days=i)
                for i in range((settled_end - since_d).days)]
    missing = [d for d in all_days if d not in cached]
    if not missing:
        return

    fill_since, fill_end = min(missing), max(missing) + timedelta(days=1)
    sel_cte, sel_name, sel_params = selector.build_cte(
        datetime.combine(fill_since, time.min),
        datetime.combine(fill_end, time.min),
    )
    params = {**sel_params, "f_h": h, "f_t": ctok,
              "f_since": fill_since, "f_n": (fill_end - fill_since).days}
    insert_sql = (
        f"INSERT INTO {TABLE} (filter_hash, token, day, wallets)\n"
        "WITH\n        " + _strip_with(sel_cte) + ",\n"
        "        _cal AS (SELECT {f_since:Date} + number AS d "
        "FROM numbers(0, {f_n:UInt32}))\n"
        "SELECT {f_h:String}, {f_t:String}, _cal.d AS day,\n"
        "       ifNull(sw.wallets, emptyArrayString()) AS wallets\n"
        "FROM _cal\n"
        f"LEFT JOIN {sel_name} sw ON sw.day = _cal.d\n"
        "WHERE _cal.d NOT IN (\n"
        f"    SELECT day FROM {TABLE} "
        "WHERE filter_hash = {f_h:String} AND token = {f_t:String}\n"
        ")"
    )
    await ch.command(insert_sql, parameters=params)


async def resolve(ch, selector, token: str | None,
                  since_dt: datetime, until_dt: datetime):
    """Cache-backed twin of SmartSelector.build_cte. Returns
    (cte_sql, 'smart_wallets', params). Settled days come from the cache table
    (filling misses first); days within the settle window are resolved live and
    UNION-ed in."""
    if not config.SMART_CACHE_ENABLED:
        return selector.build_cte(since_dt, until_dt)

    h = selector.cache_key()
    ctok = _cache_token(selector, token)
    since_d, until_d = since_dt.date(), until_dt.date()
    live_from = date.today() - timedelta(days=int(config.SMART_CACHE_SETTLE_DAYS))

    # Whole window is within the live (still-ingesting) range → no caching.
    if since_d >= live_from:
        return selector.build_cte(since_dt, until_dt)

    settled_end = min(live_from, until_d + timedelta(days=1))  # exclusive
    await _fill(ch, selector, h, ctok, since_d, settled_end)

    params: dict = {"ck_h": h, "ck_t": ctok, "ck_ss": since_d, "ck_se": settled_end}
    union_parts = [
        f"SELECT day, wallets FROM {TABLE} FINAL\n"
        "            WHERE filter_hash = {ck_h:String} AND token = {ck_t:String}\n"
        "              AND day >= {ck_ss:Date} AND day < {ck_se:Date}"
    ]

    head = ""
    if until_d >= live_from:  # at least one live day to resolve
        live_since = datetime.combine(live_from, time.min)
        live_cte_sql, live_name, live_params = selector.build_cte(
            live_since, until_dt, final_name="smart_wallets_live")
        params.update(live_params)
        head = _strip_with(live_cte_sql) + ",\n        "
        union_parts.append(f"SELECT day, wallets FROM {live_name}")

    union_sql = "\n            UNION ALL\n            ".join(union_parts)
    cte_sql = (
        "WITH\n        " + head
        + "smart_wallets AS (\n            " + union_sql + "\n        )"
    )
    return cte_sql, "smart_wallets", params
