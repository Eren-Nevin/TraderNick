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

_EMPTY_OHLCV    = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'),
    'open': pl.Float64, 'high': pl.Float64,
    'low':  pl.Float64, 'close': pl.Float64, 'volume': pl.Float64,
})
_EMPTY_EXCHANGE = pl.DataFrame(schema={
    'time': pl.Datetime('ms', 'UTC'), 'value': pl.Float64,
})
_EMPTY_AAVE     = pl.DataFrame(schema={
    'block_number': pl.Int64, 'user': pl.Utf8, 'token': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_ERC20    = pl.DataFrame(schema={
    'block_number': pl.Int64, 'token': pl.Utf8,
    'sender': pl.Utf8, 'receiver': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_NATIVE   = pl.DataFrame(schema={
    'block_number': pl.Int64, 'token': pl.Utf8,
    'sender': pl.Utf8, 'receiver': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_TRADES   = pl.DataFrame(schema={
    'id': pl.Int64, 'time': pl.Datetime('ms', 'UTC'),
    'price': pl.Float64, 'amount': pl.Float64, 'buy': pl.Boolean,
})
_EMPTY_UNISWAP  = pl.DataFrame(schema={
    'block_number': pl.Int64, 'sender': pl.Utf8, 'recipient': pl.Utf8,
    'amount0': pl.Float64, 'amount1': pl.Float64,
    'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_LIDO     = pl.DataFrame(schema={
    'block_number': pl.Int64, 'sender': pl.Utf8,
    'minted_amount': pl.Float64, 'minted_token': pl.Utf8,
    'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_STADER   = pl.DataFrame(schema={
    'block_number': pl.Int64, 'caller': pl.Utf8, 'receiver': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})
_EMPTY_THRESHOLD = pl.DataFrame(schema={
    'block_number': pl.Int64, 'depositor': pl.Utf8,
    'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
})
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
_EMPTY_HL_TRADE_HISTORY = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'wallet': pl.Utf8, 'token': pl.Utf8,
    'pnl': pl.Float64, 'fees': pl.Float64, 'net_pnl': pl.Float64,
    'volume': pl.Float64, 'buy_volume': pl.Float64, 'sell_volume': pl.Float64,
    'trade_count': pl.Int64,
})
_EMPTY_HL_POSITION_HISTORY = pl.DataFrame(schema={
    'time': pl.Datetime('us', 'UTC'), 'wallet': pl.Utf8, 'token': pl.Utf8,
    'side': pl.Utf8, 'amount': pl.Float64, 'avg_entry': pl.Float64,
    'opened_at': pl.Utf8, 'mark_price': pl.Float64, 'size': pl.Float64,
    'unrealized_pnl': pl.Float64, 'funding': pl.Float64, 'fee': pl.Float64,
    'exact_avg_price': pl.Boolean,
})


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
        df = _EMPTY_OHLCV
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_EXCHANGE
    df = _cast_time_ms_utc(df)
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
    if sql is None:
        # Unsupported network — drop-in Horatio behavior: empty result, no error.
        df = _EMPTY_AAVE
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_AAVE
    df = _cast_time_ms_utc(df)
    return await _maybe_save_or_return(df, body, f'{network}_aave_{event}.parquet')


@app.post('/evm/erc20_transfers/read')
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
        network, tokens, since, until,
        sender=body.get('sender'),
        receiver=body.get('receiver'),
        involving=body.get('involving'),
        exclude_sender=body.get('exclude_sender'),
        exclude_receiver=body.get('exclude_receiver'),
        exclude_involving=body.get('exclude_involving'),
        min_amount=body.get('min_amount'),
        max_amount=body.get('max_amount'),
    )
    if sql is None:
        df = _EMPTY_ERC20
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_ERC20
    df = _cast_time_ms_utc(df)
    # `local_filters` are Horatio's post-query polars-side filters. Phase 1
    # accepts the field but no-ops on it; the body still validates so
    # callers that send it get the same on-the-wire success they did
    # against Horatio. Real local_filter semantics land in Phase 2.
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
        df = _EMPTY_TRADES if with_id else _EMPTY_TRADES.drop('id')
    if add_symbol:
        df = df.with_columns(pl.lit(token).alias('symbol'))
    df = _cast_time_ms_utc(df)
    return await _maybe_save_or_return(df, body, f'binance_{token}_raw_trades.parquet')


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
        df = _EMPTY_EXCHANGE
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_EXCHANGE
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_EXCHANGE
    df = _cast_time_ms_utc(df)
    return await _maybe_save_or_return(df, body, f'binance_{token}_long_short_ratios.parquet')


# ---------------------------------------------------------------------------
# EVM native transfers + TRON + BTC — all hit `tradernick.transfers` with a
# different (kind, chain) WHERE clause. Body shape mirrors erc20: sender,
# receiver, involving, exclude_*, min_amount, max_amount.
# ---------------------------------------------------------------------------

def _transfer_filter_kwargs(body: dict) -> dict:
    """Pull the filter args the SQL builders accept off the request body.
    Done here once so each route handler stays a thin shim."""
    return {
        'sender':            body.get('sender'),
        'receiver':          body.get('receiver'),
        'involving':         body.get('involving'),
        'exclude_sender':    body.get('exclude_sender'),
        'exclude_receiver':  body.get('exclude_receiver'),
        'exclude_involving': body.get('exclude_involving'),
        'min_amount':        body.get('min_amount'),
        'max_amount':        body.get('max_amount'),
    }


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
        df = _EMPTY_NATIVE
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_NATIVE
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_NATIVE
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_NATIVE
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_ERC20
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_ERC20
    df = _cast_time_ms_utc(df)
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
        df = _EMPTY_NATIVE
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_NATIVE
    df = _cast_time_ms_utc(df)
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
    # HL OHLCV uses `window` instead of `time` — the client renames it.
    if 'window' in df.columns:
        dt = df.schema['window']
        if isinstance(dt, pl.Datetime) and (dt.time_unit != 'us' or dt.time_zone != 'UTC'):
            df = df.with_columns(pl.col('window').cast(pl.Datetime('us', 'UTC')))
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
    sql, params = sql_b.hl_fills(
        body['since'], body['until'], **_hl_kwargs(body),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_FILLS
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
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_VAULTS
    return await _maybe_save_or_return(df, body, 'hyperliquid_vaults.parquet')


@app.post('/hyperliquid/trade_history/read')
async def hyperliquid_trade_history(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    if not (body.get('tokens') or body.get('wallets')):
        return response.json(
            {'error': 'trade_history requires `tokens` or `wallets`'}, status=400,
        )
    sql, params = sql_b.hl_trade_history(
        body['since'], body['until'],
        tokens=body.get('tokens') or None,
        wallets=body.get('wallets') or None,
        limit=body.get('limit'),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_TRADE_HISTORY
    return await _maybe_save_or_return(df, body, 'hyperliquid_trade_history.parquet')


@app.post('/hyperliquid/position_history/read')
async def hyperliquid_position_history(request: Request):
    body = request.json or {}
    try:
        _require(body, 'since', 'until')
    except ValueError as e:
        return response.json({'error': str(e)}, status=400)
    if not (body.get('tokens') or body.get('wallets')):
        return response.json(
            {'error': 'position_history requires `tokens` or `wallets`'}, status=400,
        )
    sql, params = sql_b.hl_position_history(
        body['since'], body['until'],
        tokens=body.get('tokens') or None,
        wallets=body.get('wallets') or None,
        window=body.get('window'),
        limit=body.get('limit'),
    )
    df = await query_polars(sql, params)
    if df.is_empty():
        df = _EMPTY_HL_POSITION_HISTORY
    return await _maybe_save_or_return(df, body, 'hyperliquid_position_history.parquet')


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
    if sql is None:
        df = _EMPTY_UNISWAP
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_UNISWAP
    df = _cast_time_ms_utc(df)
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
    if sql is None:
        df = _EMPTY_LIDO
    else:
        df = await query_polars(sql, params)
        if df.is_empty():
            df = _EMPTY_LIDO
    df = _cast_time_ms_utc(df)
    return await _maybe_save_or_return(
        df, body, f"{body['network']}_lido_{body['event']}.parquet",
    )


# Register the /read and /read/min aliases — Horatio's client routes to a
# `/read/min` path when `.min_amount(...)` is set. Server-side both paths
# share the same handler (the SQL builder already reads `min_amount` from
# the body); two URLs → one func → distinct route names.
for _func, _base in (
    (evm_native_transfers, '/evm/native_transfers'),
    (tron_native_transfers, '/tron/native_transfers'),
    (tron_trc20_transfers, '/tron/trc20_transfers'),
    (btc_native_transfers, '/btc/native_transfers'),
):
    app.add_route(_func, f'{_base}/read', methods=['POST'], name=f'{_func.__name__}_read')
    app.add_route(_func, f'{_base}/read/min', methods=['POST'], name=f'{_func.__name__}_min')


@app.post('/evm/stader/read')
async def evm_stader(request: Request):
    """Stader isn't in TN ingestion — return Horatio's empty schema so
    drop-in client code doesn't error."""
    body = request.json or {}
    return await _maybe_save_or_return(
        _EMPTY_STADER, body, f"{body.get('network','x')}_stader_{body.get('event','x')}.parquet",
    )


@app.post('/evm/threshold/read')
async def evm_threshold(request: Request):
    """Threshold isn't in TN ingestion — return Horatio's empty schema."""
    body = request.json or {}
    return await _maybe_save_or_return(
        _EMPTY_THRESHOLD, body, f"{body.get('network','x')}_threshold_{body.get('event','x')}.parquet",
    )
