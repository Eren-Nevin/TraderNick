"""
TraderNick data_provider — Horatio-compatible read API backed by ClickHouse.

Phase 1: vertical slice covering binance.ohlcv, binance.funding_rate,
evm.aave.{deposit,withdraw,...}, and evm.erc20_transfers. Body shape +
parquet response format match Horatio so the tradernick_data_provider
client is wire-compatible.
"""

from __future__ import annotations

import io
import logging
import os
import re

import polars as pl
from dotenv import load_dotenv
from sanic import Sanic, Request, response

from . import proxies as proxies_mod
from . import snapshots as snapshots_mod
from . import sql as sql_b
from . import wallets as wallets_mod
from .ch import query_polars

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

app = Sanic('data_provider')
app.config.FALLBACK_ERROR_FORMAT = 'json'
app.config.RESPONSE_TIMEOUT = 3600
app.config.REQUEST_MAX_SIZE = 10 * 1024 * 1024 * 1024
app.config.REQUEST_TIMEOUT = 3600


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.before_server_start
async def setup(app_: Sanic):
    snapshots_dir = os.environ.get('SNAPSHOTS_DIR', '/data/snapshots')
    os.makedirs(snapshots_dir, exist_ok=True)
    app_.ctx.snapshots_dir = snapshots_dir
    app_.ctx.ch_host = os.environ.get('CLICKHOUSE_HOST', 'clickhouse')
    app_.ctx.ch_db = os.environ.get('CLICKHOUSE_DB', 'tradernick')
    log.info(
        'data_provider ready — snapshots=%s clickhouse=%s/%s',
        snapshots_dir, app_.ctx.ch_host, app_.ctx.ch_db,
    )


# Register snapshot routes (`/snapshots/list|load|save|save_multi|delete|scan`),
# wallets CRUD, and the cache/jobs proxies. Importing each module's `register`
# rather than decorating in this file keeps app.py focused on the read routes
# and prevents the Sanic blueprint count from drifting silently as we add more.
snapshots_mod.register(app)
wallets_mod.register(app)
proxies_mod.register(app)


# ---------------------------------------------------------------------------
# Response + body helpers (match Horatio's wire contract)
# ---------------------------------------------------------------------------

def _parquet_response(df: pl.DataFrame, filename: str):
    buf = io.BytesIO()
    # Polars infers the parquet schema from the DF; pyarrow on the client
    # side reads it via pq.read_table → pa.Table.
    df.write_parquet(buf)
    return response.raw(
        buf.getvalue(),
        content_type='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


def _require(body: dict, *fields: str):
    missing = [f for f in fields if f not in body or body[f] is None]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


_SAFE_KEY_RE = re.compile(r'[^a-zA-Z0-9._-]')


def _safe_key(key: str) -> str:
    safe = _SAFE_KEY_RE.sub('_', key)
    if not safe or safe in ('.', '..'):
        raise ValueError(f"Invalid snapshot key: {key!r}")
    return safe


async def _maybe_save_or_return(df: pl.DataFrame, body: dict, filename: str):
    """If `save_key` is set, write parquet to the snapshots dir and return
    `{"saved": True, "key": ...}`. Otherwise stream parquet bytes back.
    Matches Horatio's two-mode response."""
    save_key = body.get('save_key')
    if save_key:
        safe = _safe_key(save_key)
        path = os.path.join(app.ctx.snapshots_dir, f'{safe}.parquet')
        df.write_parquet(path)
        return response.json({'saved': True, 'key': safe})
    return _parquet_response(df, filename)


# ---------------------------------------------------------------------------
# Empty-frame templates — used to keep response schemas stable when CH
# returns zero rows (e.g. unsupported network). Mirrors Horatio's per-
# protocol empty schemas so polars reads back the right column types.
# ---------------------------------------------------------------------------

"""Empty-frame templates.

Two-tier: per-route populated shape (full Horatio column set) and the
narrower legacy `_EMPTY_*` templates used for unsupported-network calls.
A populated 0-row response carries the same schema as a 1-row response
so the client's polars/pandas decoder sees consistent columns either
way."""

# Binance — full populated shapes
_EMPTY_OHLCV_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8,
    'open': pl.Float64, 'close': pl.Float64,
    'high': pl.Float64, 'low': pl.Float64, 'volume': pl.Float64,
    'buyer_taker_volume': pl.Float64, 'seller_taker_volume': pl.Float64,
    'trade_count': pl.Int64,
})
_EMPTY_FUNDING_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8, 'rate': pl.Float64,
})
_EMPTY_BOOK_DEPTH_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8,
    'percentage': pl.Int64, 'depth': pl.Float64, 'value': pl.Float64,
})
_EMPTY_OPEN_INTEREST_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8,
    'open_interest': pl.Float64, 'open_interest_value': pl.Float64,
})
_EMPTY_LSR_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8,
    'top_trader_count_ratio': pl.Float64, 'top_trader_vol_ratio': pl.Float64,
    'long_short_count_ratio': pl.Float64, 'taker_long_short_vol_ratio': pl.Float64,
})
_EMPTY_RAW_TRADES_FULL = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'token': pl.Utf8,
    'amount': pl.Float64, 'price': pl.Float64, 'buy': pl.Boolean,
})

# Transfers — same shape for erc20/native/tron/btc
_EMPTY_TRANSFER_FULL = pl.DataFrame(schema={
    'block_number': pl.Int64, 'token': pl.Utf8,
    'sender': pl.Utf8, 'receiver': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})

# AAVE — per-event populated shapes
_EMPTY_AAVE_BY_EVENT = {
    'deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
        'amount': pl.Float64, 'on_behalf_of': pl.Utf8,
        'referral_code': pl.Int64, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw': pl.DataFrame(schema={
        'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
        'amount': pl.Float64, 'recipient': pl.Utf8,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'borrow': pl.DataFrame(schema={
        'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
        'amount': pl.Float64, 'on_behalf_of': pl.Utf8,
        'interest_rate_mode': pl.Int64, 'borrow_rate': pl.Float64,
        'referral_code': pl.Int64, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'repay': pl.DataFrame(schema={
        'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
        'amount': pl.Float64, 'repayer': pl.Utf8, 'use_a_tokens': pl.Boolean,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'flashloan': pl.DataFrame(schema={
        'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
        'amount': pl.Float64, 'target': pl.Utf8,
        'interest_rate_mode': pl.Int64, 'premium': pl.Float64,
        'referral_code': pl.Int64, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'liquidation': pl.DataFrame(schema={
        'block_number': pl.Int64, 'owner': pl.Utf8, 'liquidator': pl.Utf8,
        'debt_token': pl.Utf8, 'collateral_token': pl.Utf8,
        'debt_to_cover': pl.Float64,
        'liquidated_collateral_amount': pl.Float64,
        'receive_a_token': pl.Boolean, 'time': pl.Datetime('ms', 'UTC'),
    }),
}

# Uniswap V3 — swap has camelCase columns; LP events use snake_case.
_EMPTY_UNISWAP_BY_EVENT = {
    'swap': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'swapper': pl.Utf8, 'recipient': pl.Utf8,
        'tokenSold': pl.Utf8, 'tokenBought': pl.Utf8,
        'amountSold': pl.Float64, 'amountBought': pl.Float64,
        'sqrt_based_price': pl.Float64, 'liquidity': pl.Float64,
        'tick': pl.Int32, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'sender': pl.Utf8, 'owner': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8, 'owner': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'collect': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'owner': pl.Utf8, 'recipient': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
}

# Lido — per-event populated shapes
_EMPTY_LIDO_BY_EVENT = {
    'deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'sender': pl.Utf8, 'referral': pl.Utf8,
        'minted_amount': pl.Float64, 'minted_token': pl.Utf8,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdrawal_request': pl.DataFrame(schema={
        'block_number': pl.Int64, 'request_id': pl.Int64,
        'requestor': pl.Utf8, 'owner': pl.Utf8,
        'burned_amount': pl.Float64, 'burned_token': pl.Utf8,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdrawal_claimed': pl.DataFrame(schema={
        'block_number': pl.Int64, 'request_id': pl.Int64,
        'receiver': pl.Utf8, 'owner': pl.Utf8,
        'withdraw_amount': pl.Float64, 'withdraw_token': pl.Utf8,
        'burned_token': pl.Utf8, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'l2_deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'sender': pl.Utf8, 'receiver': pl.Utf8,
        'minted_amount': pl.Float64, 'minted_token': pl.Utf8,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'l2_withdrawal_request': pl.DataFrame(schema={
        'block_number': pl.Int64, 'sender': pl.Utf8, 'receiver': pl.Utf8,
        'burned_amount': pl.Float64, 'burned_token': pl.Utf8,
        'time': pl.Datetime('ms', 'UTC'),
    }),
}
# Stader and Threshold dropped — TN doesn't ingest those upstreams and
# the empty stubs were giving callers a false-positive impression that
# the namespace existed. Re-add if/when TN ingestion picks them up.
_EMPTY_HL_OHLCV = pl.DataFrame(schema={
    'window': pl.Datetime('us', 'UTC'), 'token': pl.Utf8,
    'open': pl.Float64, 'close': pl.Float64,
    'high': pl.Float64, 'low': pl.Float64, 'volume': pl.Float64,
    'buyer_taker_volume': pl.Float64, 'seller_taker_volume': pl.Float64,
    'trade_count': pl.Int64,
})
_EMPTY_HL_TRADES = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'token': pl.Utf8,
    'price': pl.Float64, 'amount': pl.Float64, 'buy': pl.Boolean,
    'id': pl.Int64, 'buyer_wallet': pl.Utf8, 'seller_wallet': pl.Utf8,
    'block_number': pl.Int64,
})
_EMPTY_HL_FILLS = pl.DataFrame(schema={
    'block_number': pl.Int64, 'block_time': pl.Datetime('us', 'UTC'),
    'time': pl.Datetime('us', 'UTC'),
    'wallet': pl.Utf8, 'token': pl.Utf8, 'price': pl.Float64, 'size': pl.Float64,
    'side': pl.Utf8, 'dir': pl.Utf8, 'start_position': pl.Float64, 'closed_pnl': pl.Float64,
    'fee': pl.Float64, 'fee_token': pl.Utf8, 'builder_fee': pl.Float64,
    'crossed': pl.Boolean, 'tid': pl.Int64, 'oid': pl.Int64, 'hash': pl.Utf8,
})
# Default fills projection drops HL_FILLS_EXTRA_COLS (opt in via extra_cols).
_EMPTY_HL_FILLS_CORE = _EMPTY_HL_FILLS.drop(list(sql_b.HL_FILLS_EXTRA_COLS))
_EMPTY_HL_FUNDING = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'token': pl.Utf8, 'wallet': pl.Utf8,
    'rate': pl.Float64, 'amount': pl.Float64, 'position_amount': pl.Float64,
    'block_number': pl.Int64,
})
_EMPTY_HL_TRANSFERS = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'wallet': pl.Utf8, 'direction': pl.Utf8,
    'amount': pl.Float64, 'is_finalized': pl.Boolean, 'block_number': pl.Int64,
})
_EMPTY_HL_VAULTS = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'vault': pl.Utf8, 'wallet': pl.Utf8,
    'action': pl.Utf8, 'amount': pl.Float64, 'commission': pl.Float64,
    'fee': pl.Float64, 'block_number': pl.Int64,
})
_EMPTY_REALIZED_PERF = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'wallet': pl.Utf8, 'token': pl.Utf8,
    'pnl': pl.Float64, 'fees': pl.Float64, 'net_pnl': pl.Float64, 'funding': pl.Float64,
    'volume': pl.Float64, 'buy_volume': pl.Float64, 'sell_volume': pl.Float64,
    'trade_count': pl.Int64,
})
# aggregate mode drops the per-wallet column (rows are per token+window).
_EMPTY_REALIZED_PERF_AGG = _EMPTY_REALIZED_PERF.drop('wallet')
_EMPTY_HL_POSITIONS = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'wallet': pl.Utf8, 'token': pl.Utf8,
    'side': pl.Utf8, 'amount': pl.Float64, 'avg_entry': pl.Float64,
    'opened_at': pl.Utf8, 'mark_price': pl.Float64, 'size': pl.Float64,
    'unrealized_pnl': pl.Float64, 'funding': pl.Float64, 'fee': pl.Float64,
    'exact_avg_price': pl.Boolean,
})
# change-aggregate mode: per-(token, window) position-action $ flow (no wallet column).
_EMPTY_HL_POSITIONS_CHANGE_AGG = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'token': pl.Utf8,
    'opened_long': pl.Float64, 'opened_short': pl.Float64,
    'increased_long': pl.Float64, 'decreased_long': pl.Float64,
    'increased_short': pl.Float64, 'decreased_short': pl.Float64,
    'closed_long': pl.Float64, 'closed_short': pl.Float64,
    'flip_ls': pl.Float64, 'flip_sl': pl.Float64,
    'net_pos_change': pl.Float64, 'net_flip': pl.Float64, 'net_flow': pl.Float64,
    'abs_flow': pl.Float64,
    'buy_size': pl.Float64, 'sell_size': pl.Float64,
    'buy_taker_size': pl.Float64, 'sell_taker_size': pl.Float64,
})
# snapshot-aggregate mode: per-(token, window) open-position book (no wallet column).
_EMPTY_HL_POSITIONS_SNAP_AGG = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'token': pl.Utf8, 'side': pl.Utf8,
    'net_size': pl.Float64, 'total_count': pl.Int64,
    'longs_size': pl.Float64, 'longs_count': pl.Int64,
    'shorts_size': pl.Float64, 'shorts_count': pl.Int64,
    'avg_entry': pl.Float64,
})

# Dust-rounding: aggregated $ metrics can carry tiny float-cancellation residuals
# (e.g. a net_flow of -1e-9 when the wallet set is balanced). Snap |x| < $0.001 to
# 0 so callers don't see meaningless dust. Applied ONLY to summed $ metric columns
# — never prices / funding rates / coin amounts / counts, which can be legitimately
# smaller than 0.001.
_DUST_EPS = 1e-3
_POSITIONS_CHANGE_AGG_DOLLAR_COLS = [
    'opened_long', 'opened_short', 'increased_long', 'decreased_long',
    'increased_short', 'decreased_short', 'closed_long', 'closed_short',
    'flip_ls', 'flip_sl', 'net_pos_change', 'net_flip', 'net_flow', 'abs_flow',
    'buy_size', 'sell_size', 'buy_taker_size', 'sell_taker_size',
]
_POSITIONS_SNAP_AGG_DOLLAR_COLS = ['net_size', 'longs_size', 'shorts_size']
_REALIZED_PERF_DOLLAR_COLS = [
    'pnl', 'fees', 'net_pnl', 'funding', 'volume', 'buy_volume', 'sell_volume',
]


def _snap_dust(df: pl.DataFrame, cols) -> pl.DataFrame:
    """Round each of `cols` (if present) to 0 where |value| < $0.001."""
    present = [c for c in cols if c in df.columns]
    if not present:
        return df
    return df.with_columns([
        pl.when(pl.col(c).abs() < _DUST_EPS).then(pl.lit(0.0)).otherwise(pl.col(c)).alias(c)
        for c in present
    ])


def _cast_time_ms_utc(df: pl.DataFrame) -> pl.DataFrame:
    """Force `time` column to Datetime('ms', UTC) so clients see the
    same precision Horatio returns (matches `_EMPTY_*` schemas)."""
    if 'time' in df.columns:
        dt = df.schema['time']
        if isinstance(dt, pl.Datetime) and (
            dt.time_unit != 'ms' or dt.time_zone != 'UTC'
        ):
            df = df.with_columns(pl.col('time').cast(pl.Datetime('ms', 'UTC')))
    return df


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get('/health')
async def health(_request: Request):
    return response.json({
        'status': 'ok',
        'snapshots_dir': app.ctx.snapshots_dir,
        'clickhouse_host': app.ctx.ch_host,
    })


@app.post('/binance/ohlcv/read')
async def binance_ohlcv(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'window', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token, window = body['token'], body['window']
    since, until = body['since'], body['until']
    try:
        sql, params = sql_b.binance_ohlcv(token, window, since, until)
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_OHLCV_FULL
    return await _maybe_save_or_return(df, body, f'binance_{token}_ohlcv_{window}.parquet')


@app.post('/binance/funding_rate/read')
async def binance_funding_rate(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token, since, until = body['token'], body['since'], body['until']
    sql, params = sql_b.binance_funding_rate(token, since, until)
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_FUNDING_FULL
    return await _maybe_save_or_return(df, body, f'binance_{token}_funding_rate.parquet')


@app.post('/evm/aave/read')
async def evm_aave(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'event', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network, event = body['network'], body['event']
    since, until = body['since'], body['until']
    try:
        sql, params = sql_b.evm_aave(
            event, network, since, until,
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
            eth_market_type=body.get('eth_market_type'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_AAVE_BY_EVENT.get(event, _EMPTY_AAVE_BY_EVENT['deposit'])
    if sql is None:
        # Unsupported network — drop-in Horatio behavior: empty result, no error.
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(df, body, f'{network}_aave_{event}.parquet')


# Registered (both /read and /read/min) via the alias loop below, alongside the
# other transfer handlers — Horatio routes to /read/min when .min_amount() is set.
async def evm_erc20_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'tokens', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network, tokens = body['network'], body['tokens']
    since, until = body['since'], body['until']
    if not isinstance(tokens, list) or not tokens:
        return response.json({'error': 'tokens must be a non-empty list'}, status=400)
    sql, params = sql_b.evm_erc20_transfers(
        network, tokens, since, until, **_transfer_filter_kwargs(body),
    )
    if sql is None:
        df = _EMPTY_TRANSFER_FULL
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_TRANSFER_FULL
    # `local_filters` are Horatio's post-query polars-side filters. The
    # route accepts the field but only the snapshot-scan path applies them
    # (see snapshots.scan); on the raw-read path they're informational —
    # the body still validates so client code that includes them gets the
    # same 200 it did against Horatio.
    filename = f"{network}_{'_'.join(tokens)}_transfers.parquet"
    return await _maybe_save_or_return(df, body, filename)


# ---------------------------------------------------------------------------
# Remaining binance routes
# ---------------------------------------------------------------------------

@app.post('/binance/raw_trades/read')
async def binance_raw_trades(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token = body['token']
    with_id = bool(body.get('with_id', False))
    add_symbol = bool(body.get('add_symbol', False))
    sql, params = sql_b.binance_raw_trades(
        token, body['since'], body['until'], with_id=with_id,
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = (
            _EMPTY_RAW_TRADES_FULL.with_columns(pl.lit(None).cast(pl.Int64).alias('id'))
            if with_id else _EMPTY_RAW_TRADES_FULL
        )
    if add_symbol:
        df = df.with_columns(pl.lit(token).alias('symbol'))
    return await _maybe_save_or_return(df, body, f'binance_{token}_raw_trades.parquet')


# --- Binance SPOT reads --------------------------------------------------
# Spot ohlcv / raw_trades share the perp schema + empty templates; only the
# source table differs (see sql.binance_spot_*). Mirror the perp handlers.
@app.post('/binance/spot/ohlcv/read')
async def binance_spot_ohlcv(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'window', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token, window = body['token'], body['window']
    since, until = body['since'], body['until']
    try:
        sql, params = sql_b.binance_spot_ohlcv(token, window, since, until)
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_OHLCV_FULL
    return await _maybe_save_or_return(df, body, f'binance_spot_{token}_ohlcv_{window}.parquet')


@app.post('/binance/spot/raw_trades/read')
async def binance_spot_raw_trades(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token = body['token']
    with_id = bool(body.get('with_id', False))
    add_symbol = bool(body.get('add_symbol', False))
    sql, params = sql_b.binance_spot_raw_trades(
        token, body['since'], body['until'], with_id=with_id,
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = (
            _EMPTY_RAW_TRADES_FULL.with_columns(pl.lit(None).cast(pl.Int64).alias('id'))
            if with_id else _EMPTY_RAW_TRADES_FULL
        )
    if add_symbol:
        df = df.with_columns(pl.lit(token).alias('symbol'))
    return await _maybe_save_or_return(df, body, f'binance_spot_{token}_raw_trades.parquet')


@app.post('/binance/book_depth/read')
async def binance_book_depth(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token = body['token']
    sql, params = sql_b.binance_book_depth(token, body['since'], body['until'])
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_BOOK_DEPTH_FULL
    return await _maybe_save_or_return(df, body, f'binance_{token}_book_depth.parquet')


@app.post('/binance/open_interest/read')
async def binance_open_interest(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token = body['token']
    sql, params = sql_b.binance_open_interest(token, body['since'], body['until'])
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_OPEN_INTEREST_FULL
    return await _maybe_save_or_return(df, body, f'binance_{token}_open_interest.parquet')


@app.post('/binance/long_short_ratios/read')
async def binance_long_short_ratios(request: Request):
    body = request.json or {}
    try:
        _require(body, 'token', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    token = body['token']
    sql, params = sql_b.binance_long_short_ratios(token, body['since'], body['until'])
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_LSR_FULL
    return await _maybe_save_or_return(df, body, f'binance_{token}_long_short_ratios.parquet')


# ---------------------------------------------------------------------------
# EVM native transfers + TRON + BTC — all hit `tradernick.transfers` with a
# different (kind, chain) WHERE clause. Body shape mirrors erc20: sender,
# receiver, involving, exclude_*, min_amount, max_amount.
# ---------------------------------------------------------------------------

def _transfer_filter_kwargs(body: dict) -> dict:
    """Pull every wallet-selection / amount filter the SQL builder accepts
    off the request body. Centralized so each transfer route stays a thin
    shim — adding a new filter is two lines in the body + sql.py, not
    five copy-pastes per route."""
    keys = (
        'sender', 'receiver', 'involving',
        'exclude_sender', 'exclude_receiver', 'exclude_involving',
        'sender_label', 'receiver_label', 'involving_label',
        'exclude_sender_label', 'exclude_receiver_label', 'exclude_involving_label',
        'sender_category', 'receiver_category', 'involving_category',
        'exclude_sender_category', 'exclude_receiver_category', 'exclude_involving_category',
        'sender_groups', 'receiver_groups', 'involving_groups',
        'exclude_sender_groups', 'exclude_receiver_groups', 'exclude_involving_groups',
        'min_amount', 'max_amount',
    )
    return {k: body.get(k) for k in keys}


async def evm_native_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network = body['network']
    sql, params = sql_b.evm_native_transfers(
        network, body['since'], body['until'], **_transfer_filter_kwargs(body),
    )
    if sql is None:
        df = _EMPTY_TRANSFER_FULL
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_TRANSFER_FULL
    return await _maybe_save_or_return(df, body, f'{network}_native_transfers.parquet')


async def tron_native_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network = body['network']
    sql, params = sql_b.tron_native_transfers(
        network, body['since'], body['until'], **_transfer_filter_kwargs(body),
    )
    if sql is None:
        df = _EMPTY_TRANSFER_FULL
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_TRANSFER_FULL
    return await _maybe_save_or_return(df, body, f'{network}_native_transfers.parquet')


async def tron_trc20_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'tokens', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network, tokens = body['network'], body['tokens']
    if not isinstance(tokens, list) or not tokens:
        return response.json({'error': 'tokens must be a non-empty list'}, status=400)
    sql, params = sql_b.tron_trc20_transfers(
        network, tokens, body['since'], body['until'], **_transfer_filter_kwargs(body),
    )
    if sql is None:
        df = _EMPTY_TRANSFER_FULL
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_TRANSFER_FULL
    filename = f"{network}_{'_'.join(tokens)}_trc20_transfers.parquet"
    return await _maybe_save_or_return(df, body, filename)


async def btc_native_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    network = body['network']
    sql, params = sql_b.btc_native_transfers(
        network, body['since'], body['until'], **_transfer_filter_kwargs(body),
    )
    if sql is None:
        df = _EMPTY_TRANSFER_FULL
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_TRANSFER_FULL
    return await _maybe_save_or_return(df, body, f'{network}_native_transfers.parquet')


# ---------------------------------------------------------------------------
# Hyperliquid
# ---------------------------------------------------------------------------

def _hl_kwargs(body: dict, *, allow_tokens: bool = True,
               allow_wallets: bool = True) -> dict:
    out: dict = {}
    if allow_tokens:
        out['tokens'] = body.get('tokens') or None
    if allow_wallets:
        out['wallets'] = body.get('wallets') or None
        out['wallet_groups'] = body.get('wallet_groups') or None
    return out


@app.post('/hyperliquid/ohlcv/read')
async def hyperliquid_ohlcv(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    sql, params = sql_b.hl_ohlcv(
        body['since'], body['until'],
        tokens=body.get('tokens') or None,
        window=body.get('window'),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_OHLCV
    return await _maybe_save_or_return(df, body, 'hyperliquid_ohlcv.parquet')


@app.post('/hyperliquid/trades/read')
async def hyperliquid_trades(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    sql, params = sql_b.hl_trades(
        body['since'], body['until'], **_hl_kwargs(body),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_TRADES
    return await _maybe_save_or_return(df, body, 'hyperliquid_trades.parquet')


@app.post('/hyperliquid/fills/read')
async def hyperliquid_fills(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    extra_cols = bool(body.get('extra_cols'))
    sql, params = sql_b.hl_fills(
        body['since'], body['until'], extra_cols=extra_cols, **_hl_kwargs(body),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_FILLS if extra_cols else _EMPTY_HL_FILLS_CORE
    return await _maybe_save_or_return(df, body, 'hyperliquid_fills.parquet')


@app.post('/hyperliquid/funding/read')
async def hyperliquid_funding(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    sql, params = sql_b.hl_funding(
        body['since'], body['until'], **_hl_kwargs(body),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_FUNDING
    return await _maybe_save_or_return(df, body, 'hyperliquid_funding.parquet')


@app.post('/hyperliquid/transfers/read')
async def hyperliquid_transfers(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    sql, params = sql_b.hl_transfers(
        body['since'], body['until'], wallets=body.get('wallets') or None,
        wallet_groups=body.get('wallet_groups') or None,
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_TRANSFERS
    return await _maybe_save_or_return(df, body, 'hyperliquid_transfers.parquet')


@app.post('/hyperliquid/vaults/read')
async def hyperliquid_vaults(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    sql, params = sql_b.hl_vaults(
        body['since'], body['until'], wallets=body.get('wallets') or None,
        wallet_groups=body.get('wallet_groups') or None,
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_VAULTS
    return await _maybe_save_or_return(df, body, 'hyperliquid_vaults.parquet')


@app.post('/hyperliquid/realized_performance/read')
async def hyperliquid_realized_performance(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    if not (body.get('tokens') or body.get('wallets') or body.get('wallet_groups')):
        return response.json(
            {'error': 'realized_performance requires `tokens`, `wallets`, or `wallet_groups`'},
            status=400,
        )
    aggregate = bool(body.get('aggregate'))
    if aggregate and not (body.get('wallets') or body.get('wallet_groups')):
        return response.json(
            {'error': 'aggregate requires `wallets` or `wallet_groups`'}, status=400,
        )
    window = body.get('window')
    try:
        if window:
            # Windowed (relative) mode: per-window realized metrics from fills+funding.
            sql, params = sql_b.hl_realized_performance_windowed(
                body['since'], body['until'], window,
                tokens=body.get('tokens') or None,
                wallets=body.get('wallets') or None,
                wallet_groups=body.get('wallet_groups') or None,
                aggregate=aggregate,
            )
        else:
            # Snapshot (absolute-cumulative daily) mode.
            sql, params = sql_b.hl_realized_performance(
                body['since'], body['until'],
                tokens=body.get('tokens') or None,
                wallets=body.get('wallets') or None,
                wallet_groups=body.get('wallet_groups') or None,
                aggregate=aggregate,
                limit=body.get('limit'),
            )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_REALIZED_PERF_AGG if aggregate else _EMPTY_REALIZED_PERF
    df = _snap_dust(df, _REALIZED_PERF_DOLLAR_COLS)
    return await _maybe_save_or_return(df, body, 'hyperliquid_realized_performance.parquet')


@app.post('/hyperliquid/positions/read')
async def hyperliquid_positions(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    if not (body.get('tokens') or body.get('wallets') or body.get('wallet_groups')):
        return response.json(
            {'error': 'positions requires `tokens`, `wallets`, or `wallet_groups`'},
            status=400,
        )
    window = body.get('window')
    if not window:
        return response.json(
            {'error': 'positions requires `window` (a 15m multiple, e.g. "15m", "1h")'},
            status=400,
        )
    # Neither aggregate mode requires a wallet set — with only `tokens` they cover
    # ALL wallets for those tokens. The general tokens/wallets/wallet_groups guard
    # above still bounds the scan.
    aggregate_change = bool(body.get('aggregate_change'))  # fills action-flow
    aggregate = bool(body.get('aggregate'))                # snapshot position book
    recency = body.get('pos_recency_hrs')
    if recency is not None:
        try:
            recency = int(recency)
            if recency <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return response.json(
                {'error': '`pos_recency_hrs` must be a positive integer'}, status=400,
            )
    try:
        if aggregate_change:
            # Fills-based per-(token, window) position-action flow ($ notional).
            sql, params = sql_b.hl_positions_change_aggregate(
                body['since'], body['until'], window,
                tokens=body.get('tokens') or None,
                wallets=body.get('wallets') or None,
                wallet_groups=body.get('wallet_groups') or None,
            )
        elif aggregate:
            # Snapshot-based per-(token, window) open-position book.
            sql, params = sql_b.hl_positions_snapshot_aggregate(
                body['since'], body['until'], window,
                tokens=body.get('tokens') or None,
                wallets=body.get('wallets') or None,
                wallet_groups=body.get('wallet_groups') or None,
                pos_recency_hrs=recency,
            )
        else:
            # Position snapshots downsampled to `window` (last-in-window, start-aligned).
            sql, params = sql_b.hl_positions(
                body['since'], body['until'], window,
                tokens=body.get('tokens') or None,
                wallets=body.get('wallets') or None,
                wallet_groups=body.get('wallet_groups') or None,
                limit=body.get('limit'),
            )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    df = await query_polars(sql, params)
    if df.is_empty():
        if aggregate_change:
            df = _EMPTY_HL_POSITIONS_CHANGE_AGG
        elif aggregate:
            df = _EMPTY_HL_POSITIONS_SNAP_AGG
        else:
            df = _EMPTY_HL_POSITIONS
    if aggregate_change:
        df = _snap_dust(df, _POSITIONS_CHANGE_AGG_DOLLAR_COLS)
    elif aggregate:
        df = _snap_dust(df, _POSITIONS_SNAP_AGG_DOLLAR_COLS)
    return await _maybe_save_or_return(df, body, 'hyperliquid_positions.parquet')


# Sends + spot_transfers aren't ingested into TN yet; per the plan we return
# the empty Horatio schema rather than an error so multi-call workflows that
# include them don't trip.
@app.post('/hyperliquid/sends/read')
async def hyperliquid_sends(request: Request):
    body = request.json or {}
    df = pl.DataFrame(schema={
        'time': pl.Datetime('us', 'UTC'), 'sender': pl.Utf8, 'destination': pl.Utf8,
        'token': pl.Utf8, 'amount': pl.Float64, 'usdc_value': pl.Float64,
        'fee': pl.Float64, 'block_number': pl.Int64,
    })
    return await _maybe_save_or_return(df, body, 'hyperliquid_sends.parquet')


@app.post('/hyperliquid/spot_transfers/read')
async def hyperliquid_spot_transfers(request: Request):
    body = request.json or {}
    df = pl.DataFrame(schema={
        'time': pl.Datetime('us', 'UTC'), 'sender': pl.Utf8, 'destination': pl.Utf8,
        'token': pl.Utf8, 'amount': pl.Float64, 'usdc_value': pl.Float64,
        'fee': pl.Float64, 'block_number': pl.Int64,
    })
    return await _maybe_save_or_return(df, body, 'hyperliquid_spot_transfers.parquet')


# ---------------------------------------------------------------------------
# Uniswap V3 + Lido (+ Stader / Threshold stubs)
# ---------------------------------------------------------------------------

@app.post('/evm/uniswap/read')
async def evm_uniswap(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_uniswap(
            body['event'], body['network'],
            body.get('symbol0'), body.get('symbol1'), body.get('fee'),
            body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_UNISWAP_BY_EVENT.get(body['event'], _EMPTY_UNISWAP_BY_EVENT['swap'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_uniswap_{body['event']}.parquet",
    )


@app.post('/evm/lido/read')
async def evm_lido(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_lido(
            body['event'], body['network'], body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_LIDO_BY_EVENT.get(body['event'], _EMPTY_LIDO_BY_EVENT['deposit'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_lido_{body['event']}.parquet",
    )


# Register the /read and /read/min aliases — Horatio's client routes to a
# `/read/min` path when `.min_amount(...)` is set. Server-side both paths
# share the same handler (the SQL builder already reads `min_amount` from
# the body); two URLs → one func → distinct route names.
for _func, _base in (
    (evm_erc20_transfers, '/evm/erc20_transfers'),
    (evm_native_transfers, '/evm/native_transfers'),
    (tron_native_transfers, '/tron/native_transfers'),
    (tron_trc20_transfers, '/tron/trc20_transfers'),
    (btc_native_transfers, '/btc/native_transfers'),
):
    app.add_route(_func, f'{_base}/read', methods=['POST'], name=f'{_func.__name__}_read')
    app.add_route(_func, f'{_base}/read/min', methods=['POST'], name=f'{_func.__name__}_min')


# ---------------------------------------------------------------------------
# Phase 4: TN-exclusive protocols (Spark, Morpho, Aerodrome). Not in
# horatio-data-provider's surface — adding new namespaces to the client.
# ---------------------------------------------------------------------------

# Spark mirrors AAVE's six-event surface byte-for-byte; reuse AAVE empties.
@app.post('/evm/spark/read')
async def evm_spark(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_spark(
            body['event'], body['network'], body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_AAVE_BY_EVENT.get(body['event'], _EMPTY_AAVE_BY_EVENT['deposit'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_spark_{body['event']}.parquet",
    )


_EMPTY_MORPHO_BY_EVENT = {
    'supply': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'token': pl.Utf8,
        'assets': pl.Float64, 'shares': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'receiver': pl.Utf8,
        'token': pl.Utf8, 'assets': pl.Float64, 'shares': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'borrow': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'receiver': pl.Utf8,
        'token': pl.Utf8, 'assets': pl.Float64, 'shares': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'repay': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'token': pl.Utf8,
        'assets': pl.Float64, 'shares': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'supply_collateral': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'token': pl.Utf8,
        'assets': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw_collateral': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'on_behalf': pl.Utf8, 'receiver': pl.Utf8,
        'token': pl.Utf8, 'assets': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'liquidation': pl.DataFrame(schema={
        'block_number': pl.Int64, 'market_id': pl.Utf8,
        'caller': pl.Utf8, 'borrower': pl.Utf8,
        'loan_token': pl.Utf8, 'collateral_token': pl.Utf8,
        'repaid_assets': pl.Float64, 'repaid_shares': pl.Float64,
        'seized_assets': pl.Float64,
        'bad_debt_assets': pl.Float64, 'bad_debt_shares': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
}


@app.post('/evm/morpho/read')
async def evm_morpho(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_morpho(
            body['event'], body['network'], body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
            market_id=body.get('market_id'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_MORPHO_BY_EVENT.get(body['event'], _EMPTY_MORPHO_BY_EVENT['supply'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_morpho_{body['event']}.parquet",
    )


_EMPTY_AERO_CL_BY_EVENT = {
    'swap': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'swapper': pl.Utf8, 'recipient': pl.Utf8,
        'tokenSold': pl.Utf8, 'tokenBought': pl.Utf8,
        'amountSold': pl.Float64, 'amountBought': pl.Float64,
        'sqrt_based_price': pl.Float64, 'liquidity': pl.Float64,
        'tick': pl.Int32, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'sender': pl.Utf8, 'owner': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8, 'owner': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
    'collect': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'owner': pl.Utf8, 'recipient': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'tick_lower': pl.Int32, 'tick_upper': pl.Int32,
        'price_lower': pl.Float64, 'price_upper': pl.Float64,
        'time': pl.Datetime('ms', 'UTC'),
    }),
}


_EMPTY_AERO_BASIC_BY_EVENT = {
    'swap': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'swapper': pl.Utf8, 'recipient': pl.Utf8,
        'tokenSold': pl.Utf8, 'tokenBought': pl.Utf8,
        'amountSold': pl.Float64, 'amountBought': pl.Float64,
        'stable': pl.UInt8, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'deposit': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8, 'sender': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'stable': pl.UInt8, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'withdraw': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'owner': pl.Utf8, 'recipient': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'stable': pl.UInt8, 'time': pl.Datetime('ms', 'UTC'),
    }),
    'claim': pl.DataFrame(schema={
        'block_number': pl.Int64, 'pool_address': pl.Utf8,
        'sender': pl.Utf8, 'recipient': pl.Utf8,
        'amount0': pl.Float64, 'amount1': pl.Float64,
        'token0': pl.Utf8, 'token1': pl.Utf8,
        'stable': pl.UInt8, 'time': pl.Datetime('ms', 'UTC'),
    }),
}


@app.post('/evm/aerodrome/concentrated/read')
async def evm_aero_concentrated(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_aero_concentrated(
            body['event'], body['network'],
            body.get('symbol0'), body.get('symbol1'),
            body.get('tick_spacing'),
            body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_AERO_CL_BY_EVENT.get(body['event'], _EMPTY_AERO_CL_BY_EVENT['swap'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_aero_cl_{body['event']}.parquet",
    )


@app.post('/evm/aerodrome/basic/read')
async def evm_aero_basic(request: Request):
    body = request.json or {}
    try:
        _require(body, 'event', 'network', 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    try:
        sql, params = sql_b.evm_aero_basic(
            body['event'], body['network'],
            body.get('symbol0'), body.get('symbol1'),
            body.get('stable'),
            body['since'], body['until'],
            involving=body.get('involving'),
            exclude_involving=body.get('exclude_involving'),
        )
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    empty_template = _EMPTY_AERO_BASIC_BY_EVENT.get(body['event'], _EMPTY_AERO_BASIC_BY_EVENT['swap'])
    if sql is None:
        df = empty_template
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = empty_template
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_aero_basic_{body['event']}.parquet",
    )
