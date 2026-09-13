import io
import os

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from .exceptions import DataProviderHTTPError


def raise_for_status(response: httpx.Response) -> None:
    """`response.raise_for_status()` that keeps the server's explanation.

    httpx's version raises HTTPStatusError carrying only the status line, so a
    caller sees "Server error '500'" and nothing else — data_provider's JSON
    body, which names the failure and says how to fix it, is discarded. That
    turned a 64 GiB memory-limit rejection into an opaque 500 for a user on
    2026-09-12. This surfaces `error` and `message` as
    DataProviderHTTPError.error / .detail, matching the read paths."""
    if response.is_success:
        return
    err = detail = None
    if "application/json" in response.headers.get("content-type", ""):
        try:
            data = response.json()
            if isinstance(data, dict):
                err, detail = data.get("error"), data.get("message")
        except ValueError:
            pass
    raise DataProviderHTTPError(
        response.status_code, err or f"HTTP {response.status_code}", detail)


async def fetch_table(session: httpx.AsyncClient, url: str, body: dict) -> pa.Table | None:
    response = await session.post(url, json=body)
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        if response.is_success and data.get("saved"):
            return None
        raise DataProviderHTTPError(
            response.status_code, data.get("error", str(data)), data.get("message"))
    response.raise_for_status()
    return pq.read_table(io.BytesIO(response.content))


async def stream_to_file(session: httpx.AsyncClient, url: str, body: dict,
                         dest: "str | os.PathLike") -> str:
    """POST a read and stream the parquet straight to ``dest`` on disk.

    ``fetch_table`` buffers the whole response (``response.content``) and then
    the whole Arrow table — two full copies in RAM. That is fine for ordinary
    reads and fatal for large ones: the server now chunks wide ranges and will
    happily return tens of GB, which the buffering path cannot receive.

    This writes chunk-by-chunk and holds only the current chunk, so response
    size is bounded by disk rather than memory. Read the file afterwards with
    ``pyarrow.parquet.read_table`` (eager) or, for results too big for RAM,
    ``polars.scan_parquet`` / ``pq.ParquetFile`` row-group iteration (lazy).

    Returns the destination path. On any failure the partial file is removed."""
    dest = str(dest)
    tmp = dest + ".part"
    try:
        async with session.stream("POST", url, json=body) as response:
            if "application/json" in response.headers.get("content-type", ""):
                await response.aread()
                data = response.json()
                raise DataProviderHTTPError(
                    response.status_code, data.get("error", str(data)), data.get("message"))
            response.raise_for_status()
            with open(tmp, "wb") as fh:
                async for chunk in response.aiter_bytes(1 << 20):
                    fh.write(chunk)
        os.replace(tmp, dest)
        return dest
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
