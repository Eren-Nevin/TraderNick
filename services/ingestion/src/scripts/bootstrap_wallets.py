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


async def _wait_for_mutations(ch, *, table: str, prefix: str, timeout_s: float = 7200.0,
                              progress_cb=None, progress_low: float = 0.0,
                              progress_high: float = 1.0):
    """Poll system.mutations until every recent mutation matching `prefix` is
    done. Used between the column-rewrite and index-rebuild phases so DROP
    INDEX doesn't bounce off in-flight column mutations.

    If `progress_cb` is supplied, it's called each poll with a value
    linearly interpolated between `progress_low` and `progress_high` based
    on the fraction of parts already migrated. Lets long-running waits
    surface a moving progress bar instead of a frozen number.
    """
    import asyncio as _asyncio  # local import — module is asyncio-aware
    db, tbl = table.split(".", 1)
    deadline = _asyncio.get_event_loop().time() + timeout_s
    # Snapshot the initial parts_to_do once so each poll computes a stable
    # fraction. Without this the denominator would keep shrinking and the
    # progress bar would never reach 100%.
    initial_total = None
    while True:
        rows = await ch.query(
            """
            SELECT countIf(NOT is_done), sum(parts_to_do)
            FROM system.mutations
            WHERE database = {db:String}
              AND table = {tbl:String}
              AND command LIKE {pref:String}
              AND create_time > now() - INTERVAL 1 DAY
            """,
            parameters={"db": db, "tbl": tbl, "pref": prefix + "%"},
        )
        n = int(rows.result_rows[0][0]) if rows.result_rows else 0
        parts_remaining = int(rows.result_rows[0][1] or 0) if rows.result_rows else 0
        if initial_total is None:
            # First sample becomes the denominator. If the mutation table
            # was already at zero on entry we treat the stage as complete.
            initial_total = max(parts_remaining, 1)
        if progress_cb is not None:
            done_frac = max(0.0, min(1.0, 1.0 - parts_remaining / initial_total))
            cur = progress_low + (progress_high - progress_low) * done_frac
            try:
                await progress_cb(f"{prefix.strip()} ({parts_remaining} parts left)", cur)
            except Exception:  # noqa: BLE001
                log.exception("progress_cb failed (continuing)")
        if n == 0:
            return
        if _asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(f"timed out waiting for {prefix} mutations on {table}")
        await _asyncio.sleep(5)


async def _rematerialize_worker(table: str, progress_cb=None):
    """The long-running half of rematerialize_transfers — runs as a Sanic
    background task so the POST endpoint returns immediately.

    `progress_cb` is an optional `async (stage_label: str, progress: float) -> None`
    callback. Called at every stage boundary, plus inside the long
    MATERIALIZE COLUMN wait (`_wait_for_mutations` linearly maps
    parts_to_do → fraction). When None, no progress is reported (preserves
    the original behaviour used by the wallet-upload code path)."""
    ch = await async_client()

    async def _emit(stage: str, p: float):
        if progress_cb is None:
            return
        try:
            await progress_cb(stage, p)
        except Exception:  # noqa: BLE001
            log.exception("progress_cb failed (continuing)")

    try:
        await _emit("reloading wallet_labels dictionary", 0.02)
        log.info("rematerialize[worker]: reloading wallet_labels dictionary")
        await ch.command("SYSTEM RELOAD DICTIONARY tradernick.wallet_labels")

        materialize_cols = ", ".join(f"MATERIALIZE COLUMN {c}" for c in _TRANSFERS_WALLET_COLUMNS)
        log.info("rematerialize[worker]: kicking MATERIALIZE COLUMN ×%d", len(_TRANSFERS_WALLET_COLUMNS))
        await _emit(f"kicking MATERIALIZE COLUMN ×{len(_TRANSFERS_WALLET_COLUMNS)}", 0.05)
        await ch.command(
            f"ALTER TABLE {table} {materialize_cols} SETTINGS mutations_sync = 0, alter_sync = 0"
        )

        # Wait for the column rewrites to settle before touching the indexes —
        # DROP INDEX will block on any in-flight mutation against the same
        # table, and we'd rather hold that wait inside this background task
        # than inside the HTTP request. Progress between 0.05 → 0.60 maps to
        # the fraction of MATERIALIZE COLUMN parts that have already migrated.
        log.info("rematerialize[worker]: waiting for MATERIALIZE COLUMN to finish")
        await _wait_for_mutations(
            ch, table=table, prefix="MATERIALIZE COLUMN ",
            progress_cb=progress_cb, progress_low=0.05, progress_high=0.60,
        )
        await _emit("MATERIALIZE COLUMN done", 0.60)

        # exchange_flow_minute is downstream of the sender/receiver_categories
        # + sender_entity materialized columns we just rewrote. The
        # data_processor worker is the sole maintainer of that rollup now;
        # it would converge on its own via the next sweep cycle (≤6h) but
        # we want post-rematerialize freshness in minutes — so kick a
        # one-shot backfill_data_processor job covering the last 30 days.
        try:
            await _kick_exchange_flow_rebuild()
        except Exception:
            log.exception("rematerialize[worker]: exchange_flow rebuild kick failed (continuing)")
        await _emit("exchange_flow rebuild kicked", 0.65)

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
        await _emit("indexes dropped", 0.75)
        adds = ", ".join(
            f"ADD INDEX {name} {col} TYPE {t} GRANULARITY {g}"
            for name, col, t, g in _TRANSFERS_WALLET_INDEXES
        )
        await ch.command(f"ALTER TABLE {table} {adds} SETTINGS alter_sync = 0")
        log.info("rematerialize[worker]: re-added %d indexes", len(_TRANSFERS_WALLET_INDEXES))
        await _emit("indexes re-added", 0.85)

        materialize_idx = ", ".join(
            f"MATERIALIZE INDEX {n}" for n, _c, _t, _g in _TRANSFERS_WALLET_INDEXES
        )
        await ch.command(
            f"ALTER TABLE {table} {materialize_idx} SETTINGS mutations_sync = 0, alter_sync = 0"
        )
        log.info("rematerialize[worker]: kicked MATERIALIZE INDEX ×%d", len(_TRANSFERS_WALLET_INDEXES))
        await _emit(f"MATERIALIZE INDEX ×{len(_TRANSFERS_WALLET_INDEXES)} kicked", 0.95)
    except Exception:
        log.exception("rematerialize[worker]: failed")
        raise


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
# The dedicated staging-swap refresh worker that used to live here has been
# replaced by the unified `data_processor` module (services/ingestion/src/
# data_processor/). The two public surfaces preserved for compatibility are:
#
#   refresh_exchange_flow()           — POST /admin/exchange-flow/refresh
#                                       Enqueues a backfill_data_processor
#                                       job for materializers=
#                                       ["exchange_flow_minute"], window =
#                                       last 30 days.
#
#   exchange_flow_refresh_status()    — GET /admin/exchange-flow/refresh/status
#                                       Returns the most recent
#                                       backfill_data_processor job whose
#                                       args.materializers is just
#                                       ["exchange_flow_minute"].
#
# Same behaviour is also called inline at the end of `_rematerialize_worker`
# via `_kick_exchange_flow_rebuild()` so a wallet-labels reload still
# converges the rollup automatically.

# Backfill window for refresh/rematerialize-triggered rebuilds. The
# data_processor's own sweep tier covers 30d in the background; matching
# that here means the on-demand rebuild always covers the full TTL window.
_REBUILD_LOOKBACK_DAYS = 30


async def _kick_exchange_flow_rebuild() -> dict:
    """Enqueue a one-shot backfill_data_processor job that rebuilds
    exchange_flow_minute over the last `_REBUILD_LOOKBACK_DAYS` days.
    Returns the job row dict (same shape as POST /jobs/backfill/<type>).

    Falls back to a synchronous in-process rebuild only when the Sanic app
    isn't reachable (CLI / test path) — in that mode we just call the
    backfill main() inline."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    until = _dt.now(_tz.utc).replace(tzinfo=None)
    since = until - _td(days=_REBUILD_LOOKBACK_DAYS)

    try:
        from sanic import Sanic
        app = Sanic.get_app("tradernick_ingestion")
        jobs = app.ctx.jobs
    except Exception:
        log.warning("no Sanic app — exchange_flow rebuild kick skipped")
        return {"ok": False, "reason": "no_app"}

    from jobs.manager import JOB_TYPE_BACKFILL_DATA_PROCESSOR
    job = await jobs.create_backfill_args(
        JOB_TYPE_BACKFILL_DATA_PROCESSOR,
        since, until,
        {"materializers": ["exchange_flow_minute"],
         "triggered_by": "rematerialize"},
    )
    log.info("exchange_flow rebuild enqueued: job_id=%s window=[%s, %s)",
             job.get("job_id"), since, until)
    return {"ok": True, "job_id": job.get("job_id")}


async def refresh_exchange_flow() -> dict:
    """Public entry point for POST /admin/exchange-flow/refresh — kicks a
    data_processor backfill for the last 30d and returns immediately.
    Caller polls /admin/exchange-flow/refresh/status to watch progress."""
    res = await _kick_exchange_flow_rebuild()
    return {
        "ok": bool(res.get("ok")),
        "table": "tradernick.exchange_flow_minute",
        "job_id": res.get("job_id"),
        "note": "poll /admin/exchange-flow/refresh/status for progress",
    }


async def exchange_flow_refresh_status() -> dict:
    """Return the most-recent backfill_data_processor job that touches
    materializers=['exchange_flow_minute']. Shape matches what the old
    in-memory _EXCHANGE_FLOW_STATE dict exposed so existing dashboard
    code doesn't need to change."""
    from jobs.manager import JOB_TYPE_BACKFILL_DATA_PROCESSOR
    ch = await async_client()
    rows = await ch.query(
        """
        SELECT job_id, status, progress, started_at, finished_at, error, updated_at, args
        FROM tradernick.ingestion_jobs FINAL
        WHERE job_type = {jt:String}
          AND args LIKE '%"exchange_flow_minute"%'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        parameters={"jt": JOB_TYPE_BACKFILL_DATA_PROCESSOR},
    )
    if not rows.result_rows:
        return {"running": False, "started_at": None, "finished_at": None,
                "error": None, "job_id": None}
    r = rows.result_rows[0]
    job_id, status, progress, started_at, finished_at, error, _updated_at, _args = r
    running = status in ("pending", "running")
    return {
        "running": running,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "error": error,
        "progress": float(progress) if progress is not None else None,
        "job_id": job_id,
        "status": status,
    }


async def main_async(argv: list[str] | None = None):
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=DEFAULT_PARQUET, help=f"path to wallets parquet (default {DEFAULT_PARQUET})")
    args = p.parse_args(argv)
    summary = await load_into_clickhouse(args.src)
    log.info("summary: %s", summary)


if __name__ == "__main__":
    asyncio.run(main_async())
