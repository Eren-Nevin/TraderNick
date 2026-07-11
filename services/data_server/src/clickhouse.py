import asyncio
import logging

import clickhouse_connect

import config

_log = logging.getLogger("clickhouse")
_real_client = None
_client_lock = asyncio.Lock()


async def _get_real_client():
    global _real_client
    if _real_client is None:
        async with _client_lock:
            if _real_client is None:
                _real_client = await clickhouse_connect.get_async_client(
                    host=config.CLICKHOUSE_HOST,
                    port=config.CLICKHOUSE_PORT,
                    username=config.CLICKHOUSE_USER,
                    password=config.CLICKHOUSE_PASSWORD,
                    database=config.CLICKHOUSE_DB,
                    # Outermost layer of the timeout chain (frontend 180s < Sanic 240s <
                    # this 300s) so a slow cold query isn't killed by the client before
                    # it finishes and caches.
                    send_receive_timeout=300,
                )
    return _real_client


async def _reset_real_client():
    """Drop the cached client so the next call reconnects. Without this a broken
    singleton persists across a CH restart until the data_server process itself
    restarts — meaning every dashboard query fails until then."""
    global _real_client
    c, _real_client = _real_client, None
    if c is not None:
        try:
            await c.close()
        except Exception:  # noqa: BLE001
            pass


# Transient connection/availability failures worth reconnecting + retrying (CH
# restart, brief blip, gateway 5xx). SQL/schema/type errors are permanent and
# re-raised immediately.
_TRANSIENT_HINTS = (
    "connection", "connreset", "connection reset", "refused", "reset by peer",
    "timed out", "timeout", "operational", "network", "broken pipe", "closed",
    "unreachable", "cannot connect", "connecterror", "readtimeout",
    "connecttimeout", "no route to host", "temporarily unavailable",
    "connection aborted", "server disconnected", "remotedisconnected",
    "502", "503", "504", "eof occurred", "not connected",
    # CH cancels in-flight queries as it shuts down for a restart:
    "query was cancelled", "query_was_cancelled", "killed in pending", "code: 394",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)):
        return True
    return any(h in str(exc).lower() for h in _TRANSIENT_HINTS)


# ~40s of backoff — enough to ride out a <1 min CH restart while keeping a slow
# request under the 240s Sanic ceiling even after a long cold query.
_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 6.0, 9.0, 12.0)


async def _call_with_retry(method: str, *args, **kwargs):
    last: Exception | None = None
    for i, delay in enumerate(_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            ch = await _get_real_client()
            return await getattr(ch, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc):
                raise
            _log.warning("CH %s transient failure (attempt %d/%d): %s — reconnecting",
                         method, i + 1, len(_RETRY_DELAYS), str(exc)[:200])
            await _reset_real_client()
    raise last  # type: ignore[misc]


class _RetryingAsyncClient:
    """Transparent proxy over the clickhouse_connect async client that reconnects +
    retries query / insert / command on TRANSIENT connection errors, so a brief
    ClickHouse outage (<~1 min — e.g. a container restart to apply a cpuset)
    doesn't fail dashboard reads or user CRUD writes. Non-transient errors
    (bad SQL / schema) re-raise immediately. INSERT-INTO-SELECT commands are not
    auto-retried (a blind retry could duplicate partial staging rows)."""

    async def query(self, *a, **k):
        return await _call_with_retry("query", *a, **k)

    async def insert(self, *a, **k):
        return await _call_with_retry("insert", *a, **k)

    async def command(self, cmd, *a, **k):
        stmt = cmd if isinstance(cmd, str) else str(cmd)
        if stmt.lstrip()[:6].upper() == "INSERT":
            try:
                ch = await _get_real_client()
                return await ch.command(cmd, *a, **k)
            except Exception as exc:  # noqa: BLE001
                if _is_transient(exc):
                    await _reset_real_client()
                raise
        return await _call_with_retry("command", cmd, *a, **k)

    async def close(self):
        await _reset_real_client()

    def __getattr__(self, name):
        real = _real_client
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


_proxy = _RetryingAsyncClient()


async def client():
    await _get_real_client()
    return _proxy
