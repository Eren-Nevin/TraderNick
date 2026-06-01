"""Load a wallets parquet into tradernick.wallets and refresh the wallet_labels dictionary.

The parquet must have columns: wallet (string), entity (nullable string),
categories (list of strings). Anything extra is ignored. The `labels` column on
Horatio's source parquet is also ignored for now.

For every address starting with `0x` we insert TWO rows — original casing AND a
lowercase variant — so EVM lookups (which call `lower(sender)` on the query side)
hit regardless of which case the source happened to use. BTC bech32 and TRON
base58 addresses are inserted verbatim (case-sensitive on both sides).

Usable both standalone (one-off bootstrap) and as a helper called from the
admin upload endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections import Counter

import polars as pl

from clickhouse import async_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bootstrap_wallets] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


DEFAULT_PARQUET = os.environ.get("WALLETS_PARQUET_PATH", "/app/data/wallets.parquet")


# Manual wallet entries appended after every parquet load. The Horatio
# wallets parquet doesn't ship labels for perp-DEX bridges, so we hard-code
# the few we care about here. A row appears in the table exactly the same
# shape as parquet-loaded rows (address / categories / entity); EVM ones
# also get lowercased automatically via _normalize_rows.
MANUAL_WALLETS: list[dict] = [
    {
        "address": "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7",
        "categories": ["Perp", "Deposit", "Hyperliquid-Bridge", "Hot-Wallet", "Hyperliquid-Deposit"],
        "entity": "Hyperliquid",
    },
]


def _prefix_bucket(addr: str) -> str:
    if not addr:
        return "(empty)"
    if addr.startswith("0x"):
        return "0x..."
    if addr.startswith("0:"):
        return "0:..."
    if addr.startswith("41"):
        return "41... (TRON-hex)"
    if addr.startswith("T") and len(addr) >= 30:
        return "T... (TRON-base58)"
    if addr.startswith("bc1"):
        return "bc1... (BTC-bech32)"
    if addr.startswith(("1", "3")) and 25 <= len(addr) <= 36:
        return "1.../3... (BTC-legacy)"
    return "(other)"


def _normalize_rows(df: pl.DataFrame) -> pl.DataFrame:
    """Return (address, categories, entity) rows.

    For EVM (`0x…`) wallets we emit both the original and lowercase casing so the
    dictionary matches either; non-EVM addresses keep their case.
    """
    required = {"wallet", "categories"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"parquet missing required column(s): {missing}")
    if "entity" not in df.columns:
        df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias("entity"))

    base = df.select(
        pl.col("wallet").cast(pl.Utf8).alias("address"),
        pl.col("categories").cast(pl.List(pl.Utf8)),
        pl.col("entity").cast(pl.Utf8),
    ).filter(pl.col("address").is_not_null() & (pl.col("address").str.len_chars() > 0))

    # Emit original-case rows + lowercase duplicates for `0x…`. We deduplicate after
    # to drop the cases where the original was already lowercase.
    original = base
    lc = base.filter(pl.col("address").str.starts_with("0x")).with_columns(
        pl.col("address").str.to_lowercase()
    )
    combined = pl.concat([original, lc], how="vertical").unique(subset=["address"])
    return combined


async def load_into_clickhouse(src_path: str) -> dict:
    """Read parquet at src_path, replace the wallets table, reload the dictionary.

    Returns a small dict describing what happened (row count, prefix breakdown).
    """
    if not os.path.exists(src_path):
        raise FileNotFoundError(src_path)
    log.info("reading parquet from %s", src_path)
    df = pl.read_parquet(src_path)
    log.info("source rows: %d", len(df))
    # Splice the hardcoded MANUAL_WALLETS in before normalisation so EVM ones
    # get the same lowercase-duplicate treatment as parquet rows. We map the
    # column names to match the source parquet shape (wallet/categories/entity).
    if MANUAL_WALLETS:
        manual_df = pl.DataFrame(
            {
                "wallet":     [w["address"] for w in MANUAL_WALLETS],
                "categories": [w["categories"] for w in MANUAL_WALLETS],
                "entity":     [w["entity"] for w in MANUAL_WALLETS],
            }
        )
        # Align column types/order with the source parquet to avoid concat
        # surprises if the parquet evolves.
        keep = [c for c in df.columns if c in manual_df.columns]
        df = pl.concat([df.select(keep), manual_df.select(keep)], how="diagonal_relaxed")
        log.info("appended %d manual wallets (post-merge total: %d)", len(MANUAL_WALLETS), len(df))
    rows = _normalize_rows(df)
    log.info("normalised rows (including EVM lowercase variants): %d", len(rows))

    prefix_counts = Counter(_prefix_bucket(a) for a in rows.get_column("address").to_list())
    log.info("address prefix distribution:")
    for k, n in prefix_counts.most_common():
        log.info("  %-26s %d", k, n)

    pd_df = rows.to_pandas()
    pd_df["entity"] = pd_df["entity"].where(pd_df["entity"].notna(), None)

    ch = await async_client()
    log.info("TRUNCATE tradernick.wallets")
    await ch.command("TRUNCATE TABLE tradernick.wallets")
    log.info("inserting %d rows…", len(pd_df))
    await ch.insert_df("tradernick.wallets", pd_df)
    log.info("reloading dictionary")
    await ch.command("SYSTEM RELOAD DICTIONARY tradernick.wallet_labels")
    log.info("done")
    return {
        "rows": len(pd_df),
        "prefix_counts": dict(prefix_counts),
        "src": src_path,
    }


# Columns whose values come from `dictGet(tradernick.wallet_labels, ...)` and
# therefore need to be rewritten when the wallets table changes. Keep this in
# sync with the MATERIALIZED columns declared in clickhouse/init/01_schema.sql.
_TRANSFERS_WALLET_COLUMNS = (
    "sender_categories",
    "receiver_categories",
    "sender_entity",
    "receiver_entity",
)

# Skip indexes layered on top of those columns. We DROP + ADD them rather
# than MATERIALIZE INDEX in place because in CH 24.x MATERIALIZE INDEX can
# carry over the prior index state and continue producing false negatives
# (this is what was actually biting us — see commit history). DROP + ADD
# resets the index to "empty" and the subsequent MATERIALIZE INDEX builds
# it from the current column data.
_TRANSFERS_WALLET_INDEXES: tuple[tuple[str, str, str, int], ...] = (
    # (index_name, column_expr, type_def, granularity)
    ("idx_sender_categories",   "sender_categories",   "set(100)", 4),
    ("idx_receiver_categories", "receiver_categories", "set(100)", 4),
    ("idx_sender_entity",       "sender_entity",       "set(500)", 4),
    ("idx_receiver_entity",     "receiver_entity",     "set(500)", 4),
)


async def _wait_for_mutations(ch, *, table: str, prefix: str, timeout_s: float = 7200.0):
    """Poll system.mutations until every recent mutation matching `prefix` is
    done. Used between the column-rewrite and index-rebuild phases so DROP
    INDEX doesn't bounce off in-flight column mutations.
    """
    import asyncio as _asyncio  # local import — module is asyncio-aware
    db, tbl = table.split(".", 1)
    deadline = _asyncio.get_event_loop().time() + timeout_s
    while True:
        rows = await ch.query(
            """
            SELECT countIf(NOT is_done)
            FROM system.mutations
            WHERE database = {db:String}
              AND table = {tbl:String}
              AND command LIKE {pref:String}
              AND create_time > now() - INTERVAL 1 DAY
            """,
            parameters={"db": db, "tbl": tbl, "pref": prefix + "%"},
        )
        n = int(rows.result_rows[0][0]) if rows.result_rows else 0
        if n == 0:
            return
        if _asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(f"timed out waiting for {prefix} mutations on {table}")
        await _asyncio.sleep(5)


async def _rematerialize_worker(table: str):
    """The long-running half of rematerialize_transfers — runs as a Sanic
    background task so the POST endpoint returns immediately."""
    ch = await async_client()
    try:
        log.info("rematerialize[worker]: reloading wallet_labels dictionary")
        await ch.command("SYSTEM RELOAD DICTIONARY tradernick.wallet_labels")

        materialize_cols = ", ".join(f"MATERIALIZE COLUMN {c}" for c in _TRANSFERS_WALLET_COLUMNS)
        log.info("rematerialize[worker]: kicking MATERIALIZE COLUMN ×%d", len(_TRANSFERS_WALLET_COLUMNS))
        await ch.command(
            f"ALTER TABLE {table} {materialize_cols} SETTINGS mutations_sync = 0, alter_sync = 0"
        )

        # Wait for the column rewrites to settle before touching the indexes —
        # DROP INDEX will block on any in-flight mutation against the same
        # table, and we'd rather hold that wait inside this background task
        # than inside the HTTP request.
        log.info("rematerialize[worker]: waiting for MATERIALIZE COLUMN to finish")
        await _wait_for_mutations(ch, table=table, prefix="MATERIALIZE COLUMN ")

        # exchange_flow_minute (SummingMergeTree fed by mv_exchange_flow) is
        # downstream of the sender/receiver_categories + sender_entity
        # materialized columns we just rewrote. The MV fires on INSERT only,
        # so retroactive column changes don't propagate — we must drop +
        # backfill the rollup to bring it in sync with the refreshed dict.
        # Runs sequentially here so the rematerialize/status endpoint reflects
        # the end-to-end state (column rewrite → index rebuild → rollup
        # refresh) in a single mutation queue.
        try:
            await _refresh_exchange_flow_worker(ch)
        except Exception:
            log.exception("rematerialize[worker]: exchange_flow refresh failed (continuing)")

        # Index reset — DROP every one, then ADD them back, then MATERIALIZE.
        # `alter_sync = 0` so the DDL returns once metadata is applied (no
        # extra replica sync wait — we're single-node anyway).
        for name, _col, _type, _gran in _TRANSFERS_WALLET_INDEXES:
            try:
                await ch.command(
                    f"ALTER TABLE {table} DROP INDEX {name} SETTINGS alter_sync = 0"
                )
                log.info("rematerialize[worker]: dropped %s", name)
            except Exception as exc:
                log.info("rematerialize[worker]: skip DROP INDEX %s (%s)", name, exc)
        adds = ", ".join(
            f"ADD INDEX {name} {col} TYPE {t} GRANULARITY {g}"
            for name, col, t, g in _TRANSFERS_WALLET_INDEXES
        )
        await ch.command(f"ALTER TABLE {table} {adds} SETTINGS alter_sync = 0")
        log.info("rematerialize[worker]: re-added %d indexes", len(_TRANSFERS_WALLET_INDEXES))

        materialize_idx = ", ".join(
            f"MATERIALIZE INDEX {n}" for n, _c, _t, _g in _TRANSFERS_WALLET_INDEXES
        )
        await ch.command(
            f"ALTER TABLE {table} {materialize_idx} SETTINGS mutations_sync = 0, alter_sync = 0"
        )
        log.info("rematerialize[worker]: kicked MATERIALIZE INDEX ×%d", len(_TRANSFERS_WALLET_INDEXES))
    except Exception:
        log.exception("rematerialize[worker]: failed")


async def rematerialize_transfers(*, table: str = "tradernick.transfers") -> dict:
    """Apply the post-wallet-change recovery sequence to the transfers table.

    Schema-side, `<sender|receiver>_categories` and `<sender|receiver>_entity`
    are MATERIALIZED columns sourced from the wallet_labels dictionary, with
    set() skip indexes layered on top. Adding or editing rows in the wallets
    table doesn't retroactively update either — historical rows keep whatever
    the dictGet returned at insert time, and the skip indexes encode the old
    granule-level value set. The fix is:

      1. SYSTEM RELOAD DICTIONARY — refresh the in-memory dict.
      2. MATERIALIZE COLUMN ×4 — rewrite each materialized column for every
         existing part using the current dictionary state.
      3. Wait for column mutations to finish (so DROP INDEX doesn't block).
      4. DROP INDEX + ADD INDEX + MATERIALIZE INDEX ×4 — fully reset each
         skip index. DROP+ADD because in CH 24.x MATERIALIZE INDEX alone can
         preserve prior granule-set state and keep skipping rows that now
         match.

    The whole sequence runs as a Sanic background task so the HTTP request
    returns immediately. Caller polls /admin/wallets/rematerialize/status for
    progress. We import the Sanic app lazily so this module also works from
    the standalone `python -m scripts.bootstrap_wallets` CLI path.
    """
    try:
        from sanic import Sanic
        app = Sanic.get_app("tradernick_ingestion")
        app.add_task(_rematerialize_worker(table))
        dispatch = "background_task"
    except Exception:
        # Not running inside Sanic (e.g. CLI bootstrap). Run inline.
        await _rematerialize_worker(table)
        dispatch = "inline"
    return {
        "ok": True,
        "table": table,
        "columns": list(_TRANSFERS_WALLET_COLUMNS),
        "indexes": [n for n, _c, _t, _g in _TRANSFERS_WALLET_INDEXES],
        "dispatch": dispatch,
        "note": "poll /admin/wallets/rematerialize/status for progress",
    }


async def rematerialize_status(*, table: str = "tradernick.transfers") -> dict:
    """Return outstanding wallet-related mutations on the transfers table."""
    ch = await async_client()
    rows = await ch.query(
        """
        SELECT mutation_id, command, is_done, parts_to_do, latest_failed_part, latest_fail_reason
        FROM system.mutations
        WHERE database = splitByChar('.', {table:String})[1]
          AND table    = splitByChar('.', {table:String})[2]
          AND (command LIKE 'MATERIALIZE COLUMN %' OR command LIKE 'MATERIALIZE INDEX %')
        ORDER BY create_time DESC, command
        LIMIT 32
        """,
        parameters={"table": table},
    )
    mutations = [
        {
            "mutation_id": r[0],
            "command": r[1],
            "is_done": bool(r[2]),
            "parts_to_do": int(r[3]),
            "latest_failed_part": r[4] or None,
            "latest_fail_reason": r[5] or None,
        }
        for r in rows.result_rows
    ]
    pending = [m for m in mutations if not m["is_done"]]
    return {
        "table": table,
        "in_progress": len(pending),
        "mutations": mutations,
    }


# ---- exchange_flow rollup refresh ----------------------------------------
# Mirrors the predicate baked into tradernick.mv_exchange_flow (see
# clickhouse/init/01_schema.sql). Kept inline so the worker has no dependency
# on the data_server service; the schema file is the single source of truth
# but rebuild-by-INSERT-SELECT needs to repeat the predicate here.
_EXCHANGE_FLOW_REFRESH_SQL = """
INSERT INTO tradernick.exchange_flow_minute
SELECT
    classified.1 AS direction,
    classified.2 AS exchange,
    chain,
    token,
    toStartOfMinute(time) AS time,
    sum(amount) AS sum_amount,
    sum(if(token IN ('USDC', 'USDT', 'DAI', 'USDE'),
           amount,
           coalesce(value_usd, 0.0))) AS sum_value_usd,
    count() AS count
FROM tradernick.transfers
ARRAY JOIN arrayConcat(
    if(has(receiver_categories, 'binance-deposit')     AND NOT has(sender_categories, 'cex'),  [('in', 'binance')],     CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'coinbase-deposit')    AND NOT has(sender_categories, 'cex'),  [('in', 'coinbase')],    CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'okx-deposit')         AND NOT has(sender_categories, 'cex'),  [('in', 'okx')],         CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'bybit-deposit')       AND NOT has(sender_categories, 'cex'),  [('in', 'bybit')],       CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(receiver_categories, 'hyperliquid-deposit') AND NOT has(sender_categories, 'perp'), [('in', 'hyperliquid')], CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'binance'     AND NOT has(receiver_categories, 'cex'),         [('out', 'binance')],     CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'coinbase'    AND NOT has(receiver_categories, 'cex'),         [('out', 'coinbase')],    CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'okx'         AND NOT has(receiver_categories, 'cex'),         [('out', 'okx')],         CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'bybit'       AND NOT has(receiver_categories, 'cex'),         [('out', 'bybit')],       CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String))))),
    if(has(sender_categories, 'hot-wallet') AND coalesce(sender_entity, '') = 'hyperliquid' AND coalesce(receiver_entity, '') != 'hyperliquid', [('out', 'hyperliquid')], CAST([] AS Array(Tuple(LowCardinality(String), LowCardinality(String)))))
) AS classified
GROUP BY direction, exchange, chain, token, time
SETTINGS max_execution_time = 1800
"""


# Live state for /admin/exchange-flow/refresh/status. Single concurrent run
# guarded by the `running` flag; consecutive POSTs while running are no-ops.
_EXCHANGE_FLOW_STATE: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "duration_s": None,
    "rows_after": None,
    "error": None,
}


async def _refresh_exchange_flow_worker(ch=None) -> dict:
    """Rebuild tradernick.exchange_flow_minute from the current state of
    tradernick.transfers + its materialized category/entity columns. Called
    automatically after every rematerialize (so the rollup is consistent with
    the freshly-rewritten wallet labels) and exposed as
    POST /admin/exchange-flow/refresh for manual runs.

    Strategy: TRUNCATE the rollup then INSERT SELECT. The live mv_exchange_flow
    keeps firing on incoming transfers during the ~50s reinsert; their
    contributions may collide with the SELECT scan and be summed twice for
    buckets in the current minute, but that's a sub-promille noise floor at
    chart scale and avoids the bigger risk of dropping the MV mid-flight.
    """
    import asyncio as _asyncio
    if _EXCHANGE_FLOW_STATE["running"]:
        log.info("exchange_flow refresh: already running, skipping")
        return {"ok": False, "reason": "already_running"}
    _EXCHANGE_FLOW_STATE.update(
        running=True,
        started_at=_asyncio.get_event_loop().time(),
        finished_at=None,
        duration_s=None,
        rows_after=None,
        error=None,
    )
    t0 = _asyncio.get_event_loop().time()
    try:
        if ch is None:
            ch = await async_client()
        log.info("exchange_flow refresh: TRUNCATE tradernick.exchange_flow_minute")
        await ch.command("TRUNCATE TABLE tradernick.exchange_flow_minute")
        log.info("exchange_flow refresh: INSERT SELECT (30d backfill)")
        await ch.command(_EXCHANGE_FLOW_REFRESH_SQL)
        rows = await ch.query("SELECT count() FROM tradernick.exchange_flow_minute")
        n = int(rows.result_rows[0][0]) if rows.result_rows else 0
        dt = _asyncio.get_event_loop().time() - t0
        _EXCHANGE_FLOW_STATE.update(
            running=False,
            finished_at=_asyncio.get_event_loop().time(),
            duration_s=dt,
            rows_after=n,
        )
        log.info("exchange_flow refresh: done in %.1fs, %d rows", dt, n)
        return {"ok": True, "rows_after": n, "duration_s": dt}
    except Exception as exc:
        _EXCHANGE_FLOW_STATE.update(
            running=False,
            finished_at=_asyncio.get_event_loop().time(),
            duration_s=_asyncio.get_event_loop().time() - t0,
            error=str(exc),
        )
        log.exception("exchange_flow refresh: failed")
        raise


async def refresh_exchange_flow() -> dict:
    """Dispatch the rollup refresh as a Sanic background task so the POST
    returns immediately. Caller polls /admin/exchange-flow/refresh/status."""
    try:
        from sanic import Sanic
        app = Sanic.get_app("tradernick_ingestion")
        app.add_task(_refresh_exchange_flow_worker())
        dispatch = "background_task"
    except Exception:
        await _refresh_exchange_flow_worker()
        dispatch = "inline"
    return {
        "ok": True,
        "table": "tradernick.exchange_flow_minute",
        "dispatch": dispatch,
        "note": "poll /admin/exchange-flow/refresh/status for progress",
    }


def exchange_flow_refresh_status() -> dict:
    return dict(_EXCHANGE_FLOW_STATE)


async def main_async(argv: list[str] | None = None):
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=DEFAULT_PARQUET, help=f"path to wallets parquet (default {DEFAULT_PARQUET})")
    args = p.parse_args(argv)
    summary = await load_into_clickhouse(args.src)
    log.info("summary: %s", summary)


if __name__ == "__main__":
    asyncio.run(main_async())
