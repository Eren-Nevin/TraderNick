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


# ---------------------------------------------------------------------------
# Lido liquid-staking events
# ---------------------------------------------------------------------------
# Mainnet flow:
#   - deposit: user sends ETH, gets STETH minted. minted_amount + minted_token.
#   - withdrawal_request: user burns STETH, receives a queued request_id.
#   - withdrawal_claimed: queued request_id is finalised; user gets ETH back.
#
# L2 flow:
#   - l2_deposit: user bridges STETH (mainnet) → WSTETH (L2); on the L2 side
#     this looks like a mint into the bridge-deployed token.
#   - l2_withdrawal_request: user burns WSTETH on L2 to reverse the bridge.
#
# All five events share the same first 5 columns (chain, time, block_number,
# tx_id, log_index). Per-event columns differ but the ingest pattern is the
# same: row-by-row transform → insert.

LIDO_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "sender", "referral",
    "minted_amount", "minted_token",
    "value_usd",
]
LIDO_WITHDRAWAL_REQUESTS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "request_id", "requestor", "owner",
    "burned_amount", "burned_token",
    "value_usd",
]
LIDO_WITHDRAWAL_CLAIMS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "request_id", "receiver", "owner",
    "withdraw_amount", "withdraw_token", "burned_token",
    "value_usd",
]
LIDO_L2_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "sender", "receiver",
    "minted_amount", "minted_token",
    "value_usd",
]
LIDO_L2_WITHDRAWAL_REQUESTS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "sender", "receiver",
    "burned_amount", "burned_token",
    "value_usd",
]


def _lido_value_usd(r):
    raw = r.get("value_usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _lido_base_cols(r, *, chain: str) -> list:
    """The 5 head columns shared by every Lido event table."""
    return [
        chain,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def lido_deposits_df_to_rows(df: pl.DataFrame, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_lido_base_cols(r, chain=chain) + [
            str(r["sender"]) if r.get("sender") else "",
            str(r["referral"]) if r.get("referral") else "",
            float(r["minted_amount"]) if r.get("minted_amount") is not None else 0.0,
            str(r["minted_token"]) if r.get("minted_token") else "",
            _lido_value_usd(r),
        ])
    return rows


def lido_withdrawal_requests_df_to_rows(df: pl.DataFrame, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_lido_base_cols(r, chain=chain) + [
            int(r["request_id"]) if r.get("request_id") is not None else 0,
            str(r["requestor"]) if r.get("requestor") else "",
            str(r["owner"]) if r.get("owner") else "",
            float(r["burned_amount"]) if r.get("burned_amount") is not None else 0.0,
            str(r["burned_token"]) if r.get("burned_token") else "",
            _lido_value_usd(r),
        ])
    return rows


def lido_withdrawal_claims_df_to_rows(df: pl.DataFrame, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_lido_base_cols(r, chain=chain) + [
            int(r["request_id"]) if r.get("request_id") is not None else 0,
            str(r["receiver"]) if r.get("receiver") else "",
            str(r["owner"]) if r.get("owner") else "",
            float(r["withdraw_amount"]) if r.get("withdraw_amount") is not None else 0.0,
            str(r["withdraw_token"]) if r.get("withdraw_token") else "",
            str(r["burned_token"]) if r.get("burned_token") else "",
            _lido_value_usd(r),
        ])
    return rows


def lido_l2_deposits_df_to_rows(df: pl.DataFrame, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_lido_base_cols(r, chain=chain) + [
            str(r["sender"]) if r.get("sender") else "",
            str(r["receiver"]) if r.get("receiver") else "",
            float(r["minted_amount"]) if r.get("minted_amount") is not None else 0.0,
            str(r["minted_token"]) if r.get("minted_token") else "",
            _lido_value_usd(r),
        ])
    return rows


def lido_l2_withdrawal_requests_df_to_rows(df: pl.DataFrame, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_lido_base_cols(r, chain=chain) + [
            str(r["sender"]) if r.get("sender") else "",
            str(r["receiver"]) if r.get("receiver") else "",
            float(r["burned_amount"]) if r.get("burned_amount") is not None else 0.0,
            str(r["burned_token"]) if r.get("burned_token") else "",
            _lido_value_usd(r),
        ])
    return rows


# event key → (DeFiStream method name on lido client, target CH table,
# column list, row transform). Same dispatch pattern as AAVE_EVENTS /
# UNISWAP_EVENTS. The L1 events live on ETH only; the L2 ones live on the
# 9 L2 chains supported by DeFiStream's Lido coverage.
LIDO_EVENTS = {
    "deposit":              ("deposits",              "tradernick.lido_deposits",              LIDO_DEPOSITS_COLUMNS,              lido_deposits_df_to_rows),
    "withdrawal_request":   ("withdrawal_requests",   "tradernick.lido_withdrawal_requests",   LIDO_WITHDRAWAL_REQUESTS_COLUMNS,   lido_withdrawal_requests_df_to_rows),
    # DeFiStream Python client uses the slightly-odd `withdrawals_claimed`
    # (plural on the first word); the wire event name stays singular.
    "withdrawal_claimed":   ("withdrawals_claimed",   "tradernick.lido_withdrawal_claims",     LIDO_WITHDRAWAL_CLAIMS_COLUMNS,     lido_withdrawal_claims_df_to_rows),
    "l2_deposit":           ("l2_deposits",           "tradernick.lido_l2_deposits",           LIDO_L2_DEPOSITS_COLUMNS,           lido_l2_deposits_df_to_rows),
    "l2_withdrawal_request":("l2_withdrawal_requests","tradernick.lido_l2_withdrawal_requests",LIDO_L2_WITHDRAWAL_REQUESTS_COLUMNS,lido_l2_withdrawal_requests_df_to_rows),
}


# ---------------------------------------------------------------------------
# AAVE v2 events (legacy mainnet + Polygon)
# ---------------------------------------------------------------------------
# Same 6-event taxonomy as V3 but without the eth_market axis (V2 was a
# single pool per chain). Transforms parallel the V3 versions — only
# difference is the dropped eth_market column.

AAVE_V2_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "on_behalf_of", "referral_code", "value_usd",
]
AAVE_V2_WITHDRAWALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "recipient", "value_usd",
]
AAVE_V2_BORROWS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "on_behalf_of",
    "interest_rate_mode", "borrow_rate", "referral_code", "value_usd",
]
AAVE_V2_REPAYS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "repayer", "value_usd",
]
AAVE_V2_FLASHLOANS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "target", "premium", "referral_code", "value_usd",
]
AAVE_V2_LIQUIDATIONS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "owner", "liquidator",
    "debt_token", "debt_to_cover",
    "collateral_token", "liquidated_collateral_amount",
    "receive_a_token", "value_usd",
]


def aave_v2_deposits_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
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


def aave_v2_withdrawals_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
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


def aave_v2_borrows_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
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


def aave_v2_repays_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["repayer"]) if r.get("repayer") else "",
            _aave_value_usd(r),
        ])
    return rows


def aave_v2_flashloans_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]),
            str(r["target"]) if r.get("target") else "",
            float(r["premium"]) if r.get("premium") is not None else 0.0,
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def aave_v2_liquidations_df_to_rows(df, *, chain: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain,
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


AAVE_V2_EVENTS = {
    "deposit":     ("deposits",     "tradernick.aave_v2_deposits",     AAVE_V2_DEPOSITS_COLUMNS,     aave_v2_deposits_df_to_rows),
    "withdraw":    ("withdrawals",  "tradernick.aave_v2_withdrawals",  AAVE_V2_WITHDRAWALS_COLUMNS,  aave_v2_withdrawals_df_to_rows),
    "borrow":      ("borrows",      "tradernick.aave_v2_borrows",      AAVE_V2_BORROWS_COLUMNS,      aave_v2_borrows_df_to_rows),
    "repay":       ("repays",       "tradernick.aave_v2_repays",       AAVE_V2_REPAYS_COLUMNS,       aave_v2_repays_df_to_rows),
    "flashloan":   ("flashloans",   "tradernick.aave_v2_flashloans",   AAVE_V2_FLASHLOANS_COLUMNS,   aave_v2_flashloans_df_to_rows),
    "liquidation": ("liquidations", "tradernick.aave_v2_liquidations", AAVE_V2_LIQUIDATIONS_COLUMNS, aave_v2_liquidations_df_to_rows),
}


# ---------------------------------------------------------------------------
# Uniswap V2 events
# ---------------------------------------------------------------------------
# V2 has 3 events (no collect — fees auto-compound). No fee_tier (single
# 0.30% per pool). Pool identity is (chain, symbol0, symbol1). DeFiStream's
# V2 swap response uses camelCase (tokenSold / amountSold) — normalise here.

UNISWAP_V2_SWAPS_COLUMNS = [
    "chain", "symbol0", "symbol1", "time", "block_number", "tx_id", "log_index",
    "pair_address", "swapper", "recipient",
    "token_sold", "token_bought", "amount_sold", "amount_bought",
    "value_usd",
]
UNISWAP_V2_DEPOSITS_COLUMNS = [
    "chain", "symbol0", "symbol1", "time", "block_number", "tx_id", "log_index",
    "pair_address", "sender",
    "amount0", "amount1",
    "value_usd",
]
UNISWAP_V2_WITHDRAWALS_COLUMNS = [
    "chain", "symbol0", "symbol1", "time", "block_number", "tx_id", "log_index",
    "pair_address", "owner", "recipient",
    "amount0", "amount1",
    "value_usd",
]


def _uni_v2_value_usd(r):
    raw = r.get("value_usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def uniswap_v2_swaps_df_to_rows(df, *, chain: str, symbol0: str, symbol1: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, symbol0, symbol1,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pair_address"]) if r.get("pair_address") else "",
            str(r["swapper"]) if r.get("swapper") else "",
            str(r["recipient"]) if r.get("recipient") else "",
            # camelCase on the wire — normalise.
            str(r["tokenSold"]) if r.get("tokenSold") else "",
            str(r["tokenBought"]) if r.get("tokenBought") else "",
            float(r["amountSold"]) if r.get("amountSold") is not None else 0.0,
            float(r["amountBought"]) if r.get("amountBought") is not None else 0.0,
            _uni_v2_value_usd(r),
        ])
    return rows


def uniswap_v2_deposits_df_to_rows(df, *, chain: str, symbol0: str, symbol1: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, symbol0, symbol1,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pair_address"]) if r.get("pair_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            _uni_v2_value_usd(r),
        ])
    return rows


def uniswap_v2_withdrawals_df_to_rows(df, *, chain: str, symbol0: str, symbol1: str):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            chain, symbol0, symbol1,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pair_address"]) if r.get("pair_address") else "",
            str(r["owner"]) if r.get("owner") else "",
            # Defistream withdrawal exposes `to` (not `recipient`) on the wire.
            str(r["to"]) if r.get("to") else "",
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            _uni_v2_value_usd(r),
        ])
    return rows


UNISWAP_V2_EVENTS = {
    "swap":     ("swaps",       "tradernick.uniswap_v2_swaps",       UNISWAP_V2_SWAPS_COLUMNS,       uniswap_v2_swaps_df_to_rows),
    "deposit":  ("deposits",    "tradernick.uniswap_v2_deposits",    UNISWAP_V2_DEPOSITS_COLUMNS,    uniswap_v2_deposits_df_to_rows),
    "withdraw": ("withdrawals", "tradernick.uniswap_v2_withdrawals", UNISWAP_V2_WITHDRAWALS_COLUMNS, uniswap_v2_withdrawals_df_to_rows),
}


# ---------------------------------------------------------------------------
# Uniswap V4 events
# ---------------------------------------------------------------------------
# Pool identity: (chain, sym0, sym1, fee, tick_spacing, hooks). LP events
# DON'T expose amount0/amount1 — V4 emits only liquidity_delta (signed for
# withdraw). swap rows look V3-shaped (sqrt_based_price, liquidity, tick)
# but use `sender` (no recipient — V4 is callback-based).
# Wire fields: tokenSold / amountSold / etc. (camelCase) — normalised here.

UNISWAP_V4_SWAPS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee", "tick_spacing", "hooks",
    "time", "block_number", "tx_id", "log_index",
    "pool_id", "sender",
    "token_sold", "token_bought", "amount_sold", "amount_bought",
    "sqrt_based_price", "liquidity", "tick",
    "value_usd",
]
UNISWAP_V4_DEPOSITS_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee", "tick_spacing", "hooks",
    "time", "block_number", "tx_id", "log_index",
    "pool_id", "sender",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "liquidity_delta", "value_usd",
]
UNISWAP_V4_WITHDRAWALS_COLUMNS = UNISWAP_V4_DEPOSITS_COLUMNS  # same shape
UNISWAP_V4_INITIALIZES_COLUMNS = [
    "chain", "symbol0", "symbol1", "fee", "tick_spacing", "hooks",
    "time", "block_number", "tx_id", "log_index",
    "pool_id", "currency0_addr", "currency1_addr",
    "initial_sqrt_x96", "initial_tick",
]


def _v_usd(r):
    raw = r.get("value_usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _v4_pool_head(r, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    """Shared 6-tuple pool identity + 4 row-locator columns."""
    return [
        chain, symbol0, symbol1, int(fee), int(tick_spacing), hooks,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def uniswap_v4_swaps_df_to_rows(df, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_v4_pool_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  fee=fee, tick_spacing=tick_spacing, hooks=hooks) + [
            str(r["pool_id"]) if r.get("pool_id") else "",
            str(r["sender"]) if r.get("sender") else "",
            str(r["tokenSold"]) if r.get("tokenSold") else "",
            str(r["tokenBought"]) if r.get("tokenBought") else "",
            float(r["amountSold"]) if r.get("amountSold") is not None else 0.0,
            float(r["amountBought"]) if r.get("amountBought") is not None else 0.0,
            float(r["sqrt_based_price"]) if r.get("sqrt_based_price") is not None else 0.0,
            float(r["liquidity"]) if r.get("liquidity") is not None else 0.0,
            int(r["tick"]) if r.get("tick") is not None else 0,
            _v_usd(r),
        ])
    return rows


def _v4_lp_df_to_rows(df, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    """Shared deposit/withdraw transform — V4 LP events only emit
    liquidity_delta (positive on deposit, negative on withdraw)."""
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_v4_pool_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  fee=fee, tick_spacing=tick_spacing, hooks=hooks) + [
            str(r["pool_id"]) if r.get("pool_id") else "",
            str(r["sender"]) if r.get("sender") else "",
            int(r["tick_lower"]) if r.get("tick_lower") is not None else 0,
            int(r["tick_upper"]) if r.get("tick_upper") is not None else 0,
            float(r["price_lower"]) if r.get("price_lower") is not None else 0.0,
            float(r["price_upper"]) if r.get("price_upper") is not None else 0.0,
            float(r["liquidity_delta"]) if r.get("liquidity_delta") is not None else 0.0,
            _v_usd(r),
        ])
    return rows


def uniswap_v4_deposits_df_to_rows(df, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    return _v4_lp_df_to_rows(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                              fee=fee, tick_spacing=tick_spacing, hooks=hooks)


def uniswap_v4_withdrawals_df_to_rows(df, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    return _v4_lp_df_to_rows(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                              fee=fee, tick_spacing=tick_spacing, hooks=hooks)


def uniswap_v4_initializes_df_to_rows(df, *, chain, symbol0, symbol1, fee, tick_spacing, hooks):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_v4_pool_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  fee=fee, tick_spacing=tick_spacing, hooks=hooks) + [
            str(r["pool_id"]) if r.get("pool_id") else "",
            str(r["currency0"]) if r.get("currency0") else "",
            str(r["currency1"]) if r.get("currency1") else "",
            float(r["sqrt_price_x96"]) if r.get("sqrt_price_x96") is not None else 0.0,
            int(r["tick"]) if r.get("tick") is not None else 0,
        ])
    return rows


UNISWAP_V4_EVENTS = {
    "swap":       ("swaps",        "tradernick.uniswap_v4_swaps",        UNISWAP_V4_SWAPS_COLUMNS,        uniswap_v4_swaps_df_to_rows),
    "deposit":    ("deposits",     "tradernick.uniswap_v4_deposits",     UNISWAP_V4_DEPOSITS_COLUMNS,     uniswap_v4_deposits_df_to_rows),
    "withdraw":   ("withdrawals",  "tradernick.uniswap_v4_withdrawals",  UNISWAP_V4_WITHDRAWALS_COLUMNS,  uniswap_v4_withdrawals_df_to_rows),
    "initialize": ("initializes",  "tradernick.uniswap_v4_initializes",  UNISWAP_V4_INITIALIZES_COLUMNS,  uniswap_v4_initializes_df_to_rows),
}


# ---------------------------------------------------------------------------
# Aerodrome concentrated-pool events (BASE only, V1 scope)
# ---------------------------------------------------------------------------
# Same shape as Uniswap V3 minus fee_tier (Aero uses tick_spacing alone to
# distinguish CL pool tiers). swap rows carry sqrt_based_price/liquidity/tick;
# LP events expose amount0/amount1 directly. Wire is camelCase for swap.

AERO_CL_SWAPS_COLUMNS = [
    "chain", "symbol0", "symbol1", "tick_spacing",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "swapper", "recipient",
    "token_sold", "token_bought", "amount_sold", "amount_bought",
    "sqrt_based_price", "liquidity", "tick",
    "value_usd",
]
AERO_CL_DEPOSITS_COLUMNS = [
    "chain", "symbol0", "symbol1", "tick_spacing",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "sender", "owner",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]
AERO_CL_WITHDRAWALS_COLUMNS = [
    "chain", "symbol0", "symbol1", "tick_spacing",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "owner",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]
AERO_CL_COLLECTS_COLUMNS = [
    "chain", "symbol0", "symbol1", "tick_spacing",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "owner", "recipient",
    "amount0", "amount1",
    "tick_lower", "tick_upper", "price_lower", "price_upper",
    "value_usd",
]


def _aero_cl_head(r, *, chain, symbol0, symbol1, tick_spacing):
    return [
        chain, symbol0, symbol1, int(tick_spacing),
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def aero_cl_swaps_df_to_rows(df, *, chain, symbol0, symbol1, tick_spacing):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_aero_cl_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, tick_spacing=tick_spacing) + [
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
            _v_usd(r),
        ])
    return rows


def _aero_cl_lp_df_to_rows(df, *, chain, symbol0, symbol1, tick_spacing,
                          include_sender, include_recipient):
    rows = []
    for r in df.iter_rows(named=True):
        head = _aero_cl_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, tick_spacing=tick_spacing)
        head.append(str(r["pool_address"]) if r.get("pool_address") else "")
        if include_sender:
            head.append(str(r["sender"]) if r.get("sender") else "")
        head.append(str(r["owner"]) if r.get("owner") else "")
        if include_recipient:
            head.append(str(r["recipient"]) if r.get("recipient") else "")
        head.extend([
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            int(r["tick_lower"]) if r.get("tick_lower") is not None else 0,
            int(r["tick_upper"]) if r.get("tick_upper") is not None else 0,
            float(r["price_lower"]) if r.get("price_lower") is not None else 0.0,
            float(r["price_upper"]) if r.get("price_upper") is not None else 0.0,
            _v_usd(r),
        ])
        rows.append(head)
    return rows


def aero_cl_deposits_df_to_rows(df, *, chain, symbol0, symbol1, tick_spacing):
    return _aero_cl_lp_df_to_rows(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  tick_spacing=tick_spacing,
                                  include_sender=True, include_recipient=False)


def aero_cl_withdrawals_df_to_rows(df, *, chain, symbol0, symbol1, tick_spacing):
    return _aero_cl_lp_df_to_rows(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  tick_spacing=tick_spacing,
                                  include_sender=False, include_recipient=False)


def aero_cl_collects_df_to_rows(df, *, chain, symbol0, symbol1, tick_spacing):
    return _aero_cl_lp_df_to_rows(df, chain=chain, symbol0=symbol0, symbol1=symbol1,
                                  tick_spacing=tick_spacing,
                                  include_sender=False, include_recipient=True)


AERO_CL_EVENTS = {
    "swap":     ("swaps",       "tradernick.aero_concentrated_swaps",       AERO_CL_SWAPS_COLUMNS,       aero_cl_swaps_df_to_rows),
    "deposit":  ("deposits",    "tradernick.aero_concentrated_deposits",    AERO_CL_DEPOSITS_COLUMNS,    aero_cl_deposits_df_to_rows),
    "withdraw": ("withdrawals", "tradernick.aero_concentrated_withdrawals", AERO_CL_WITHDRAWALS_COLUMNS, aero_cl_withdrawals_df_to_rows),
    "collect":  ("collects",    "tradernick.aero_concentrated_collects",    AERO_CL_COLLECTS_COLUMNS,    aero_cl_collects_df_to_rows),
}


# ---------------------------------------------------------------------------
# Aerodrome basic-pool events (Solidly v1-style, BASE only)
# ---------------------------------------------------------------------------
# Pool identity: (chain=BASE, sym0, sym1, stable). 4 events; gauge-style
# claims (basic-only; concentrated uses collect instead). Wire is the same
# camelCase mix as the concentrated swap event.

AERO_BASIC_SWAPS_COLUMNS = [
    "chain", "symbol0", "symbol1", "stable",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "swapper", "recipient",
    "token_sold", "token_bought", "amount_sold", "amount_bought",
    "value_usd",
]
AERO_BASIC_DEPOSITS_COLUMNS = [
    "chain", "symbol0", "symbol1", "stable",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "sender",
    "amount0", "amount1",
    "value_usd",
]
AERO_BASIC_WITHDRAWALS_COLUMNS = [
    "chain", "symbol0", "symbol1", "stable",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "owner", "recipient",
    "amount0", "amount1",
    "value_usd",
]
AERO_BASIC_CLAIMS_COLUMNS = [
    "chain", "symbol0", "symbol1", "stable",
    "time", "block_number", "tx_id", "log_index",
    "pool_address", "sender", "recipient",
    "amount0", "amount1",
    "value_usd",
]


def _aero_basic_head(r, *, chain, symbol0, symbol1, stable):
    return [
        chain, symbol0, symbol1, 1 if stable else 0,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def aero_basic_swaps_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["swapper"]) if r.get("swapper") else "",
            str(r["recipient"]) if r.get("recipient") else "",
            str(r["tokenSold"]) if r.get("tokenSold") else "",
            str(r["tokenBought"]) if r.get("tokenBought") else "",
            float(r["amountSold"]) if r.get("amountSold") is not None else 0.0,
            float(r["amountBought"]) if r.get("amountBought") is not None else 0.0,
            _v_usd(r),
        ])
    return rows


def aero_basic_deposits_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            _v_usd(r),
        ])
    return rows


def aero_basic_withdrawals_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["owner"]) if r.get("owner") else "",
            # Mirror Uniswap V2 withdraw: the wire field for the receiving
            # address is `to` (not `recipient`).
            str(r["to"]) if r.get("to") else "",
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            _v_usd(r),
        ])
    return rows


def aero_basic_claims_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            str(r["recipient"]) if r.get("recipient") else "",
            float(r["amount0"]) if r.get("amount0") is not None else 0.0,
            float(r["amount1"]) if r.get("amount1") is not None else 0.0,
            _v_usd(r),
        ])
    return rows


AERO_BASIC_EVENTS = {
    "swap":     ("swaps",       "tradernick.aero_basic_swaps",       AERO_BASIC_SWAPS_COLUMNS,       aero_basic_swaps_df_to_rows),
    "deposit":  ("deposits",    "tradernick.aero_basic_deposits",    AERO_BASIC_DEPOSITS_COLUMNS,    aero_basic_deposits_df_to_rows),
    "withdraw": ("withdrawals", "tradernick.aero_basic_withdrawals", AERO_BASIC_WITHDRAWALS_COLUMNS, aero_basic_withdrawals_df_to_rows),
    "claim":    ("claims",      "tradernick.aero_basic_claims",      AERO_BASIC_CLAIMS_COLUMNS,      aero_basic_claims_df_to_rows),
}
