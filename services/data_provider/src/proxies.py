"""Jobs proxy.

`/jobs/*` proxies to admin_server (which already owns the job queue for
backfills) so the client's `jobs` namespace keeps working.

The old `/cache/flush` and per-namespace `flush` / `compact` / `dedup` no-op
routes (Horatio-era cache maintenance) were removed — data_provider reads live
from ClickHouse, so there was never a per-route cache to flush/compact/dedup,
and the matching client methods are gone as of 0.8.0.
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


def register(app: Sanic) -> None:
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
