"""Client helpers for the data-provider jobs API."""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx


TERMINAL_STATUSES = {'succeeded', 'failed'}


async def _submit_job(session: httpx.AsyncClient, url: str, body: dict) -> dict:
    """POST to a job-submitting endpoint; return the parsed {job_id, status} handle."""
    resp = await session.post(url, json=body)
    resp.raise_for_status()
    return resp.json()


class JobsNamespace:
    """Poll / cancel / wait on long-running cache jobs (dedup, compact, migrate)."""

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    async def list(self, status: Optional[str] = None, limit: int = 200) -> list[dict]:
        params: dict = {'limit': limit}
        if status is not None:
            params['status'] = status
        resp = await self._session.get(self._base_url + '/jobs', params=params)
        resp.raise_for_status()
        return resp.json().get('jobs', [])

    async def get(self, job_id: str) -> dict:
        resp = await self._session.get(self._base_url + f'/jobs/{job_id}')
        resp.raise_for_status()
        return resp.json()

    async def cancel(self, job_id: str) -> dict:
        resp = await self._session.post(self._base_url + f'/jobs/{job_id}/cancel', json={})
        resp.raise_for_status()
        return resp.json()

    async def wait(self, job_id: str, *, poll_interval: float = 2.0,
                   timeout: Optional[float] = None) -> dict:
        """Poll until status is terminal. Raises ``asyncio.TimeoutError`` on timeout."""
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        while True:
            rec = await self.get(job_id)
            if rec.get('status') in TERMINAL_STATUSES:
                return rec
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                raise asyncio.TimeoutError(f'Job {job_id} did not finish within {timeout}s')
            await asyncio.sleep(poll_interval)

    async def submit(self, path: str, body: Optional[dict] = None) -> dict:
        """Generic helper: POST to any job-submitting endpoint and return its handle.

        Useful for dedup / compact endpoints on namespaces that don't yet have
        a dedicated ``dedup(...)`` method. Example::

            handle = await client.jobs.submit(
                "/evm/uniswap/dedup",
                {"network": "ETH", "dry_run": True},
            )
            report = await client.jobs.wait(handle["job_id"])
        """
        resp = await self._session.post(self._base_url + path, json=body or {})
        resp.raise_for_status()
        return resp.json()
