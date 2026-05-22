from datetime import timezone

import clickhouse_connect
import polars as pl

import config

_async_client_obj = None


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
