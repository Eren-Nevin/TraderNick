"""Wallet labels CRUD (mirrors server ``/wallets`` routes)."""
from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, List

import httpx
import pandas as pd
import polars as pl
import pyarrow.parquet as pq

from .exceptions import DataProviderHTTPError


def _as_list(v):
    if v is None:
        return None
    return [v] if isinstance(v, str) else list(v)

if TYPE_CHECKING:
    from typing_extensions import Self


def _to_parquet_bytes(data: pd.DataFrame | pl.DataFrame | bytes) -> bytes:
    """Serialize data to parquet bytes. Accepts a pandas/polars DataFrame or raw bytes."""
    if isinstance(data, bytes):
        return data
    if isinstance(data, pl.DataFrame):
        buf = io.BytesIO()
        data.write_parquet(buf)
        return buf.getvalue()
    if isinstance(data, pd.DataFrame):
        buf = io.BytesIO()
        data.to_parquet(buf, index=False)
        return buf.getvalue()
    raise TypeError(
        f"wallets.upsert expects a pandas/polars DataFrame or bytes, got {type(data).__name__}"
    )


async def _handle_json(resp: httpx.Response) -> Any:
    """Parse JSON body; convert non-2xx to DataProviderHTTPError."""
    try:
        data = resp.json()
    except Exception:
        data = {"error": resp.text}
    if resp.is_success:
        return data
    raise DataProviderHTTPError(resp.status_code, data.get("error", str(data)))


class WalletsNamespace:
    """Client for the server's wallet-labels CRUD endpoints."""

    def __init__(self, session: httpx.AsyncClient, base_url: str):
        self._session = session
        self._base_url = base_url

    async def list(
        self,
        *,
        category: str | None = None,
        entity: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        """List wallet labels with optional server-side filters."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category
        if entity is not None:
            params["entity"] = entity
        if search is not None:
            params["search"] = search
        resp = await self._session.get(self._base_url + "/wallets", params=params)
        data = await _handle_json(resp)
        return data.get("wallets", [])

    async def addresses(
        self,
        *,
        groups: str | List[str] | None = None,
        categories: str | List[str] | None = None,
        entities: str | List[str] | None = None,
        labels: str | List[str] | None = None,
        addresses: str | List[str] | None = None,
    ) -> List[str]:
        """Resolve a wallet selection to the matching addresses (lowercased).

        The union of every wallet in any of the given ``groups`` /
        ``categories`` / ``entities`` (``labels`` is a synonym for
        ``entities``), plus any raw ``addresses`` passed through. This is the
        same resolver the ``scan_parquet`` filters use under the hood, exposed
        directly so you can inspect or reuse a wallet set.
        """
        body: dict[str, Any] = {}
        for name, val in (
            ("groups", groups), ("categories", categories),
            ("entities", entities), ("labels", labels), ("addresses", addresses),
        ):
            lst = _as_list(val)
            if lst:
                body[name] = lst
        resp = await self._session.post(
            self._base_url + "/wallets/addresses", json=body, timeout=None,
        )
        if not resp.is_success:
            await _handle_json(resp)  # raises
        table = pq.read_table(io.BytesIO(resp.content))
        col = "address" if "address" in table.column_names else table.column_names[0]
        return table.column(col).to_pylist()

    async def get(self, address: str) -> dict | None:
        """Fetch a single wallet label by address. Returns ``None`` if not found."""
        resp = await self._session.get(self._base_url + f"/wallets/{address}")
        if resp.status_code == 404:
            return None
        return await _handle_json(resp)

    async def upsert(self, data: pd.DataFrame | pl.DataFrame | bytes) -> dict:
        """Upsert labels. Accepts a pandas/polars DataFrame or raw parquet bytes."""
        body = _to_parquet_bytes(data)
        resp = await self._session.post(
            self._base_url + "/wallets",
            content=body,
            headers={"Content-Type": "application/octet-stream"},
        )
        return await _handle_json(resp)

    async def delete(self, address: str) -> dict:
        """Delete a wallet label by address."""
        resp = await self._session.delete(self._base_url + f"/wallets/{address}")
        return await _handle_json(resp)
