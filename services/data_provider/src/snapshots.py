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
from datetime import datetime, timezone

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


def _human_size(n: int) -> str:
    """Bytes → human-readable string (1024-based, e.g. '328.4 MB')."""
    size = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB', 'PB'):
        if size < 1024.0 or unit == 'PB':
            return f'{int(size)} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024.0


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

    @app.get('/snapshots/list_detailed')
    async def snapshots_list_detailed(request: Request):
        """Like /snapshots/list, but each entry carries its on-disk size
        (bytes + human-readable) and mtime, plus a roster-wide total. Sorted
        by key to match /snapshots/list."""
        d = app.ctx.snapshots_dir
        if not os.path.isdir(d):
            return response.json({
                'snapshots': [], 'count': 0,
                'total_bytes': 0, 'total_size': _human_size(0),
            })
        entries = []
        total = 0
        for n in os.listdir(d):
            if not n.endswith('.parquet'):
                continue
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                continue  # raced deletion — skip
            total += st.st_size
            entries.append({
                'key': os.path.splitext(n)[0],
                'bytes': st.st_size,
                'size': _human_size(st.st_size),
                'modified': datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            })
        entries.sort(key=lambda e: e['key'])
        return response.json({
            'snapshots': entries,
            'count': len(entries),
            'total_bytes': total,
            'total_size': _human_size(total),
        })

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
            'sender_groups', 'receiver_groups', 'involving_groups',
            'exclude_sender_groups', 'exclude_receiver_groups', 'exclude_involving_groups',
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

        # Wallet-selection filters were already applied at the SQL level by
        # `_build` (via _transfers_filters). No post-fetch step needed.
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
        """Filter a saved snapshot server-side; stream the result as parquet
        bytes or persist it under `save_key`.

        Body accepts the same wallet-selection filter keys as the transfer
        reads (``involving``/``sender``/``receiver`` + ``_label``/``_category``/
        ``_groups`` + ``exclude_*``, each ``str|list``) plus ``since``/``until``
        and ``min_amount``/``max_amount``. Each wallet-selection filter is
        resolved to member addresses (ClickHouse) and applied against the
        snapshot in DuckDB — the snapshot parquet is one table, each resolved
        address set another. Filters referencing columns the snapshot lacks
        (e.g. sender/receiver on a non-transfer snapshot) are skipped.
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

        # Resolve every wallet-selection filter in the body to an address set
        # (one CH query per filter; run concurrently).
        async def _resolve(role, exclude, dim, values):
            return (role, exclude, await _resolve_dim_addresses(dim, values))
        tasks = [
            _resolve(role, exclude, dim, body[bkey])
            for bkey, (role, exclude, dim) in _FILTER_KEYS.items()
            if body.get(bkey)
        ]
        specs = list(await asyncio.gather(*tasks)) if tasks else []

        save_key = body.get('save_key')
        dst = None
        if save_key:
            try:
                dst = _snap_path(app, save_key)
            except ValueError as e:
                return response.json({'error': str(e)}, status=400)

        try:
            data = await asyncio.to_thread(
                _duckdb_scan, src, specs,
                body.get('since'), body.get('until'),
                body.get('min_amount'), body.get('max_amount'), dst,
            )
        except Exception as e:  # noqa: BLE001
            log.exception('snapshots.scan duckdb failed')
            return response.json({'error': f'scan failed: {e}'}, status=500)

        if save_key:
            return response.json({'saved': True, 'key': _safe_key(save_key)})
        return response.raw(data, content_type='application/octet-stream')


# ---------------------------------------------------------------------------
# Snapshot scan: resolve wallet-selection filters → addresses (ClickHouse),
# then filter the snapshot in DuckDB (snapshot + address sets as tables).
# ---------------------------------------------------------------------------

# body filter key → (column role, exclude?, dimension). address = the values
# themselves; label(entity)/category/groups resolve to addresses via CH.
_ROLES = ('sender', 'receiver', 'involving')
_DIMS = {'': 'address', '_label': 'entity', '_category': 'category', '_groups': 'groups'}
_FILTER_KEYS: dict[str, tuple[str, bool, str]] = {}
for _role in _ROLES:
    for _suf, _dim in _DIMS.items():
        _FILTER_KEYS[f'{_role}{_suf}'] = (_role, False, _dim)
        _FILTER_KEYS[f'exclude_{_role}{_suf}'] = (_role, True, _dim)


async def _resolve_dim_addresses(dim: str, values) -> list[str]:
    """Resolve a wallet selection to lowercased member addresses:
      address  → the values themselves (already addresses)
      entity   → tradernick.wallets by entity tag
      category → tradernick.wallets by category (array membership)
      groups   → tradernick.wallet_pins / wallet_groups (user_id 'local')
    """
    if values is None:
        return []
    vals = values if isinstance(values, (list, tuple)) else [values]
    lowered = [str(v).lower() for v in vals if v is not None and str(v) != '']
    if not lowered:
        return []
    if dim == 'address':
        return lowered
    if dim == 'entity':
        sql = ("SELECT DISTINCT lower(address) AS a FROM tradernick.wallets FINAL "
               "WHERE lower(coalesce(entity, '')) IN {v:Array(String)}")
    elif dim == 'category':
        sql = ("SELECT DISTINCT lower(address) AS a FROM tradernick.wallets FINAL "
               "WHERE hasAny(arrayMap(c -> lower(c), categories), {v:Array(String)})")
    elif dim == 'groups':
        sql = ("SELECT DISTINCT lower(p.address) AS a FROM tradernick.wallet_pins p FINAL "
               "WHERE p.user_id = 'local' AND p.deleted = 0 AND p.group_id IN ("
               "SELECT group_id FROM tradernick.wallet_groups FINAL "
               "WHERE user_id = 'local' AND deleted = 0 AND lower(name) IN {v:Array(String)})")
    else:
        return []
    df = await query_polars(sql, {'v': lowered})
    return [] if df.is_empty() else df['a'].to_list()


def _duck_ts(s: str) -> str:
    """ISO 'YYYY-MM-DDTHH:MM:SSZ' → DuckDB-parseable 'YYYY-MM-DD HH:MM:SS'."""
    return str(s).replace('T', ' ').replace('Z', '').strip()


def _sql_lit(s: str) -> str:
    return str(s).replace("'", "''")


def _duckdb_scan(src, specs, since, until, min_amount, max_amount, dst):
    """Filter a snapshot parquet in DuckDB. `specs` = list of
    (role, exclude, addresses). Returns parquet bytes, or writes to `dst` and
    returns None. Runs in a worker thread (sync DuckDB) via asyncio.to_thread."""
    import duckdb
    import pyarrow as pa

    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")  # snapshot time cols are UTC
        cols = con.execute(
            "SELECT * FROM read_parquet(?) LIMIT 0", [src]
        ).fetch_arrow_table().column_names
        has = {c: (c in cols) for c in ('sender', 'receiver', 'time', 'amount')}

        preds: list[str] = []
        params: list = []
        for i, (role, exclude, addrs) in enumerate(specs):
            con.register(
                f'sel_{i}',
                pa.table({'address': pa.array([a.lower() for a in addrs], type=pa.string())}),
            )
            sub = f'SELECT address FROM sel_{i}'
            parts = []
            if role in ('sender', 'involving') and has['sender']:
                parts.append(f'lower(sender) IN ({sub})')
            if role in ('receiver', 'involving') and has['receiver']:
                parts.append(f'lower(receiver) IN ({sub})')
            if not parts:
                continue  # snapshot lacks these columns → filter is a no-op
            if role == 'involving':
                joined = (' OR '.join(parts) if not exclude
                          else ' AND '.join(f'NOT ({p})' for p in parts))
                preds.append(f'({joined})')
            else:
                preds.append(f'NOT ({parts[0]})' if exclude else parts[0])

        if since and has['time']:
            preds.append('time >= ?'); params.append(_duck_ts(since))
        if until and has['time']:
            preds.append('time < ?'); params.append(_duck_ts(until))
        if min_amount is not None and has['amount']:
            preds.append('amount >= ?'); params.append(float(min_amount))
        if max_amount is not None and has['amount']:
            preds.append('amount <= ?'); params.append(float(max_amount))

        where = (' WHERE ' + ' AND '.join(preds)) if preds else ''
        base = f"SELECT * FROM read_parquet('{_sql_lit(src)}'){where}"
        if dst:
            con.execute(f"COPY ({base}) TO '{_sql_lit(dst)}' (FORMAT PARQUET)", params)
            return None
        tbl = con.execute(base, params).fetch_arrow_table()
        buf = io.BytesIO()
        pq.write_table(tbl, buf)
        return buf.getvalue()
    finally:
        con.close()
