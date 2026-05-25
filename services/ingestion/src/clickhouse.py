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


# --- AAVE v3 events ---------------------------------------------------------
#
# Six event types share a (chain, eth_market, time, block_number, tx_id,
# log_index, value_usd) prefix and then diverge. Each has its own column list
# below; the transform helpers `aave_<event>_df_to_rows` read the polars
# DataFrame DeFiStream returns and emit rows in that exact column order so
# `ch.insert(table, rows, column_names=AAVE_<EVENT>_COLUMNS)` works directly.
#
# `eth_market` is the user-supplied `eth_market_type` (Core/Prime/EtherFi)
# for ETH queries; empty string otherwise.

AAVE_DEPOSITS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount",
    "on_behalf_of", "referral_code",
    "value_usd",
]
AAVE_WITHDRAWALS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount",
    "recipient",
    "value_usd",
]
AAVE_BORROWS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount",
    "on_behalf_of", "interest_rate_mode", "borrow_rate", "referral_code",
    "value_usd",
]
AAVE_REPAYS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount",
    "repayer", "use_a_tokens",
    "value_usd",
]
AAVE_FLASHLOANS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount",
    "target", "interest_rate_mode", "premium", "referral_code",
    "value_usd",
]
AAVE_LIQUIDATIONS_COLUMNS = [
    "chain", "eth_market", "time", "block_number", "tx_id", "log_index",
    "owner", "liquidator",
    "debt_token", "debt_to_cover",
    "collateral_token", "liquidated_collateral_amount",
    "receive_a_token",
    "value_usd",
]


def _aave_value_usd(r):
    """Parse value_usd from a DeFiStream row, treating empty / missing as NULL."""
    raw = r.get("value_usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _aave_bool(r, col: str) -> int:
    """DeFiStream emits booleans as lowercase strings ('true' / 'false'). Map
    to UInt8 0/1 for CH storage."""
    v = r.get(col)
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, str):
        return 1 if v.strip().lower() == "true" else 0
    return 0


def aave_deposits_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["on_behalf_of"]) if r.get("on_behalf_of") else "",
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def aave_withdrawals_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["recipient"]) if r.get("recipient") else "",
            _aave_value_usd(r),
        ])
    return rows


def aave_borrows_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["on_behalf_of"]) if r.get("on_behalf_of") else "",
            int(r["interest_rate_mode"]) if r.get("interest_rate_mode") is not None else 0,
            float(r["borrow_rate"]) if r.get("borrow_rate") is not None else 0.0,
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def aave_repays_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["repayer"]) if r.get("repayer") else "",
            _aave_bool(r, "use_a_tokens"),
            _aave_value_usd(r),
        ])
    return rows


def aave_flashloans_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["target"]) if r.get("target") else "",
            int(r["interest_rate_mode"]) if r.get("interest_rate_mode") is not None else 0,
            float(r["premium"]) if r.get("premium") is not None else 0.0,
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def aave_liquidations_df_to_rows(df: pl.DataFrame, *, chain: str, eth_market: str = ""):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, eth_market,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["owner"]) if r.get("owner") else "",
            str(r["liquidator"]) if r.get("liquidator") else "",
            str(r["debt_token"]) if r.get("debt_token") else "",
            float(r["debt_to_cover"]) if r.get("debt_to_cover") is not None else 0.0,
            str(r["collateral_token"]) if r.get("collateral_token") else "",
            float(r["liquidated_collateral_amount"]) if r.get("liquidated_collateral_amount") is not None else 0.0,
            _aave_bool(r, "receive_a_token"),
            _aave_value_usd(r),
        ])
    return rows


# Mapping consumed by the AAVE polling group + backfill driver: event key →
# (DeFiStream method name on aave_v3 client, target CH table, column list,
# row transform). Keeps the per-event branches identical apart from these.
AAVE_EVENTS = {
    "deposit":     ("deposits",    "tradernick.aave_deposits",     AAVE_DEPOSITS_COLUMNS,     aave_deposits_df_to_rows),
    "withdraw":    ("withdrawals", "tradernick.aave_withdrawals",  AAVE_WITHDRAWALS_COLUMNS,  aave_withdrawals_df_to_rows),
    "borrow":      ("borrows",     "tradernick.aave_borrows",      AAVE_BORROWS_COLUMNS,      aave_borrows_df_to_rows),
    "repay":       ("repays",      "tradernick.aave_repays",       AAVE_REPAYS_COLUMNS,       aave_repays_df_to_rows),
    "flashloan":   ("flashloans",  "tradernick.aave_flashloans",   AAVE_FLASHLOANS_COLUMNS,   aave_flashloans_df_to_rows),
    "liquidation": ("liquidations","tradernick.aave_liquidations", AAVE_LIQUIDATIONS_COLUMNS, aave_liquidations_df_to_rows),
}


# --- Uniswap V3 events ------------------------------------------------------
#
# Four event types (swap / deposit / withdraw / collect) each scoped to a
# specific pool, identified by (chain, symbol0, symbol1, fee_tier). The pool
# columns are emitted from the caller side because DeFiStream's response
# doesn't echo them in a typed form — only as part of the canonical
# `token0` / `token1` strings on deposit/withdraw/collect (and not at all
# on swap).

UNISWAP_SWAPS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee_tier",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "swapper", "recipient",
    "token_sold", "token_bought", "amount_sold", "amount_bought",
    "sqrt_based_price", "liquidity", "tick",
    "value_usd",
]
UNISWAP_DEPOSITS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee_tier",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "sender", "owner",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]
UNISWAP_WITHDRAWALS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee_tier",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "owner",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]
UNISWAP_COLLECTS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee_tier",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "owner", "recipient",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]


def _uniswap_value_usd(r):
    raw = r.get("value_usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def uniswap_swaps_df_to_rows(
    df: pl.DataFrame, *, chain: str, symbol0: str, symbol1: str, fee_tier: int
):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, symbol0, symbol1, int(fee_tier),
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["swapper"]) if r.get("swapper") else "",
            str(r["recipient"]) if r.get("recipient") else "",
            str(r["tokenSold"]) if r.get("tokenSold") else "",
            str(r["tokenBought"]) if r.get("tokenBought") else "",
            float(r["amountSold"]) if r.get("amountSold") is not None else 0.0,
            float(r["amountBought"]) if r.get("amountBought") is not None else 0.0,
            float(r["sqrt_based_price"]) if r.get("sqrt_based_price") is not None else 0.0,
            float(r["liquidity"]) if r.get("liquidity") is not None else 0.0,
            int(r["tick"]) if r.get("tick") is not None else 0,
            _uniswap_value_usd(r),
        ])
    return rows


def _uniswap_lp_event_df_to_rows(
    df: pl.DataFrame, *, chain: str, symbol0: str, symbol1: str, fee_tier: int,
    include_sender: bool, include_recipient: bool,
):
    """Shared transform for deposit / withdraw / collect — they all carry
    the same pool-position row shape (owner + amount0/amount1 + tick range
    + value_usd) but the actor field differs:
      deposit:  has `sender` + `owner`
      withdraw: has `owner` only
      collect:  has `owner` + `recipient`
    """
    rows = []
    for r in df.iter_rows(named=True):
        row = [
            chain, symbol0, symbol1, int(fee_tier),
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pool_address"]) if r.get("pool_address") else "",
        ]
        if include_sender:
            row.append(str(r["sender"]) if r.get("sender") else "")
        row.append(str(r["owner"]) if r.get("owner") else "")
        if include_recipient:
            row.append(str(r["recipient"]) if r.get("recipient") else "")
        row.extend([
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            int(r["tick_lower"]) if r.get("tick_lower") is not None else 0,
            int(r["tick_upper"]) if r.get("tick_upper") is not None else 0,
            float(r["price_lower"]) if r.get("price_lower") is not None else 0.0,
            float(r["price_upper"]) if r.get("price_upper") is not None else 0.0,
            _uniswap_value_usd(r),
        ])
        rows.append(row)
    return rows


def uniswap_deposits_df_to_rows(df: pl.DataFrame, *, chain, symbol0, symbol1, fee_tier):
    return _uniswap_lp_event_df_to_rows(
        df, chain=chain, symbol0=symbol0, symbol1=symbol1, fee_tier=fee_tier,
        include_sender=True, include_recipient=False,
    )


def uniswap_withdrawals_df_to_rows(df: pl.DataFrame, *, chain, symbol0, symbol1, fee_tier):
    return _uniswap_lp_event_df_to_rows(
        df, chain=chain, symbol0=symbol0, symbol1=symbol1, fee_tier=fee_tier,
        include_sender=False, include_recipient=False,
    )


def uniswap_collects_df_to_rows(df: pl.DataFrame, *, chain, symbol0, symbol1, fee_tier):
    return _uniswap_lp_event_df_to_rows(
        df, chain=chain, symbol0=symbol0, symbol1=symbol1, fee_tier=fee_tier,
        include_sender=False, include_recipient=True,
    )


# event key → (DeFiStream method name on uniswap_v3 client, target CH table,
# column list, row transform). Same dispatch pattern as AAVE_EVENTS.
UNISWAP_EVENTS = {
    "swap":     ("swaps",       "tradernick.uniswap_swaps",       UNISWAP_SWAPS_COLUMNS,       uniswap_swaps_df_to_rows),
    "deposit":  ("deposits",    "tradernick.uniswap_deposits",    UNISWAP_DEPOSITS_COLUMNS,    uniswap_deposits_df_to_rows),
    "withdraw": ("withdrawals", "tradernick.uniswap_withdrawals", UNISWAP_WITHDRAWALS_COLUMNS, uniswap_withdrawals_df_to_rows),
    "collect":  ("collects",    "tradernick.uniswap_collects",    UNISWAP_COLLECTS_COLUMNS,    uniswap_collects_df_to_rows),
}
