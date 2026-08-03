import io

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from .exceptions import DataProviderHTTPError


async def fetch_table(session: httpx.AsyncClient, url: str, body: dict) -> pa.Table | None:
    response = await session.post(url, json=body)
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        if response.is_success and data.get("saved"):
            return None
        raise DataProviderHTTPError(response.status_code, data.get("error", str(data)))
    response.raise_for_status()
    return pq.read_table(io.BytesIO(response.content))


async def save_parquet(session: httpx.AsyncClient, url: str, body: dict, key: str) -> None:
    """Send a query with save_key to save the result as a named snapshot."""
    resp = await session.post(url, json={**body, "save_key": key})
    resp.raise_for_status()


async def load_parquet_bytes(session: httpx.AsyncClient, base_url: str, key: str) -> bytes:
    """Load a previously saved snapshot as raw parquet bytes."""
    resp = await session.post(f"{base_url}/snapshots/load", json={"key": key})
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        data = resp.json()
        raise DataProviderHTTPError(resp.status_code, data.get("error", str(data)))
    resp.raise_for_status()
    return resp.content


async def load_parquet(session: httpx.AsyncClient, base_url: str, key: str) -> pa.Table:
    """Load a previously saved snapshot as a pyarrow Table."""
    raw = await load_parquet_bytes(session, base_url, key)
    return pq.read_table(io.BytesIO(raw))


async def delete_snapshot(session: httpx.AsyncClient, base_url: str, key: str) -> None:
    """Delete a snapshot by key."""
    resp = await session.post(f"{base_url}/snapshots/delete", json={"key": key})
    resp.raise_for_status()


async def list_snapshots(session: httpx.AsyncClient, base_url: str) -> list[str]:
    """List all saved snapshot keys."""
    resp = await session.get(f"{base_url}/snapshots/list")
    resp.raise_for_status()
    return resp.json()["keys"]


async def list_snapshots_detailed(session: httpx.AsyncClient, base_url: str) -> dict:
    """List saved snapshots with per-file sizes and a roster-wide total.

    Returns the server payload verbatim::

        {"snapshots": [{"key", "bytes", "size", "modified"}, ...],
         "count": int, "total_bytes": int, "total_size": str}
    """
    resp = await session.get(f"{base_url}/snapshots/list_detailed")
    resp.raise_for_status()
    return resp.json()
