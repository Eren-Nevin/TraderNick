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
        )
    return _client
