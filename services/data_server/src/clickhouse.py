import clickhouse_connect

import config

_client = None


async def client():
    global _client
    if _client is None:
        _client = await clickhouse_connect.get_async_client(
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
    return _client
