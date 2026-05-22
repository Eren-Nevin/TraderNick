# TraderNick

Four-service stack for ingesting Binance OHLCV data from DeFiStream into ClickHouse and serving it through a Svelte/D3 dashboard.

## Services

- **clickhouse** — single-node ClickHouse with 30-day TTL on all data.
- **ingestion** — Sanic admin server. Supervises one live-poller subprocess per group (Phase 1: `binance_ohlcv`). Exposes Basic-auth job endpoints for backfills.
- **data_server** — Read-only Sanic. Serves `/ohlcv` and `/tokens` to the dashboard.
- **dashboard** — SvelteKit 2 / Svelte 5 / Tailwind 4 / D3. SSR-rendered, all data_server access proxied server-side.

Phase 1 scope: BTC, ETH, SOL, ARB, OP at 1m granularity. 5m / 1h / 4h / 1d candles are computed at query time via `toStartOfInterval`.

## Quickstart

```sh
cp .env.example .env
# edit .env — fill DEFISTREAM_API_KEY, change ADMIN_PASSWORD and CLICKHOUSE_PASSWORD

docker compose up --build
```

Dashboard: <http://localhost:10000/trades>

## Host ports

| Service | Host | Container |
|---|---|---|
| dashboard | `10000` | `3000` |
| ingestion (admin API) | `10001` | `8000` |
| data_server | `10002` | `8000` |
| clickhouse HTTP | `10003` | `8123` |
| clickhouse native | `10004` | `9000` |

## Endpoints

### data_server (port 10002)
- `GET /health`
- `GET /tokens`
- `GET /ohlcv?token=BTC&interval=1h&since=<iso>&until=<iso>&limit=1000`

### ingestion (port 10001, Basic auth)
- `GET /health` (unauth)
- `GET /groups`
- `GET /jobs`
- `GET /jobs/<job_id>`
- `POST /jobs/backfill/binance_ohlcv` — body `{tokens?: string[], days?: number}`
- `DELETE /jobs/<job_id>`

Trigger a 30-day backfill for BTC:
```sh
curl -u admin:change_me -X POST http://localhost:10001/jobs/backfill/binance_ohlcv \
     -H 'content-type: application/json' \
     -d '{"tokens":["BTC"],"days":30}'
```

## Production note

The `10001` and `10002` host port bindings exist for local development. Remove both `ports:` blocks for `ingestion` and `data_server` in production — the admin panel reaches ingestion over the internal Docker network, and the dashboard reaches data_server the same way.
