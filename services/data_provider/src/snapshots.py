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

`save_multi` (server-side per-network fan-out + DuckDB merge) is sketched
here but doesn't share state with the per-route handlers — until we wire
it up to a proper subprocess pool it returns 501. Single-network calls
already exercise the inline `save_key` path on each route, so the only
loss is the parallel multi-network upload optimization.
"""

from __future__ import annotations

import io
import logging
import os
import re

import polars as pl
import pyarrow.parquet as pq
from sanic import Request, Sanic, response

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
        """Server-side multi-network fan-out + DuckDB merge.

        Horatio runs the per-network reads in subprocesses and merges via
        DuckDB so the client never materializes the union. Phase 2 ships
        a 501 stub — single-network calls already exercise the inline
        `save_key` path on each route. Wiring the subprocess pool is
        tracked alongside the scan engine work.
        """
        return response.json(
            {'error': 'save_multi not implemented yet — set save_key on the per-route call instead'},
            status=501,
        )

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
