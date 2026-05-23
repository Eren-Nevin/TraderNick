import re
from datetime import datetime, timezone

import clickhouse_connect
import polars as pl

import config

_async_client_obj = None

_IDENT_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def safe_ident(s) -> str:
    """Validate a chain/token/kind identifier for safe inline-SQL embedding.

    Allows the character set we actually use; anything else (quotes, spaces,
    semicolons, SQL operators) is rejected.
    """
    if not isinstance(s, str) or not _IDENT_RE.fullmatch(s):
        raise ValueError(f"invalid identifier: {s!r}")
    return s


def sql_dt(dt: datetime) -> str:
    """Format a naive datetime for inline SQL literal."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def delete_transfers_range(*, where_extra: str, since: datetime, until: datetime) -> None:
    """Lightweight DELETE on tradernick.transfers, scoped by a caller-built WHERE clause.

    The caller is responsible for ensuring `where_extra` uses only `safe_ident()`-validated
    identifiers (i.e. no quoted user strings). Time predicate is appended via AND.
    """
    ch = await async_client()
    sql = (
        "DELETE FROM tradernick.transfers "
        f"WHERE ({where_extra}) AND time >= '{sql_dt(since)}' AND time < '{sql_dt(until)}'"
    )
    await ch.command(sql)


async def async_client():
    global _async_client_obj
    if _async_client_obj is None:
        _async_client_obj = await clickhouse_connect.get_async_client(
            host=config.CLICKHOUSE_HOST,
            port=config.CLICKHOUSE_PORT,
            username=config.CLICKHOUSE_USER,
            password=config.CLICKHOUSE_PASSWORD,
            database=config.CLICKHOUSE_DB,
        )
    return _async_client_obj


OHLCV_COLUMNS = [
    "token", "time",
    "open", "close", "high", "low",
    "volume", "buyer_taker_volume", "seller_taker_volume",
    "trade_count",
]

RAW_TRADE_COLUMNS = ["token", "time", "amount", "price", "buy", "id"]

OPEN_INTEREST_COLUMNS = ["token", "time", "open_interest", "open_interest_value"]
LONG_SHORT_COLUMNS = [
    "token", "time",
    "top_trader_count_ratio", "top_trader_vol_ratio",
    "long_short_count_ratio", "taker_long_short_vol_ratio",
]
FUNDING_RATE_COLUMNS = ["token", "time", "rate"]

TRANSFER_COLUMNS = [
    "kind", "chain", "token", "time", "block_number",
    "sender", "receiver", "amount", "tx_id", "log_index", "value_usd",
]


def _to_naive_utc(t):
    if hasattr(t, "to_pydatetime"):
        t = t.to_pydatetime()
    if getattr(t, "tzinfo", None) is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def ohlcv_df_to_rows(df):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            r["token"],
            _to_naive_utc(r["window"]),
            float(r["open"]),
            float(r["close"]),
            float(r["high"]),
            float(r["low"]),
            float(r["volume"]),
            float(r["buyer_taker_volume"]),
            float(r["seller_taker_volume"]),
            int(r["trade_count"]),
        ])
    return rows


def open_interest_df_to_rows(df: pl.DataFrame, token: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            token,
            _to_naive_utc(r["time"]),
            float(r["open_interest"]),
            float(r["open_interest_value"]),
        ])
    return rows


def long_short_df_to_rows(df: pl.DataFrame, token: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            token,
            _to_naive_utc(r["time"]),
            float(r["top_trader_count_ratio"]),
            float(r["top_trader_vol_ratio"]),
            float(r["long_short_count_ratio"]),
            float(r["taker_long_short_vol_ratio"]),
        ])
    return rows


def funding_rate_df_to_rows(df: pl.DataFrame, token: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            token,
            _to_naive_utc(r["time"]),
            float(r["rate"]),
        ])
    return rows


def transfers_df_to_rows(df: pl.DataFrame, *, kind: str, chain: str, token_override: str | None = None):
    rows = []
    cols = set(df.columns)
    has_tx_id = "tx_id" in cols
    has_log_index = "log_index" in cols
    has_value_usd = "value_usd" in cols
    has_token_col = "token" in cols
    for r in df.iter_rows(named=True):
        token = token_override or (r["token"] if has_token_col else "")
        v_usd = None
        if has_value_usd:
            raw = r.get("value_usd")
            if raw is not None and raw != "":
                try:
                    v_usd = float(raw)
                except (TypeError, ValueError):
                    v_usd = None
        rows.append([
            kind,
            chain,
            str(token).upper(),
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["sender"]) if r.get("sender") is not None else "",
            str(r["receiver"]) if r.get("receiver") is not None else "",
            float(r["amount"]),
            str(r["tx_id"]) if has_tx_id and r.get("tx_id") is not None else "",
            int(r["log_index"]) if has_log_index and r.get("log_index") is not None else 0,
            v_usd,
        ])
    return rows


def transfers_df_for_bulk_insert(df: pl.DataFrame, *, kind: str, chain: str, token_override: str | None = None):
    cols = set(df.columns)
    if "tx_id" not in cols:
        df = df.with_columns(pl.lit("").alias("tx_id"))
    if "log_index" not in cols:
        df = df.with_columns(pl.lit(0).alias("log_index"))
    if "value_usd" not in cols:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("value_usd"))

    df = df.with_columns([
        pl.lit(kind).alias("kind"),
        pl.lit(chain).alias("chain"),
    ])
    if token_override is not None:
        df = df.with_columns(pl.lit(str(token_override).upper()).alias("token"))

    df = df.with_columns([
        pl.col("time").dt.convert_time_zone("UTC").dt.replace_time_zone(None).cast(pl.Datetime("ms")),
        pl.col("block_number").cast(pl.UInt64),
        pl.col("sender").cast(pl.Utf8).fill_null(""),
        pl.col("receiver").cast(pl.Utf8).fill_null(""),
        pl.col("amount").cast(pl.Float64),
        pl.col("tx_id").cast(pl.Utf8).fill_null(""),
        pl.col("log_index").cast(pl.UInt32),
        pl.col("value_usd").cast(pl.Float64, strict=False),
    ])

    return df.select(TRANSFER_COLUMNS).to_pandas()


def raw_trades_df_for_insert(df: pl.DataFrame, token: str):
    return (
        df
        .with_columns([
            pl.col("time").dt.convert_time_zone("UTC").dt.replace_time_zone(None).cast(pl.Datetime("ms")),
            pl.col("id").cast(pl.UInt64),
            pl.col("buy").cast(pl.Boolean),
        ])
        .with_columns(pl.lit(token).alias("token"))
        .select(RAW_TRADE_COLUMNS)
        .to_pandas()
    )
