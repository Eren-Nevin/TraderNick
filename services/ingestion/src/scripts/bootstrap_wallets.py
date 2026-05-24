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


async def main_async(argv: list[str] | None = None):
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=DEFAULT_PARQUET, help=f"path to wallets parquet (default {DEFAULT_PARQUET})")
    args = p.parse_args(argv)
    summary = await load_into_clickhouse(args.src)
    log.info("summary: %s", summary)


if __name__ == "__main__":
    asyncio.run(main_async())
