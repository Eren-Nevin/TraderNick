"""Filesystem-backed parquet snapshots.

Snapshots are user-named parquet artifacts living under SNAPSHOTS_DIR.
Wire format matches Horatio's snapshot layer exactly so the
tradernick_data_provider client treats them identically:

  GET  /snapshots/list   →  {"keys": [...]}
  POST /snapshots/load   →  raw parquet bytes (Content-Type: application/octet-stream)
  POST /snapshots/delete →  {"deleted": <key>}
  POST /snapshots/save   →  upload parquet bytes, key in `X-Snapshot-Key` header
                            (Horatio client uses this for the multi-network
                            client-side concat → upload fallback path)
  POST /snapshots/scan   →  lazy filter via polars (or DuckDB if engine='duckdb'),
                            return filtered parquet bytes — or save under
                            `save_key` server-side without a round trip.
  POST /snapshots/save_multi → server-side per-network fan-out for transfer
                            queries (erc20 / native / trc20 / btc). Runs the
                            same SQL the per-route handlers do, concats the
                            per-network frames in-process, applies local
                            filters, writes parquet under `save_key`. Bytes
                            never leave the box; the client only sees a
                            {"saved": true, "key": ...} ack.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re

import polars as pl
import pyarrow.parquet as pq
from sanic import Request, Sanic, response

from . import sql as sql_b
from .ch import query_polars

log = logging.getLogger(__name__)


_SAFE_KEY_RE = re.compile(r'[^a-zA-Z0-9._-]')


def _safe_key(key: str) -> str:
    safe = _SAFE_KEY_RE.sub('_', key)
    if not safe or safe in ('.', '..'):
        raise ValueError(f"Invalid snapshot key: {key!r}")
    return safe


def _snap_path(app_: Sanic, key: str) -> str:
    return os.path.join(app_.ctx.snapshots_dir, f'{_safe_key(key)}.parquet')


def register(app: Sanic) -> None:
    """Attach all `/snapshots/*` routes to the Sanic app."""

    @app.get('/snapshots/list')
    async def snapshots_list(request: Request):
        d = app.ctx.snapshots_dir
        if not os.path.isdir(d):
            return response.json({'keys': []})
        keys = sorted(
            os.path.splitext(n)[0]
            for n in os.listdir(d)
            if n.endswith('.parquet')
        )
        return response.json({'keys': keys})

    @app.post('/snapshots/load')
    async def snapshots_load(request: Request):
        body = request.json or {}
        key = body.get('key')
        if not key:
            return response.json({'error': 'missing key'}, status=400)
        try:
            path = _snap_path(app, key)
        except ValueError as e:
            return response.json({'error': str(e)}, status=400)
        if not os.path.isfile(path):
            return response.json({'error': f'snapshot not found: {key}'}, status=404)
        with open(path, 'rb') as fh:
            data = fh.read()
        return response.raw(
            data, content_type='application/octet-stream',
            headers={'Content-Disposition': f'attachment; filename={_safe_key(key)}.parquet'},
        )

    @app.post('/snapshots/delete')
    async def snapshots_delete(request: Request):
        body = request.json or {}
        key = body.get('key')
        if not key:
            return response.json({'error': 'missing key'}, status=400)
        try:
            path = _snap_path(app, key)
        except ValueError as e:
            return response.json({'error': str(e)}, status=400)
        try:
            os.remove(path)
        except FileNotFoundError:
            return response.json({'error': f'snapshot not found: {key}'}, status=404)
        return response.json({'deleted': _safe_key(key)})

    @app.post('/snapshots/save')
    async def snapshots_save(request: Request):
        """Receive parquet bytes in the request body. Used by the client's
        multi-network fallback (it concats per-network polars frames locally,
        writes to a tempfile, streams the file up). Key arrives in the
        `X-Snapshot-Key` header."""
        key = request.headers.get('X-Snapshot-Key') or request.headers.get('x-snapshot-key')
        if not key:
            return response.json(
                {'error': 'missing X-Snapshot-Key header'}, status=400,
            )
        try:
            path = _snap_path(app, key)
        except ValueError as e:
            return response.json({'error': str(e)}, status=400)
        body = request.body
        if not body:
            return response.json({'error': 'empty request body'}, status=400)
        # Atomic write: stage under .tmp then rename. Same pattern Horatio
        # uses so a crashed write never leaves a half-parquet sitting where
        # `load` would later trip on it.
        tmp = path + '.tmp'
        with open(tmp, 'wb') as fh:
            fh.write(body)
        os.replace(tmp, path)
        return response.json({'saved': True, 'key': _safe_key(key)})

    @app.post('/snapshots/save_multi')
    async def snapshots_save_multi(request: Request):
        """Per-network fan-out for transfer queries; concat + persist as one
        parquet under `save_key`.

        Body:
          {
            "protocol": "erc20_transfers" | "native_transfers"
                       | "trc20_transfers" | "tron_native_transfers"
                       | "bitcoin_native_transfers",
            "networks": ["ETH", "ARB", ...],
            "save_key": "my-snapshot",
            "since": ISO, "until": ISO,
            "tokens": [...],          # erc20 / trc20 only
            "min_amount"|"max_amount": float?,
            "sender"|"receiver"|"involving"|<exclude_*>|<*_label>|<*_category>: ...,
            "with_network": bool?,    # default true for len(networks) > 1
            "include_zero_amounts": bool?,
            "local_filters": [...]?,  # applied post-concat
          }

        The route reuses the per-route SQL builders so multi-network and
        single-network reads always agree on filter semantics. Per-network
        queries are gathered concurrently — CH is the bottleneck, not the
        Python side."""
        body = request.json or {}
        protocol = body.get('protocol')
        networks = body.get('networks') or []
        save_key = body.get('save_key')
        since, until = body.get('since'), body.get('until')

        if not protocol:
            return response.json({'error': 'missing protocol'}, status=400)
        if not networks or not isinstance(networks, list):
            return response.json({'error': 'networks must be a non-empty list'}, status=400)
        if not save_key:
            return response.json({'error': 'missing save_key'}, status=400)
        if not since or not until:
            return response.json({'error': 'missing since/until'}, status=400)
        try:
            dst = _snap_path(app, save_key)
        except ValueError as e:
            return response.json({'error': str(e)}, status=400)

        # Pull every shared transfer filter kwarg off the body. Same keys
        # the per-route handler passes through `_transfer_filter_kwargs`.
        filter_keys = (
            'sender', 'receiver', 'involving',
            'exclude_sender', 'exclude_receiver', 'exclude_involving',
            'sender_label', 'receiver_label', 'involving_label',
            'exclude_sender_label', 'exclude_receiver_label', 'exclude_involving_label',
            'sender_category', 'receiver_category', 'involving_category',
            'exclude_sender_category', 'exclude_receiver_category', 'exclude_involving_category',
            'min_amount', 'max_amount',
        )
        filters = {k: body.get(k) for k in filter_keys}

        tokens = body.get('tokens')
        per_proto = {
            'erc20_transfers':         (sql_b.evm_erc20_transfers,   True),
            'native_transfers':        (sql_b.evm_native_transfers,  False),
            'trc20_transfers':         (sql_b.tron_trc20_transfers,  True),
            'tron_native_transfers':   (sql_b.tron_native_transfers, False),
            'bitcoin_native_transfers':(sql_b.btc_native_transfers,  False),
        }
        if protocol not in per_proto:
            return response.json(
                {'error': f'unsupported protocol: {protocol}'}, status=400,
            )
        builder, needs_tokens = per_proto[protocol]
        if needs_tokens and (not isinstance(tokens, list) or not tokens):
            return response.json(
                {'error': f'{protocol} requires non-empty tokens list'}, status=400,
            )

        def _build(network: str):
            if needs_tokens:
                return builder(network, tokens, since, until, **filters)
            return builder(network, since, until, **filters)

        async def _read_one(network: str) -> pl.DataFrame | None:
            sql, params = _build(network)
            if sql is None:
                # Unsupported network on this protocol — skip silently;
                # multi-net callers commonly probe a superset.
                log.info('save_multi: %s/%s unsupported, skipping', protocol, network)
                return None
            df = await query_polars(sql, params)
            if df.is_empty():
                return None
            return df.with_columns(pl.lit(network).alias('network'))

        with_network = body.get('with_network')
        if with_network is None:
            with_network = len(networks) > 1

        try:
            results = await asyncio.gather(
                *[_read_one(n) for n in networks], return_exceptions=False,
            )
        except Exception as e:
            log.exception('save_multi fan-out failed')
            return response.json({'error': f'fan-out failed: {e}'}, status=500)

        frames = [df for df in results if df is not None]
        if not frames:
            # Empty union — emit the canonical empty-transfer schema so the
            # client gets a parquet it can read back without a column-shape
            # surprise. Optionally add the network column.
            empty = pl.DataFrame(schema={
                'block_number': pl.Int64, 'token': pl.Utf8,
                'sender': pl.Utf8, 'receiver': pl.Utf8,
                'amount': pl.Float64, 'time': pl.Datetime('ms', 'UTC'),
                **({'network': pl.Utf8} if with_network else {}),
            })
            if not with_network and 'network' in empty.columns:
                empty = empty.drop('network')
            empty.write_parquet(dst)
            return response.json({
                'saved': True, 'key': _safe_key(save_key), 'rows': 0, 'networks': [],
            })

        # Concat must tolerate per-network column drift (e.g. native vs
        # erc20 token col). `diagonal_relaxed` widens missing columns to
        # null and reconciles supertype differences.
        combined = pl.concat(frames, how='diagonal_relaxed')

        if not with_network and 'network' in combined.columns:
            combined = combined.drop('network')

        # Mirror the per-route default: hide amount==0 noise unless the
        # caller explicitly opted in. Token-approval transfers etc.
        if not body.get('include_zero_amounts') and 'amount' in combined.columns:
            combined = combined.filter(pl.col('amount') != 0)

        local_filters = body.get('local_filters') or []
        if local_filters:
            combined = _apply_local_filters_lazy(combined.lazy(), local_filters).collect()

        if 'time' in combined.columns:
            combined = combined.sort('time')

        combined.write_parquet(dst)
        nets_seen = sorted({
            n for n, f in zip(networks, results) if f is not None
        })
        return response.json({
            'saved': True, 'key': _safe_key(save_key),
            'rows': combined.height, 'networks': nets_seen,
        })

    @app.post('/snapshots/scan')
    async def snapshots_scan(request: Request):
        """Lazy-filter a snapshot via polars and either stream the result
        as parquet bytes or persist it under `save_key`.

        Body:
          {
            "key": "<source key>",
            "engine": "polars" | "duckdb",
            "since": ISO?, "until": ISO?,
            "local_filters": [{"op": "...", "values": [...]}, ...],
            "save_key": "<target key>"?,
          }

        DuckDB engine is accepted but currently falls through to polars —
        it's a perf optimization, not a behavior change.
        """
        body = request.json or {}
        key = body.get('key')
        if not key:
            return response.json({'error': 'missing key'}, status=400)
        try:
            src = _snap_path(app, key)
        except ValueError as e:
            return response.json({'error': str(e)}, status=400)
        if not os.path.isfile(src):
            return response.json({'error': f'snapshot not found: {key}'}, status=404)

        lf = pl.scan_parquet(src)
        since, until = body.get('since'), body.get('until')
        if 'time' in lf.collect_schema().names():
            if since:
                lf = lf.filter(pl.col('time') >= pl.lit(since).str.to_datetime())
            if until:
                lf = lf.filter(pl.col('time') <  pl.lit(until).str.to_datetime())

        lf = _apply_local_filters_lazy(lf, body.get('local_filters') or [])

        save_key = body.get('save_key')
        if save_key:
            try:
                dst = _snap_path(app, save_key)
            except ValueError as e:
                return response.json({'error': str(e)}, status=400)
            lf.sink_parquet(dst)
            return response.json({'saved': True, 'key': _safe_key(save_key)})

        df = lf.collect()
        buf = io.BytesIO()
        df.write_parquet(buf)
        return response.raw(
            buf.getvalue(), content_type='application/octet-stream',
        )


def _apply_local_filters_lazy(lf: pl.LazyFrame, steps: list[dict]) -> pl.LazyFrame:
    """Server-side polars lazy implementation of Horatio's `local_*` filters.

    Each step is `{"op": <op>, "values": [...]}`. Phase 2 supports the
    address-and-label predicates that don't require a wallet metadata
    join — the entity/category/label variants need wallet_labels columns
    available on the snapshot, which most do not.
    """
    schema = lf.collect_schema().names()

    def lower_col(name: str) -> pl.Expr | None:
        if name not in schema:
            return None
        return pl.col(name).str.to_lowercase()

    for step in steps:
        op = step.get('op')
        values = [str(v).lower() for v in step.get('values', [])]
        if not op or not values:
            continue
        if op in ('involving',):
            preds = []
            s = lower_col('sender')
            r = lower_col('receiver')
            if s is not None:
                preds.append(s.is_in(values))
            if r is not None:
                preds.append(r.is_in(values))
            if preds:
                lf = lf.filter(pl.any_horizontal(preds))
        elif op == 'exclude_involving':
            preds = []
            s = lower_col('sender')
            r = lower_col('receiver')
            if s is not None:
                preds.append(~s.is_in(values))
            if r is not None:
                preds.append(~r.is_in(values))
            if preds:
                lf = lf.filter(pl.all_horizontal(preds))
        elif op == 'sender':
            s = lower_col('sender')
            if s is not None:
                lf = lf.filter(s.is_in(values))
        elif op == 'exclude_sender':
            s = lower_col('sender')
            if s is not None:
                lf = lf.filter(~s.is_in(values))
        elif op == 'receiver':
            r = lower_col('receiver')
            if r is not None:
                lf = lf.filter(r.is_in(values))
        elif op == 'exclude_receiver':
            r = lower_col('receiver')
            if r is not None:
                lf = lf.filter(~r.is_in(values))
        else:
            # Label/category/entity variants — no-op until wallet_labels
            # is exposed alongside the snapshot. Skipping is correct
            # behavior per Horatio (filter that finds no columns to match
            # against is a no-op, not an error).
            log.debug('local_filter %s — skipped (no matching columns)', op)
    return lf
