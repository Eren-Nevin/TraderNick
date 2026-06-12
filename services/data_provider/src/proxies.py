"""Cache stubs + jobs proxy.

`/cache/flush` and the per-namespace `flush` / `compact` / `dedup` paths
existed in Horatio because its disk-cached parquet store needed periodic
maintenance — duplicates from streaming inserts, fragmentation from many
small partitions, stale rows from a wider time window than necessary.

With ClickHouse:
  - `flush` → no-op. We don't materialize a per-route cache; every read
    is a live SELECT against CH. There's nothing to evict.
  - `compact` → no-op. CH's ReplacingMergeTree merges fragments in the
    background; manual OPTIMIZE FINAL would be far more expensive than
    leaving merges to their natural cadence. Returning `{"ok": true}`
    is correct: the client asks "are you compacted?", and the answer
    is "yes, continuously."
  - `dedup` → no-op for the same reason ReplacingMT collapses duplicates
    in the merge step. Returning a dry-run summary (0 found) matches
    Horatio's "nothing to remove" path.

`/jobs/*` proxies to admin_server (which already owns the job queue for
backfills) so the Horatio-style client surface keeps working.
"""

from __future__ import annotations

import logging
import os

import httpx
from sanic import Request, Sanic, response

log = logging.getLogger(__name__)


_ADMIN_URL = os.environ.get('ADMIN_SERVER_URL', 'http://admin_server:8000')
# ingestion's /jobs endpoint is behind Basic auth. We hold the credentials
# server-side so the client doesn't have to surface them — Horatio's wire
# contract has no auth handshake. Falls back to None if unset, in which case
# httpx sends no Authorization header (admin_server in monolith=False mode
# doesn't require auth on every route).
_ADMIN_USER = os.environ.get('ADMIN_USER') or os.environ.get('INGESTION_ADMIN_USER')
_ADMIN_PASS = os.environ.get('ADMIN_PASSWORD') or os.environ.get('INGESTION_ADMIN_PASSWORD')
_ADMIN_AUTH = (_ADMIN_USER, _ADMIN_PASS) if (_ADMIN_USER and _ADMIN_PASS) else None


# Flush / compact / dedup endpoints we accept as no-ops. Every entry is a
# Sanic-rule path string; we register one handler per path with a unique
# name. New protocols only have to be added here once.
_FLUSH_COMPACT_PATHS = [
    # Top-level cache (Horatio's `/cache/flush`).
    '/cache/flush',
    # Binance maintenance endpoints.
    '/binance/raw_trades/flush', '/binance/raw_trades/compact',
    '/binance/ohlcv/flush',      '/binance/ohlcv/compact',
    '/binance/exchange/flush',   '/binance/exchange/compact',
    # EVM ERC-20 transfers (+ aggregate variants).
    '/evm/erc20_transfers/flush',           '/evm/erc20_transfers/compact',
    '/evm/erc20_transfers/dedup',
    '/evm/erc20_transfers/aggregate/flush', '/evm/erc20_transfers/aggregate/compact',
    '/evm/erc20_transfers/aggregate/dedup',
    # EVM native transfers (+ aggregate variants).
    '/evm/native_transfers/flush',           '/evm/native_transfers/compact',
    '/evm/native_transfers/dedup',
    '/evm/native_transfers/aggregate/flush', '/evm/native_transfers/aggregate/compact',
    '/evm/native_transfers/aggregate/dedup',
    # AAVE / Uniswap / Lido / Stader / Threshold.
    '/evm/aave/flush',     '/evm/aave/compact',
    '/evm/aave/aggregate/flush', '/evm/aave/aggregate/compact',
    '/evm/uniswap/flush',  '/evm/uniswap/compact',
    '/evm/uniswap/aggregate/flush', '/evm/uniswap/aggregate/compact',
    '/evm/lido/flush',     '/evm/lido/compact',
    '/evm/lido/aggregate/flush', '/evm/lido/aggregate/compact',
    '/evm/stader/flush',   '/evm/stader/compact',
    '/evm/stader/aggregate/flush', '/evm/stader/aggregate/compact',
    '/evm/threshold/flush', '/evm/threshold/compact',
    '/evm/threshold/aggregate/flush', '/evm/threshold/aggregate/compact',
    # TRON.
    '/tron/trc20_transfers/flush', '/tron/trc20_transfers/compact',
    '/tron/trc20_transfers/dedup',
    '/tron/trc20_transfers/aggregate/flush', '/tron/trc20_transfers/aggregate/compact',
    '/tron/trc20_transfers/aggregate/dedup',
    '/tron/native_transfers/flush', '/tron/native_transfers/compact',
    '/tron/native_transfers/dedup',
    '/tron/native_transfers/aggregate/flush', '/tron/native_transfers/aggregate/compact',
    '/tron/native_transfers/aggregate/dedup',
    # BTC.
    '/btc/native_transfers/flush', '/btc/native_transfers/compact',
    '/btc/native_transfers/dedup',
    # Hyperliquid (single top-level flush + per-endpoint compat).
    '/hyperliquid/flush',
]


async def _cache_noop(request: Request):
    """Return a Horatio-compatible success body.

    Horatio's flush returns `{"flushed": True, ...}`, compact returns
    `{"compacted_partitions": N, ...}`, dedup returns
    `{"dry_run": ..., "removed": 0}`. The client only checks for HTTP
    success — none of these payloads are introspected — so a generic
    `{"ok": true}` is enough to satisfy the contract."""
    return response.json({'ok': True, 'note': 'no-op: ClickHouse handles via TTL + merges'})


def register(app: Sanic) -> None:
    for path in _FLUSH_COMPACT_PATHS:
        # Sanic requires unique route names. Derive from the path so
        # `/evm/aave/flush` becomes `noop_evm_aave_flush` etc.
        name = 'noop' + path.replace('/', '_')
        app.add_route(_cache_noop, path, methods=['POST'], name=name)

    # ----- Jobs proxy -----

    @app.get('/jobs')
    async def jobs_list(request: Request):
        # Forward query params (limit, status) verbatim. ingestion's monolith
        # returns a bare list; Horatio's contract is `{"jobs": [...]}`. The
        # client decodes via `.get('jobs', [])`, so we normalize the shape
        # so the same client code works against either backend.
        async with httpx.AsyncClient(timeout=30, auth=_ADMIN_AUTH) as client:
            r = await client.get(
                f'{_ADMIN_URL}/jobs',
                params={k: v for k, v in request.args.items()},
            )
        if r.status_code >= 400:
            return response.text(
                r.text, status=r.status_code,
                content_type=r.headers.get('content-type', 'application/json'),
            )
        try:
            data = r.json()
        except ValueError:
            return response.text(r.text, status=r.status_code)
        if isinstance(data, list):
            data = {'jobs': data}
        return response.json(data)

    @app.get('/jobs/<job_id>')
    async def jobs_get(request: Request, job_id: str):
        async with httpx.AsyncClient(timeout=30, auth=_ADMIN_AUTH) as client:
            r = await client.get(f'{_ADMIN_URL}/jobs/{job_id}')
        return response.text(
            r.text, status=r.status_code,
            content_type=r.headers.get('content-type', 'application/json'),
        )

    @app.post('/jobs/<job_id>/cancel')
    async def jobs_cancel(request: Request, job_id: str):
        async with httpx.AsyncClient(timeout=30, auth=_ADMIN_AUTH) as client:
            r = await client.delete(f'{_ADMIN_URL}/jobs/{job_id}')
        return response.text(
            r.text, status=r.status_code,
            content_type=r.headers.get('content-type', 'application/json'),
        )
