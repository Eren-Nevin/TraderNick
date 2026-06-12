"""Wallets namespace.

Reads hit `tradernick.wallets` directly (CH SELECT). Writes go to the
admin_server's existing `POST /admin/wallets` parquet-upload endpoint —
data_provider stays read-only against ingestion tables, just like the plan
specifies.

Horatio wire contract:

  GET    /wallets               → {"wallets": [{address, categories, entity, ...}, ...]}
                                  query params: category=, entity=, search=, limit=, offset=
  GET    /wallets/<address>     → {address, categories, entity, ...} or 404
  POST   /wallets               → upsert, parquet bytes in application/octet-stream body
  DELETE /wallets/<address>     → {"deleted": <address>}
"""

from __future__ import annotations

import logging
import os

import httpx
import polars as pl
from sanic import Request, Sanic, response

from .ch import get_client, query_polars

log = logging.getLogger(__name__)


_ADMIN_URL = os.environ.get('ADMIN_SERVER_URL', 'http://admin_server:8000')
_ADMIN_USER = os.environ.get('ADMIN_USER') or os.environ.get('INGESTION_ADMIN_USER')
_ADMIN_PASS = os.environ.get('ADMIN_PASSWORD') or os.environ.get('INGESTION_ADMIN_PASSWORD')
_ADMIN_AUTH = (_ADMIN_USER, _ADMIN_PASS) if (_ADMIN_USER and _ADMIN_PASS) else None


def register(app: Sanic) -> None:
    @app.get('/wallets')
    async def wallets_list(request: Request):
        params = request.args
        category = (params.get('category') or '').lower() or None
        entity = (params.get('entity') or '').lower() or None
        search = (params.get('search') or '').lower() or None
        try:
            limit = max(1, min(int(params.get('limit', 100)), 10000))
        except ValueError:
            limit = 100
        try:
            offset = max(0, int(params.get('offset', 0)))
        except ValueError:
            offset = 0

        where = ['1 = 1']
        sql_params: dict = {'lim': int(limit), 'off': int(offset)}
        if category:
            sql_params['cat'] = category
            where.append('has(arrayMap(c -> lower(c), categories), {cat:String})')
        if entity:
            sql_params['ent'] = entity
            where.append('lower(coalesce(entity, \'\')) = {ent:String}')
        if search:
            sql_params['q'] = f'%{search}%'
            where.append(
                "("
                "lower(address) LIKE {q:String} "
                "OR lower(coalesce(entity, '')) LIKE {q:String} "
                "OR arrayExists(c -> position(lower(c), trim(BOTH '%' FROM {q:String})) > 0, categories)"
                ")"
            )
        sql = f"""
            SELECT address, categories, entity, loaded_at
            FROM tradernick.wallets FINAL
            WHERE {' AND '.join(where)}
            ORDER BY address
            LIMIT {{lim:UInt32}} OFFSET {{off:UInt32}}
        """
        df = await query_polars(sql, sql_params)
        return response.json({'wallets': df.to_dicts()})

    @app.get('/wallets/<address>')
    async def wallets_get(request: Request, address: str):
        sql = """
            SELECT address, categories, entity, loaded_at
            FROM tradernick.wallets FINAL
            WHERE lower(address) = {addr:String}
            LIMIT 1
        """
        df = await query_polars(sql, {'addr': address.lower()})
        if df.is_empty():
            return response.json({'error': 'not found'}, status=404)
        return response.json(df.to_dicts()[0])

    @app.post('/wallets')
    async def wallets_upsert(request: Request):
        """Forward to admin_server's POST /admin/wallets as a multipart
        upload. The client sends parquet bytes in the body; admin_server
        expects a `file` form field, so we adapt the wire format here.

        Skip rematerialize by default — large batches set it via the
        `skip_rematerialize` query param. Matches Horatio's lightweight
        upsert semantics (it doesn't rebuild dictionaries every call)."""
        body = request.body
        if not body:
            return response.json({'error': 'empty body'}, status=400)
        skip_remat = (request.args.get('skip_rematerialize', '1')).lower() in ('1', 'true', 'yes')
        files = {'file': ('wallets.parquet', body, 'application/octet-stream')}
        data = {'skip_rematerialize': '1' if skip_remat else '0'}
        async with httpx.AsyncClient(timeout=300, auth=_ADMIN_AUTH) as client:
            r = await client.post(f'{_ADMIN_URL}/admin/wallets', files=files, data=data)
        try:
            return response.json(r.json(), status=r.status_code)
        except ValueError:
            return response.text(r.text, status=r.status_code)

    @app.delete('/wallets/<address>')
    async def wallets_delete(request: Request, address: str):
        """Delete a single wallet by address.

        The wallets row is removed via lightweight delete against
        `tradernick.wallets`. The dictionary `wallet_labels` reloads on
        its LIFETIME(MIN 300 MAX 600) cycle, so subsequent transfer reads
        will stop matching this address within ~5-10 min without manual
        intervention.

        Caveat: the materialized columns on `tradernick.transfers`
        (sender_categories / receiver_categories / sender_entity /
        receiver_entity) were computed at insert time from the dictionary,
        so existing transfer rows still carry the old labels. Mirrors
        Horatio's behavior (its wallet delete doesn't reach back into
        historical data either). Call /admin/wallets/rematerialize on
        ingestion if you need the historical rows rebuilt — that's a
        ~minutes-to-hour operation we deliberately keep out of the hot path.
        """
        addr = address.strip()
        if not addr:
            return response.json({'error': 'address required'}, status=400)

        # Confirm the row exists so we can return a proper 404 instead of
        # an opaque 200 when the user deletes something that wasn't there.
        check = await query_polars(
            "SELECT count() AS n FROM tradernick.wallets FINAL "
            "WHERE lower(address) = {addr:String}",
            {'addr': addr.lower()},
        )
        if check.is_empty() or int(check['n'][0]) == 0:
            return response.json({'error': 'not found'}, status=404)

        client = await get_client()
        # Lightweight delete: avoids the heavy ALTER...DELETE mutation
        # path on this small (<1M rows) wallets table. Async on the CH
        # side; the dictionary refresh handles eventual consistency.
        await client.command(
            "DELETE FROM tradernick.wallets "
            "WHERE lower(address) = {addr:String}",
            parameters={'addr': addr.lower()},
        )
        log.info('wallets.delete address=%s', addr.lower())
        return response.json({'deleted': addr})
