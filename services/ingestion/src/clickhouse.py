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

    Also purges the same time window from `tradernick.exchange_flow_minute` — the
    SummingMergeTree rollup downstream of mv_exchange_flow doesn't know about
    deletes upstream, so leaving it intact while the source is force-purged
    would leave stale (and, after re-ingest, compounded) sums that drift the
    chart by orders of magnitude. The rollup's column set is direction /
    exchange / chain / token / time, so we can scope to the same time window
    without knowing the caller's chain/token predicate (slightly wider purge,
    but mv_exchange_flow is the only writer and the periodic rebuild covers
    any over-purge instantly).
    """
    ch = await async_client()
    src_sql = (
        "DELETE FROM tradernick.transfers "
        f"WHERE ({where_extra}) AND time >= '{sql_dt(since)}' AND time < '{sql_dt(until)}'"
    )
    rollup_sql = (
        "DELETE FROM tradernick.exchange_flow_minute "
        f"WHERE time >= '{sql_dt(since)}' AND time < '{sql_dt(until)}'"
    )
    await ch.command(src_sql)
    await ch.command(rollup_sql)


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


# NOTE: defistream 2.22 added multi-token support to the four perp-side
# binance endpoints (OI / L-S / funding / raw_trades) plus OHLCV. A single
# .token(*symbols) call now returns rows for every symbol, each carrying
# its own `token` column. Transforms read `r["token"]` instead of taking
# the token as a parameter so one DataFrame can produce mixed-token rows.

def open_interest_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
            _to_naive_utc(r["time"]),
            float(r["open_interest"]),
            float(r["open_interest_value"]),
        ])
    return rows


def long_short_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
            _to_naive_utc(r["time"]),
            float(r["top_trader_count_ratio"]),
            float(r["top_trader_vol_ratio"]),
            float(r["long_short_count_ratio"]),
            float(r["taker_long_short_vol_ratio"]),
        ])
    return rows


def funding_rate_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
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


def raw_trades_df_for_insert(df: pl.DataFrame):
    # 2.22 multi-token responses already include a `token` column per row,
    # so we drop the previous pl.lit(token) override and trust the source.
    return (
        df
        .with_columns([
            pl.col("time").dt.convert_time_zone("UTC").dt.replace_time_zone(None).cast(pl.Datetime("ms")),
            pl.col("id").cast(pl.UInt64),
            pl.col("buy").cast(pl.Boolean),
            pl.col("token").cast(pl.Utf8),
        ])
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


def _aligned_amounts(r, *, symbol0: str, symbol1: str) -> tuple[float, float]:
    """Align wire (amount0, amount1) to our canonical (symbol0, symbol1).

    Pool contracts emit amount0/amount1 in token-address order, which only
    sometimes matches alphabetic order. DeFiStream returns the wire values
    as-is with a `token0`/`token1` label pair so the consumer can re-align.
    We compare wire's `token0` against our canonical `symbol0`; if it
    matches, store wire's amount0/1 directly. If wire's `token0` matches
    our `symbol1`, swap so the row's amount0 column always corresponds to
    the row's symbol0 column.

    Examples:
      USDC/WETH on ETH:  USDC < WETH addr → wire token0=USDC → no swap
      USDC/WETH on BASE: WETH < USDC addr → wire token0=WETH → SWAP
    """
    wire_t0 = str(r.get("token0") or "").upper()
    a0 = float(r["amount0"]) if r.get("amount0") is not None else 0.0
    a1 = float(r["amount1"]) if r.get("amount1") is not None else 0.0
    if wire_t0 == symbol1.upper() and wire_t0 != symbol0.upper():
        return a1, a0  # swap
    return a0, a1


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
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        row.extend([
            a0, a1,
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
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        rows.append([
            chain, symbol0, symbol1,
            _to_naive_utc(r["time"]),
            int(r["block_number"]),
            str(r["tx_id"]) if r.get("tx_id") else "",
            int(r["log_index"]) if r.get("log_index") is not None else 0,
            str(r["pair_address"]) if r.get("pair_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            a0, a1,
            _uni_v2_value_usd(r),
        ])
    return rows


def uniswap_v2_withdrawals_df_to_rows(df, *, chain: str, symbol0: str, symbol1: str):
    rows = []
    for r in df.iter_rows(named=True):
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
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
            a0, a1,
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
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        head.extend([
            a0, a1,
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
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            a0, a1,
            _v_usd(r),
        ])
    return rows


def aero_basic_withdrawals_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["owner"]) if r.get("owner") else "",
            # Mirror Uniswap V2 withdraw: the wire field for the receiving
            # address is `to` (not `recipient`).
            str(r["to"]) if r.get("to") else "",
            a0, a1,
            _v_usd(r),
        ])
    return rows


def aero_basic_claims_df_to_rows(df, *, chain, symbol0, symbol1, stable):
    rows = []
    for r in df.iter_rows(named=True):
        a0, a1 = _aligned_amounts(r, symbol0=symbol0, symbol1=symbol1)
        rows.append(_aero_basic_head(r, chain=chain, symbol0=symbol0, symbol1=symbol1, stable=stable) + [
            str(r["pool_address"]) if r.get("pool_address") else "",
            str(r["sender"]) if r.get("sender") else "",
            str(r["recipient"]) if r.get("recipient") else "",
            a0, a1,
            _v_usd(r),
        ])
    return rows


AERO_BASIC_EVENTS = {
    "swap":     ("swaps",       "tradernick.aero_basic_swaps",       AERO_BASIC_SWAPS_COLUMNS,       aero_basic_swaps_df_to_rows),
    "deposit":  ("deposits",    "tradernick.aero_basic_deposits",    AERO_BASIC_DEPOSITS_COLUMNS,    aero_basic_deposits_df_to_rows),
    "withdraw": ("withdrawals", "tradernick.aero_basic_withdrawals", AERO_BASIC_WITHDRAWALS_COLUMNS, aero_basic_withdrawals_df_to_rows),
    "claim":    ("claims",      "tradernick.aero_basic_claims",      AERO_BASIC_CLAIMS_COLUMNS,      aero_basic_claims_df_to_rows),
}


# ---------------------------------------------------------------------------
# AAVE v4 events (ETH only)
# ---------------------------------------------------------------------------
# V4 introduces hub-and-spoke: spoke contract + reserve_id replace V3's
# eth_market axis. Each event also carries `shares` (aToken shares
# minted/burned). No flashloan event in V4. 5 events total.

AAVE_V4_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "spoke", "reserve_id", "caller", "user", "token", "amount", "shares",
    "value_usd",
]
AAVE_V4_WITHDRAWALS_COLUMNS = AAVE_V4_DEPOSITS_COLUMNS  # same shape
AAVE_V4_BORROWS_COLUMNS     = AAVE_V4_DEPOSITS_COLUMNS  # same shape
AAVE_V4_REPAYS_COLUMNS      = AAVE_V4_DEPOSITS_COLUMNS  # same shape
AAVE_V4_LIQUIDATIONS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "spoke", "user", "liquidator",
    "collateral_token", "collateral_amount",
    "debt_token", "debt_amount",
    "value_usd",
]


def _v4_head(r, *, chain):
    return [
        chain,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def _v4_lend_df_to_rows(df, *, chain):
    """Shared transform for deposit/withdraw/borrow/repay — they all have
    the same wire shape (token-keyed lend operations)."""
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_v4_head(r, chain=chain) + [
            str(r["spoke"]) if r.get("spoke") else "",
            int(r["reserve_id"]) if r.get("reserve_id") is not None else 0,
            str(r["caller"]) if r.get("caller") else "",
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            float(r["shares"]) if r.get("shares") is not None else 0.0,
            _aave_value_usd(r),
        ])
    return rows


def aave_v4_deposits_df_to_rows(df, *, chain):     return _v4_lend_df_to_rows(df, chain=chain)
def aave_v4_withdrawals_df_to_rows(df, *, chain):  return _v4_lend_df_to_rows(df, chain=chain)
def aave_v4_borrows_df_to_rows(df, *, chain):      return _v4_lend_df_to_rows(df, chain=chain)
def aave_v4_repays_df_to_rows(df, *, chain):       return _v4_lend_df_to_rows(df, chain=chain)


def aave_v4_liquidations_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_v4_head(r, chain=chain) + [
            str(r["spoke"]) if r.get("spoke") else "",
            str(r["user"]) if r.get("user") else "",
            str(r["liquidator"]) if r.get("liquidator") else "",
            str(r["collateral_token"]) if r.get("collateral_token") else "",
            float(r["collateral_amount"]) if r.get("collateral_amount") is not None else 0.0,
            str(r["debt_token"]) if r.get("debt_token") else "",
            float(r["debt_amount"]) if r.get("debt_amount") is not None else 0.0,
            _aave_value_usd(r),
        ])
    return rows


AAVE_V4_EVENTS = {
    "deposit":     ("deposits",     "tradernick.aave_v4_deposits",     AAVE_V4_DEPOSITS_COLUMNS,     aave_v4_deposits_df_to_rows),
    "withdraw":    ("withdrawals",  "tradernick.aave_v4_withdrawals",  AAVE_V4_WITHDRAWALS_COLUMNS,  aave_v4_withdrawals_df_to_rows),
    "borrow":      ("borrows",      "tradernick.aave_v4_borrows",      AAVE_V4_BORROWS_COLUMNS,      aave_v4_borrows_df_to_rows),
    "repay":       ("repays",       "tradernick.aave_v4_repays",       AAVE_V4_REPAYS_COLUMNS,       aave_v4_repays_df_to_rows),
    "liquidation": ("liquidations", "tradernick.aave_v4_liquidations", AAVE_V4_LIQUIDATIONS_COLUMNS, aave_v4_liquidations_df_to_rows),
}


# ---------------------------------------------------------------------------
# Morpho events (ETH + BASE)
# ---------------------------------------------------------------------------
# Morpho Blue's isolated-market architecture: each market_id is a 32-byte
# hash identifying a unique (loan, collateral, oracle, IRM, lltv) tuple.
# Supply/withdraw/borrow/repay carry assets + shares; collateral events
# have no shares. Liquidations have repaid + seized + bad_debt fields.
# Flashloans are skipped — DeFiStream's decode worker is broken for the
# event today.

MORPHO_SUPPLIES_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market_id", "caller", "on_behalf", "token", "assets", "shares", "value_usd",
]
MORPHO_REPAYS_COLUMNS = MORPHO_SUPPLIES_COLUMNS  # identical shape
MORPHO_WITHDRAWALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market_id", "caller", "on_behalf", "receiver",
    "token", "assets", "shares", "value_usd",
]
MORPHO_BORROWS_COLUMNS = MORPHO_WITHDRAWALS_COLUMNS  # same shape
MORPHO_SUPPLY_COLLATERALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market_id", "caller", "on_behalf", "token", "assets", "value_usd",
]
MORPHO_WITHDRAW_COLLATERALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market_id", "caller", "on_behalf", "receiver",
    "token", "assets", "value_usd",
]
MORPHO_LIQUIDATIONS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market_id", "caller", "borrower",
    "loan_token", "collateral_token",
    "repaid_assets", "repaid_shares", "seized_assets",
    "bad_debt_assets", "bad_debt_shares",
    "value_usd",
]


def _morpho_head(r, *, chain):
    return [
        chain,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def _morpho_lend_df_to_rows(df, *, chain, include_receiver, include_shares):
    """Shared transform for the 4 main lending events (supply/withdraw/
    borrow/repay). The two collateral events use a similar shape with
    no shares; the special liquidation event has its own transform."""
    rows = []
    for r in df.iter_rows(named=True):
        row = _morpho_head(r, chain=chain) + [
            str(r["market_id"]) if r.get("market_id") else "",
            str(r["caller"]) if r.get("caller") else "",
            str(r["on_behalf"]) if r.get("on_behalf") else "",
        ]
        if include_receiver:
            row.append(str(r["receiver"]) if r.get("receiver") else "")
        row.append(str(r["token"]) if r.get("token") else "")
        row.append(float(r["assets"]) if r.get("assets") is not None else 0.0)
        if include_shares:
            row.append(float(r["shares"]) if r.get("shares") is not None else 0.0)
        row.append(_aave_value_usd(r))
        rows.append(row)
    return rows


def morpho_supplies_df_to_rows(df, *, chain):     return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=False, include_shares=True)
def morpho_withdrawals_df_to_rows(df, *, chain):  return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=True,  include_shares=True)
def morpho_borrows_df_to_rows(df, *, chain):      return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=True,  include_shares=True)
def morpho_repays_df_to_rows(df, *, chain):       return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=False, include_shares=True)
def morpho_supply_collaterals_df_to_rows(df, *, chain):  return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=False, include_shares=False)
def morpho_withdraw_collaterals_df_to_rows(df, *, chain): return _morpho_lend_df_to_rows(df, chain=chain, include_receiver=True, include_shares=False)


def morpho_liquidations_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_morpho_head(r, chain=chain) + [
            str(r["market_id"]) if r.get("market_id") else "",
            str(r["caller"]) if r.get("caller") else "",
            str(r["borrower"]) if r.get("borrower") else "",
            str(r["loan_token"]) if r.get("loan_token") else "",
            str(r["collateral_token"]) if r.get("collateral_token") else "",
            float(r["repaid_assets"]) if r.get("repaid_assets") is not None else 0.0,
            float(r["repaid_shares"]) if r.get("repaid_shares") is not None else 0.0,
            float(r["seized_assets"]) if r.get("seized_assets") is not None else 0.0,
            float(r["bad_debt_assets"]) if r.get("bad_debt_assets") is not None else 0.0,
            float(r["bad_debt_shares"]) if r.get("bad_debt_shares") is not None else 0.0,
            _aave_value_usd(r),
        ])
    return rows


MORPHO_EVENTS = {
    "supply":             ("supplies",             "tradernick.morpho_supplies",             MORPHO_SUPPLIES_COLUMNS,             morpho_supplies_df_to_rows),
    "withdraw":           ("withdrawals",          "tradernick.morpho_withdrawals",          MORPHO_WITHDRAWALS_COLUMNS,          morpho_withdrawals_df_to_rows),
    "borrow":             ("borrows",              "tradernick.morpho_borrows",              MORPHO_BORROWS_COLUMNS,              morpho_borrows_df_to_rows),
    "repay":              ("repays",               "tradernick.morpho_repays",               MORPHO_REPAYS_COLUMNS,               morpho_repays_df_to_rows),
    "supply_collateral":  ("supply_collaterals",   "tradernick.morpho_supply_collaterals",   MORPHO_SUPPLY_COLLATERALS_COLUMNS,   morpho_supply_collaterals_df_to_rows),
    "withdraw_collateral":("withdraw_collaterals", "tradernick.morpho_withdraw_collaterals", MORPHO_WITHDRAW_COLLATERALS_COLUMNS, morpho_withdraw_collaterals_df_to_rows),
    "liquidation":        ("liquidations",         "tradernick.morpho_liquidations",         MORPHO_LIQUIDATIONS_COLUMNS,         morpho_liquidations_df_to_rows),
}


# ---------------------------------------------------------------------------
# Spark events (ETH only)
# ---------------------------------------------------------------------------
# Spark is an AAVE V3 fork by Sky/Maker — same 6-event taxonomy and shapes
# as V3 minus the eth_market axis (single market per chain).

SPARK_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "on_behalf_of", "referral_code", "value_usd",
]
SPARK_WITHDRAWALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "recipient", "value_usd",
]
SPARK_BORROWS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "on_behalf_of",
    "interest_rate_mode", "borrow_rate", "referral_code", "value_usd",
]
SPARK_REPAYS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "repayer", "use_a_tokens", "value_usd",
]
SPARK_FLASHLOANS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "user", "token", "amount", "target",
    "interest_rate_mode", "premium", "referral_code", "value_usd",
]
SPARK_LIQUIDATIONS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "owner", "liquidator",
    "debt_token", "debt_to_cover",
    "collateral_token", "liquidated_collateral_amount",
    "receive_a_token", "value_usd",
]


def _spark_head(r, *, chain):
    return [
        chain,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def spark_deposits_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            str(r["on_behalf_of"]) if r.get("on_behalf_of") else "",
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def spark_withdrawals_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            str(r["recipient"]) if r.get("recipient") else "",
            _aave_value_usd(r),
        ])
    return rows


def spark_borrows_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            str(r["on_behalf_of"]) if r.get("on_behalf_of") else "",
            int(r["interest_rate_mode"]) if r.get("interest_rate_mode") is not None else 0,
            float(r["borrow_rate"]) if r.get("borrow_rate") is not None else 0.0,
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def spark_repays_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            str(r["repayer"]) if r.get("repayer") else "",
            _aave_bool(r, "use_a_tokens"),
            _aave_value_usd(r),
        ])
    return rows


def spark_flashloans_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
            str(r["user"]) if r.get("user") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            str(r["target"]) if r.get("target") else "",
            int(r["interest_rate_mode"]) if r.get("interest_rate_mode") is not None else 0,
            float(r["premium"]) if r.get("premium") is not None else 0.0,
            int(r["referral_code"]) if r.get("referral_code") is not None else 0,
            _aave_value_usd(r),
        ])
    return rows


def spark_liquidations_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_spark_head(r, chain=chain) + [
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


SPARK_EVENTS = {
    "deposit":     ("deposits",     "tradernick.spark_deposits",     SPARK_DEPOSITS_COLUMNS,     spark_deposits_df_to_rows),
    "withdraw":    ("withdrawals",  "tradernick.spark_withdrawals",  SPARK_WITHDRAWALS_COLUMNS,  spark_withdrawals_df_to_rows),
    "borrow":      ("borrows",      "tradernick.spark_borrows",      SPARK_BORROWS_COLUMNS,      spark_borrows_df_to_rows),
    "repay":       ("repays",       "tradernick.spark_repays",       SPARK_REPAYS_COLUMNS,       spark_repays_df_to_rows),
    "flashloan":   ("flashloans",   "tradernick.spark_flashloans",   SPARK_FLASHLOANS_COLUMNS,   spark_flashloans_df_to_rows),
    "liquidation": ("liquidations", "tradernick.spark_liquidations", SPARK_LIQUIDATIONS_COLUMNS, spark_liquidations_df_to_rows),
}


# ---------------------------------------------------------------------------
# GMX V2 transforms (defistream 2.14.0)
# ---------------------------------------------------------------------------
# GMX V2's per-event responses share a common "head" — (chain, time,
# block_number, tx_id, log_index, market, market_name). Each event adds its
# own tail of typed fields. We separate the head helper from the per-event
# transforms so a schema change touches one place.

# Position events + liquidations: src_chain_id/src_chain_name added in
# 2.19 via .enrich_src_chain() (filled by order_key join). For ARB-native
# orders src_chain_id=0 and src_chain_name='ARB'. When the join can't find
# the parent order in the 14-day lookback, both default to 0/''.
GMX_POSITION_INCREASES_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "account",
    "collateral_token", "collateral_symbol",
    "size_delta_usd", "collateral_delta_amount", "execution_price",
    "is_long", "order_type", "order_type_name",
    "size_in_usd", "price_impact_usd",
    "order_key", "position_key",
    "src_chain_id", "src_chain_name",
    "value_usd",
]
# position_decreases + liquidations share the same shape since defistream
# 2.16 — both carry base_pnl_usd (realised PnL on the close).
GMX_POSITION_DECREASES_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "account",
    "collateral_token", "collateral_symbol",
    "size_delta_usd", "collateral_delta_amount", "execution_price",
    "is_long", "order_type", "order_type_name",
    "size_in_usd", "price_impact_usd", "base_pnl_usd",
    "order_key", "position_key",
    "src_chain_id", "src_chain_name",
    "value_usd",
]
GMX_LIQUIDATIONS_COLUMNS = GMX_POSITION_DECREASES_COLUMNS
GMX_SWAPS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "receiver", "order_key",
    "token_in", "token_in_symbol", "amount_in",
    "token_out", "token_out_symbol", "amount_out",
    "price_impact_usd", "value_usd",
]
# Deposits, 2.19-enriched: gained realized_* fields (additive — base
# value_usd was never broken here) + deposit_key.
GMX_DEPOSITS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "account",
    "long_token_amount", "short_token_amount",
    "realized_long_token_amount", "realized_short_token_amount",
    "realized_market_tokens",
    "long_symbol", "short_symbol",
    "deposit_key",
    "min_market_tokens",
    "src_chain_id", "src_chain_name",
    "value_usd",
]
# Withdrawals, 2.19-enriched: the base response no longer carries
# realized amounts at all — it only ships the intent (min_*) plus the
# withdrawal_key. With .enrich_realized_amounts() defistream joins
# against the execute event to recover the actual outflow + USD.
# We store the realized values in the existing long_token_amount /
# short_token_amount / value_usd columns so the dashboard query layer
# (which sums long + short) doesn't need to change.
GMX_WITHDRAWALS_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "account",
    "market_token_amount",
    "min_long_token_amount", "min_short_token_amount",
    "long_token_amount", "short_token_amount",  # realized_*, see above
    "long_symbol", "short_symbol",
    "withdrawal_key",
    "src_chain_id", "src_chain_name",
    "value_usd",
]
GMX_FUNDING_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name",
    "collateral_token", "collateral_symbol", "is_long",
    "funding_fee_amount_per_size", "delta",
]
GMX_BORROWING_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "market", "market_name", "is_long",
    "cumulative_borrowing_factor", "delta",
]
GMX_FEES_COLUMNS = [
    "chain", "time", "block_number", "tx_id", "log_index",
    "fee_type", "market", "market_name",
    "collateral_token", "collateral_symbol", "trader", "order_key",
    "fee_receiver_amount", "fee_amount_for_pool", "total_cost_amount",
    "value_usd",
]


def _gmx_head(r, *, chain):
    """Shared 5-field event head: (chain, time, block_number, tx_id, log_index)."""
    return [
        chain,
        _to_naive_utc(r["time"]),
        int(r["block_number"]),
        str(r["tx_id"]) if r.get("tx_id") else "",
        int(r["log_index"]) if r.get("log_index") is not None else 0,
    ]


def _gmx_bool(r, col):
    """GMX returns is_long as a real Python bool (polars), not the
    lowercase-string AAVE shape. Coerce to UInt8 0/1 either way."""
    v = r.get(col)
    if isinstance(v, bool): return 1 if v else 0
    if isinstance(v, str):  return 1 if v.strip().lower() == "true" else 0
    return 0


def _gmx_position_body(r):
    """The shared tail of fields between position_increases /
    position_decreases / liquidations (minus base_pnl_usd which liquidations
    add on top)."""
    return [
        str(r["market"]) if r.get("market") else "",
        str(r["market_name"]) if r.get("market_name") else "",
        str(r["account"]) if r.get("account") else "",
        str(r["collateral_token"]) if r.get("collateral_token") else "",
        str(r["collateral_symbol"]) if r.get("collateral_symbol") else "",
        float(r["size_delta_usd"]) if r.get("size_delta_usd") is not None else 0.0,
        float(r["collateral_delta_amount"]) if r.get("collateral_delta_amount") is not None else 0.0,
        float(r["execution_price"]) if r.get("execution_price") is not None else 0.0,
        _gmx_bool(r, "is_long"),
        int(r["order_type"]) if r.get("order_type") is not None else 0,
        str(r["order_type_name"]) if r.get("order_type_name") else "",
        float(r["size_in_usd"]) if r.get("size_in_usd") is not None else 0.0,
        float(r["price_impact_usd"]) if r.get("price_impact_usd") is not None else 0.0,
    ]


def _gmx_position_tail(r):
    # 2.19: src_chain_id/src_chain_name sit between (order_key, position_key)
    # and value_usd. DefiStream returns src_chain_id=-1 as the sentinel for
    # "no parent order found in the 14-day OrderCreated lookback"; map to
    # 0 since our column is UInt32 (and the resolved-but-ARB-native case
    # is already represented by 0 / "ARB" downstream).
    raw_id = r.get("src_chain_id")
    sid = int(raw_id) if (raw_id is not None and int(raw_id) >= 0) else 0
    return [
        str(r["order_key"]) if r.get("order_key") else "",
        str(r["position_key"]) if r.get("position_key") else "",
        sid,
        str(r["src_chain_name"]) if r.get("src_chain_name") else "",
        _aave_value_usd(r),
    ]


def gmx_position_increases_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + _gmx_position_body(r) + _gmx_position_tail(r))
    return rows


def _gmx_position_close_row(r, *, chain):
    """Shared row builder for position_decreases + liquidations — both now
    carry base_pnl_usd in defistream 2.16. Falls back to 0.0 if a row is
    missing the field (older / partial responses)."""
    return (
        _gmx_head(r, chain=chain) + _gmx_position_body(r)
        + [float(r["base_pnl_usd"]) if r.get("base_pnl_usd") is not None else 0.0]
        + _gmx_position_tail(r)
    )


def gmx_position_decreases_df_to_rows(df, *, chain):
    return [_gmx_position_close_row(r, chain=chain) for r in df.iter_rows(named=True)]


def gmx_liquidations_df_to_rows(df, *, chain):
    return [_gmx_position_close_row(r, chain=chain) for r in df.iter_rows(named=True)]


def gmx_swaps_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            str(r["receiver"]) if r.get("receiver") else "",
            str(r["order_key"]) if r.get("order_key") else "",
            str(r["token_in"]) if r.get("token_in") else "",
            str(r["token_in_symbol"]) if r.get("token_in_symbol") else "",
            float(r["amount_in"]) if r.get("amount_in") is not None else 0.0,
            str(r["token_out"]) if r.get("token_out") else "",
            str(r["token_out_symbol"]) if r.get("token_out_symbol") else "",
            float(r["amount_out"]) if r.get("amount_out") is not None else 0.0,
            float(r["price_impact_usd"]) if r.get("price_impact_usd") is not None else 0.0,
            _aave_value_usd(r),
        ])
    return rows


def _gmx_src_chain(r):
    """Pull (src_chain_id, src_chain_name) from a deposit/withdrawal row.
    Added in defistream 2.16. 2.19's enrich_src_chain join can return -1
    when no parent order is found in the 14-day lookback — map to 0 so
    the value fits our UInt32 column."""
    raw_id = r.get("src_chain_id")
    sid = int(raw_id) if (raw_id is not None and int(raw_id) >= 0) else 0
    return [
        sid,
        str(r["src_chain_name"]) if r.get("src_chain_name") else "",
    ]


def _gmx_f(r, col):
    """Float cast that tolerates missing/None — 2.19's enrichment fields
    may be absent on rows where the enriched join didn't fire (e.g. the
    parent order is older than the 14-day lookback)."""
    v = r.get(col)
    return float(v) if v is not None else 0.0


def gmx_deposits_df_to_rows(df, *, chain):
    """Deposits transform. The base response carries the intent amounts
    (what the user committed from their wallet); .enrich_realized_amounts()
    additionally returns the on-chain executed amounts + minted GM
    receipt. Both are kept — the dashboard sums long + short on the
    intent side since deposits execute as requested."""
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            str(r["account"]) if r.get("account") else "",
            _gmx_f(r, "long_token_amount"),
            _gmx_f(r, "short_token_amount"),
            _gmx_f(r, "realized_long_token_amount"),
            _gmx_f(r, "realized_short_token_amount"),
            _gmx_f(r, "realized_market_tokens"),
            str(r["long_symbol"]) if r.get("long_symbol") else "",
            str(r["short_symbol"]) if r.get("short_symbol") else "",
            str(r["deposit_key"]) if r.get("deposit_key") else "",
            _gmx_f(r, "min_market_tokens"),
        ] + _gmx_src_chain(r) + [
            _aave_value_usd(r),
        ])
    return rows


def gmx_withdrawals_df_to_rows(df, *, chain):
    """Withdrawals transform. In 2.19 the base response no longer carries
    realized amounts — only the intent (min_*) + withdrawal_key. We require
    .enrich_realized_amounts() to recover the actual on-chain outflow
    (and the realized USD), and we store the realized values in the
    long_token_amount / short_token_amount / value_usd columns so the
    dashboard's "sum long + short" query layer doesn't need to change."""
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            str(r["account"]) if r.get("account") else "",
            _gmx_f(r, "market_token_amount"),
            _gmx_f(r, "min_long_token_amount"),
            _gmx_f(r, "min_short_token_amount"),
            # The two columns the dashboard reads: realized_* preferred
            # (correctly decimaled by defistream 2.19's enrich), with a
            # zero fallback for rows where the keeper hasn't yet executed.
            _gmx_f(r, "realized_long_token_amount"),
            _gmx_f(r, "realized_short_token_amount"),
            str(r["long_symbol"]) if r.get("long_symbol") else "",
            str(r["short_symbol"]) if r.get("short_symbol") else "",
            str(r["withdrawal_key"]) if r.get("withdrawal_key") else "",
        ] + _gmx_src_chain(r) + [
            # value_usd in the row carries realized_value_usd (added by
            # enrich_realized_amounts). _aave_value_usd reads r["value_usd"]
            # which defistream maps to the realized field name here.
            float(r["realized_value_usd"]) if r.get("realized_value_usd") is not None else _aave_value_usd(r),
        ])
    return rows


def gmx_funding_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            str(r["collateral_token"]) if r.get("collateral_token") else "",
            str(r["collateral_symbol"]) if r.get("collateral_symbol") else "",
            _gmx_bool(r, "is_long"),
            float(r["funding_fee_amount_per_size"]) if r.get("funding_fee_amount_per_size") is not None else 0.0,
            float(r["delta"]) if r.get("delta") is not None else 0.0,
        ])
    return rows


def gmx_borrowing_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            _gmx_bool(r, "is_long"),
            float(r["cumulative_borrowing_factor"]) if r.get("cumulative_borrowing_factor") is not None else 0.0,
            float(r["delta"]) if r.get("delta") is not None else 0.0,
        ])
    return rows


def gmx_fees_collected_df_to_rows(df, *, chain):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(_gmx_head(r, chain=chain) + [
            str(r["fee_type"]) if r.get("fee_type") else "",
            str(r["market"]) if r.get("market") else "",
            str(r["market_name"]) if r.get("market_name") else "",
            str(r["collateral_token"]) if r.get("collateral_token") else "",
            str(r["collateral_symbol"]) if r.get("collateral_symbol") else "",
            str(r["trader"]) if r.get("trader") else "",
            str(r["order_key"]) if r.get("order_key") else "",
            float(r["fee_receiver_amount"]) if r.get("fee_receiver_amount") is not None else 0.0,
            float(r["fee_amount_for_pool"]) if r.get("fee_amount_for_pool") is not None else 0.0,
            float(r["total_cost_amount"]) if r.get("total_cost_amount") is not None else 0.0,
            _aave_value_usd(r),
        ])
    return rows


# Dispatch dict matching the (method_name, table, columns, transform) shape
# the other protocols use. method_name is the builder accessor on
# ds.evm.gmx_v2 — DeFiStream uses plurals for everything except funding /
# borrowing / fees_collected (which are emitted-as-batch events).
GMX_EVENTS = {
    "position_increase": ("position_increases", "tradernick.gmx_position_increases", GMX_POSITION_INCREASES_COLUMNS, gmx_position_increases_df_to_rows),
    "position_decrease": ("position_decreases", "tradernick.gmx_position_decreases", GMX_POSITION_DECREASES_COLUMNS, gmx_position_decreases_df_to_rows),
    "liquidation":       ("liquidations",       "tradernick.gmx_liquidations",       GMX_LIQUIDATIONS_COLUMNS,       gmx_liquidations_df_to_rows),
    "swap":              ("swaps",              "tradernick.gmx_swaps",              GMX_SWAPS_COLUMNS,        gmx_swaps_df_to_rows),
    "deposit":           ("deposits",           "tradernick.gmx_deposits",           GMX_DEPOSITS_COLUMNS,     gmx_deposits_df_to_rows),
    "withdraw":          ("withdrawals",        "tradernick.gmx_withdrawals",        GMX_WITHDRAWALS_COLUMNS,  gmx_withdrawals_df_to_rows),
    "funding":           ("funding",            "tradernick.gmx_funding",            GMX_FUNDING_COLUMNS,      gmx_funding_df_to_rows),
    "borrowing":         ("borrowing",          "tradernick.gmx_borrowing",          GMX_BORROWING_COLUMNS,    gmx_borrowing_df_to_rows),
    "fees_collected":    ("fees_collected",     "tradernick.gmx_fees_collected",     GMX_FEES_COLUMNS,         gmx_fees_collected_df_to_rows),
}


# ---------------------------------------------------------------------------
# Hyperliquid transforms (defistream ds.exchange.hyperliquid.*)
# ---------------------------------------------------------------------------
# 8 endpoints ingested (Tier 1+2). Every transform reads `token`/`wallet`
# directly from each row since the multi-token API form gives mixed-token
# DataFrames. Most fields map 1:1 from the polars response; the only
# coercions are bool→UInt8 (CH likes int flags) and ensuring strings/floats
# tolerate missing values.

def _hl_bool(v) -> int:
    if isinstance(v, bool): return 1 if v else 0
    if isinstance(v, str):  return 1 if v.strip().lower() == "true" else 0
    return 0


# OHLCV — identical shape to binance_ohlcv_1m. Reuses ohlcv_df_to_rows.
HL_OHLCV_COLUMNS = OHLCV_COLUMNS
hl_ohlcv_df_to_rows = ohlcv_df_to_rows


HL_TRADES_COLUMNS = [
    "token", "time", "price", "amount", "buy", "id",
    "buyer_wallet", "seller_wallet", "block_number",
]


def hl_trades_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
            _to_naive_utc(r["time"]),
            float(r["price"]),
            float(r["amount"]),
            _hl_bool(r.get("buy")),
            int(r["id"]) if r.get("id") is not None else 0,
            str(r["buyer_wallet"]) if r.get("buyer_wallet") else "",
            str(r["seller_wallet"]) if r.get("seller_wallet") else "",
            int(r["block_number"]) if r.get("block_number") is not None else 0,
        ])
    return rows


HL_FILLS_COLUMNS = [
    "token", "time", "block_time", "block_number", "wallet",
    "price", "size", "side", "dir",
    "start_position", "closed_pnl", "fee", "fee_token", "builder_fee",
    "crossed", "tid", "oid", "hash",
]


def hl_fills_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
            _to_naive_utc(r["time"]),
            _to_naive_utc(r["block_time"]),
            int(r["block_number"]) if r.get("block_number") is not None else 0,
            str(r["wallet"]) if r.get("wallet") else "",
            float(r["price"]) if r.get("price") is not None else 0.0,
            float(r["size"]) if r.get("size") is not None else 0.0,
            str(r["side"]) if r.get("side") else "",
            str(r["dir"]) if r.get("dir") else "",
            float(r["start_position"]) if r.get("start_position") is not None else 0.0,
            float(r["closed_pnl"]) if r.get("closed_pnl") is not None else 0.0,
            float(r["fee"]) if r.get("fee") is not None else 0.0,
            str(r["fee_token"]) if r.get("fee_token") else "",
            float(r["builder_fee"]) if r.get("builder_fee") is not None else 0.0,
            _hl_bool(r.get("crossed")),
            int(r["tid"]) if r.get("tid") is not None else 0,
            int(r["oid"]) if r.get("oid") is not None else 0,
            str(r["hash"]) if r.get("hash") else "",
        ])
    return rows


HL_FUNDING_COLUMNS = [
    "token", "time", "wallet", "rate", "amount", "position_amount", "block_number",
]


def hl_funding_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            str(r["token"]),
            _to_naive_utc(r["time"]),
            str(r["wallet"]) if r.get("wallet") else "",
            float(r["rate"]) if r.get("rate") is not None else 0.0,
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            float(r["position_amount"]) if r.get("position_amount") is not None else 0.0,
            int(r["block_number"]) if r.get("block_number") is not None else 0,
        ])
    return rows


HL_POSITION_HISTORY_COLUMNS = [
    "time", "wallet", "token", "side", "amount", "avg_entry", "opened_at",
    "mark_price", "size", "unrealized_pnl", "funding", "fee", "exact_avg_price",
]


def hl_position_history_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            _to_naive_utc(r["time"]),
            str(r["wallet"]) if r.get("wallet") else "",
            str(r["token"]) if r.get("token") else "",
            str(r["side"]) if r.get("side") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            float(r["avg_entry"]) if r.get("avg_entry") is not None else 0.0,
            _to_naive_utc(r["opened_at"]) if r.get("opened_at") is not None else _to_naive_utc(r["time"]),
            float(r["mark_price"]) if r.get("mark_price") is not None else 0.0,
            float(r["size"]) if r.get("size") is not None else 0.0,
            float(r["unrealized_pnl"]) if r.get("unrealized_pnl") is not None else 0.0,
            float(r["funding"]) if r.get("funding") is not None else 0.0,
            float(r["fee"]) if r.get("fee") is not None else 0.0,
            _hl_bool(r.get("exact_avg_price")),
        ])
    return rows


HL_TRADE_HISTORY_COLUMNS = [
    "time", "wallet", "token",
    "pnl", "fees", "net_pnl",
    "volume", "buy_volume", "sell_volume", "trade_count",
]


def hl_trade_history_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            _to_naive_utc(r["time"]),
            str(r["wallet"]) if r.get("wallet") else "",
            str(r["token"]) if r.get("token") else "",
            float(r["pnl"]) if r.get("pnl") is not None else 0.0,
            float(r["fees"]) if r.get("fees") is not None else 0.0,
            float(r["net_pnl"]) if r.get("net_pnl") is not None else 0.0,
            float(r["volume"]) if r.get("volume") is not None else 0.0,
            float(r["buy_volume"]) if r.get("buy_volume") is not None else 0.0,
            float(r["sell_volume"]) if r.get("sell_volume") is not None else 0.0,
            int(r["trade_count"]) if r.get("trade_count") is not None else 0,
        ])
    return rows


HL_TRANSFERS_COLUMNS = [
    "time", "direction", "wallet", "amount", "is_finalized", "block_number",
]


def hl_transfers_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            _to_naive_utc(r["time"]),
            str(r["direction"]) if r.get("direction") else "",
            str(r["wallet"]) if r.get("wallet") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            _hl_bool(r.get("is_finalized")),
            int(r["block_number"]) if r.get("block_number") is not None else 0,
        ])
    return rows


HL_VAULTS_COLUMNS = [
    "time", "vault", "wallet", "action", "amount", "commission", "fee", "block_number",
]


def hl_vaults_df_to_rows(df: pl.DataFrame):
    rows = []
    for r in df.iter_rows(named=True):
        rows.append([
            _to_naive_utc(r["time"]),
            str(r["vault"]) if r.get("vault") else "",
            str(r["wallet"]) if r.get("wallet") else "",
            str(r["action"]) if r.get("action") else "",
            float(r["amount"]) if r.get("amount") is not None else 0.0,
            float(r["commission"]) if r.get("commission") is not None else 0.0,
            float(r["fee"]) if r.get("fee") is not None else 0.0,
            int(r["block_number"]) if r.get("block_number") is not None else 0,
        ])
    return rows


# Dispatch dict: event-key -> (builder_method_name, table, columns, transform).
# builder_method_name is the accessor on ds.exchange.hyperliquid.
HL_EVENTS = {
    "ohlcv":            ("ohlcv",            "tradernick.hl_ohlcv_1m",         HL_OHLCV_COLUMNS,            hl_ohlcv_df_to_rows),
    "trades":           ("trades",           "tradernick.hl_trades",           HL_TRADES_COLUMNS,           hl_trades_df_to_rows),
    "fills":            ("fills",            "tradernick.hl_fills",            HL_FILLS_COLUMNS,            hl_fills_df_to_rows),
    "funding":          ("funding",          "tradernick.hl_funding",          HL_FUNDING_COLUMNS,          hl_funding_df_to_rows),
    "position_history": ("position_history", "tradernick.hl_position_history", HL_POSITION_HISTORY_COLUMNS, hl_position_history_df_to_rows),
    "trade_history":    ("trade_history",    "tradernick.hl_trade_history",    HL_TRADE_HISTORY_COLUMNS,    hl_trade_history_df_to_rows),
    "transfers":        ("transfers",        "tradernick.hl_transfers",        HL_TRANSFERS_COLUMNS,        hl_transfers_df_to_rows),
    "vaults":           ("vaults",           "tradernick.hl_vaults",           HL_VAULTS_COLUMNS,           hl_vaults_df_to_rows),
}
