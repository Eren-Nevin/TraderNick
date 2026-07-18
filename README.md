# TraderNick

**Version 2.0**

On-chain and exchange market-data platform: ingests perps, spot, DEX, lending,
staking, and wallet-flow data from [DeFiStream](https://defistream.dev) into
ClickHouse and serves it through a real-time SvelteKit/D3 dashboard with a
Hyperliquid smart-money analytics suite.

What started (v1) as a single Binance-OHLCV pipeline is now a multi-source stack
spanning ten+ data providers, per-wallet position reconstruction, and a set of
purpose-built trading widgets (Trading Pit, Backtracker Leaderboard, Group
Snapshot, Smart Wallets, Token Shortlist).

## Data sources

| Domain | Sources | Highlights |
|---|---|---|
| **Perps** | Hyperliquid, GMX | fills (maker/taker), positions, funding, PnL, leaderboards |
| **Spot / OHLCV** | Binance | 1m raw + on-the-fly `toStartOfInterval` candles, book depth, funding, long/short ratios, spot CVD |
| **DEX** | Uniswap v2/v3/v4, Aerodrome | swaps, liquidity |
| **Lending** | Aave v2/v3/v4, Morpho, Spark | deposits, borrows, liquidations |
| **Staking** | Lido | deposits / withdrawals |
| **Wallet flows** | EVM / BTC / TRON native + ERC-20/TRC-20 transfers | exchange in/out flows, entity tagging |

## Services

Each ingest domain runs as a **live** poller + a **backfill** worker pair; all
write into ClickHouse and are supervised by the admin server.

- **clickhouse** — single-node ClickHouse, pinned to 6 cores (`cpuset: "0-5"`),
  `restart: unless-stopped`. Raw tables are `ReplacingMergeTree` (idempotent
  re-insert); derived rollups are `Aggregating`/`SummingMergeTree`.
- **`<source>`\_live / `<source>`\_backfill** — one pair per domain
  (`hyperliquid`, `binance`, `transfers`, `aave`, `uniswap`, `aerodrome`,
  `lido`, `morpho`, `spark`, `gmx`). Live pollers self-reconnect and boot-gap-fill;
  backfill workers chunk historical ranges with progress reporting.
- **data\_process\_live / \_backfill** — the data processor: rebuilds the derived
  rollups (`hl_position_history_*`, `hl_fills_*_daily`, `exchange_flow_minute`, …)
  via atomic `REPLACE PARTITION`.
- **admin\_server** — Sanic jobs API (Basic auth). Single source of truth for the
  provider routing table; fans backfill/rebuild jobs out to the per-source workers.
- **tradernick\_admin** — admin-role worker (wallet-label parquet, batch CRUD).
- **data\_server** — read-only Sanic serving every dashboard endpoint (perps,
  DEX, lending, staking, flows, groups, wallet pins, leaderboards).
- **data\_provider** — standalone query API (`:10005`) with a Python client
  (`tradernick-data-provider`, on PyPI). See [Query client](#query-client-tradernick-data-provider).
- **dashboard** — SvelteKit 2 / Svelte 5 / Tailwind 4 / D3. SSR-rendered; all
  data\_server / admin\_server access is proxied server-side.

All ClickHouse clients (ingestion, data\_server, data\_provider) wrap their
connections in a retrying proxy that reconnects and backs off on transient
outages, so a sub-minute ClickHouse blip never drops an insert.

## Dashboard

Pages under `/` (SvelteKit route group `(app)`):

- **trades** — Binance spot/OHLCV charts, book depth, funding, long/short, spot CVD.
- **perp** — Hyperliquid: the smart-money suite (below) + GMX.
- **dex** — Uniswap / Aerodrome swaps & liquidity.
- **lending** — Aave / Morpho / Spark.
- **staking** — Lido.
- **flows** — exchange in/out transfer flows.
- **wallets** / **wallet/hl/`<addr>`** — pinned wallet groups and per-wallet position detail.
- **filters** — smart-money wallet filters.
- **dashboard/`<pageId>`** — user-composable multi-widget layouts.
- **admin** (route group `(admin)`) — backfills, live-job control, token batches.

### Hyperliquid smart-money widgets

- **Trading Pit** — per-token flow across a wallet group: opened / increased /
  decreased / closed / flipped, long & short, with market-order (crossed) share.
- **Backtracker Leaderboard** — per-token wallet activity over a lookback
  (30m / 1h / … ) with a frozen token column.
- **Group Snapshot** — a group's combined book per token (net size, entry, uPnL,
  net-long `+3 (5/2)` = longs/shorts).
- **Smart Wallets** (Cutoff & Dynamic), **Token Leaderboard**, **Token Shortlist**
  (sidebar long/short watchlist), **Wallet Pins**.

## Quickstart

```sh
cp .env.example .env
# edit .env — fill DEFISTREAM_API_KEY, change ADMIN_PASSWORD and CLICKHOUSE_PASSWORD,
# and toggle the per-source *_ENABLED flags for the domains you want to ingest.

docker compose up --build
```

Dashboard: <http://localhost:10000>

## Host ports

| Service | Host | Container | Role |
|---|---|---|---|
| dashboard | `10000` | `3000` | UI + server-side proxy |
| admin\_server (jobs API, auth) | `10001` | `8000` | backfills, live-job control |
| data\_server | `10002` | `8000` | read-only dashboard API |
| clickhouse HTTP | `10003` | `8123` | |
| clickhouse native | `10004` | `9000` | |
| data\_provider | `10005` | `8000` | Python-client query API |

## Endpoints

### admin\_server (port 10001, Basic auth)
- `GET /health` (unauth), `GET /groups`, `GET /jobs`, `GET /jobs/<job_id>`
- `POST /jobs/backfill/<source>` — body `{tokens?, since, until?, force?}`.
  `since` is required (a guard against runaway backfills). `force` purges the
  range first — only for a clean rewrite of suspect data; a gap needs no `force`
  because raw tables are `ReplacingMergeTree`.
- `DELETE /jobs/<job_id>`

```sh
# Backfill Hyperliquid from a date (chunked, with progress)
curl -u admin:change_me -X POST http://localhost:10001/jobs/backfill/hyperliquid \
     -H 'content-type: application/json' \
     -d '{"since":"2026-07-01T00:00:00Z"}'
```

## Query client (`tradernick-data-provider`)

An async Python client for `data_provider`, published on PyPI. Reads come back as
polars/pandas; queries are fluent builders terminated by `as_polars()` /
`as_pandas()` / `as_parquet(key)`. Full reference: **[clients/USAGE.md](clients/USAGE.md)**.

```sh
pip install tradernick-data-provider
```

```python
import asyncio
from tradernick_data_provider import DataProviderClient

async def main():
    async with DataProviderClient("http://localhost:10005") as c:
        df = await c.binance.spot.ohlcv("BTC", "1h") \
            .time_range("2026-07-01", "2026-07-08").as_polars()
        print(df)

asyncio.run(main())
```

### Unified wallet-filter API (v0.7.0)

Transfer queries (`evm.erc20` / `evm.native_transfers` / `tron.*` / `btc.*`) and
snapshot scans share **one** wallet-selection filter surface. Every filter takes
`str | list[str]` (a single value or "any of"), across three roles × four
dimensions, plus `exclude_` variants and `min_amount` / `max_amount`:

| role \ dimension | address | label / entity | category | group |
|---|---|---|---|---|
| `involving` (sender OR receiver) | `.involving(v)` | `.involving_label(v)` | `.involving_category(v)` | `.involving_groups(v)` |
| `sender` | `.sender(v)` | `.sender_label(v)` | `.sender_category(v)` | `.sender_groups(v)` |
| `receiver` | `.receiver(v)` | `.receiver_label(v)` | `.receiver_category(v)` | `.receiver_groups(v)` |

- **address** = raw wallet; **label/entity** = the wallet's `entity` tag;
  **category** = a wallet category; **group** = a named wallet group (from the
  `/wallets` page). All matched case-insensitively.
- The **same method is context-dependent**: on a read it's pushed into
  ClickHouse; on a `scan_parquet` the server resolves the selection to member
  addresses and filters the snapshot in DuckDB (so category/entity/group filters
  work on snapshots too).

```python
# reads: whales OR smart-money, excluding hot wallets, over $1M
flow = await (c.evm.erc20.transfers(["USDC", "USDT"]).network("ethereum")
              .involving_groups(["Whales", "Smart-Money"])
              .exclude_sender_category("Hot-Wallet")
              .min_amount(1_000_000)
              .time_range("2026-07-10", "2026-07-11").as_polars())

# same filters, applied to a saved snapshot
sub = await c.scan_parquet("usdc_flows").sender_groups(["Whales"]).as_polars()

# resolve a selection to its addresses
addrs = await c.wallets.addresses(groups=["Whales"], categories="CEX")
```

Also: `binance.{ohlcv, raw_trades, book_depth, funding_rate, ...}` +
`binance.spot.*`, `hyperliquid.{fills, trade_history, ...}`, `evm.{aave, uniswap,
lido, spark, morpho, aerodrome}`, snapshots (`as_parquet` / `load_parquet` /
`scan_parquet`), and `wallets` / `jobs` / `cache`.

## Operations

See **[docs/runbooks/outage-recovery.md](docs/runbooks/outage-recovery.md)** for
multi-hour / multi-day outage recovery — detection, gap audit, raw-table backfill,
`OPTIMIZE`, and derived-rollup rebuild. The golden rule: every raw ingest table is
a `ReplacingMergeTree`, so re-inserting is idempotent and backfilling can never
permanently duplicate.

## Production note

The `10001`–`10005` host-port bindings exist for local development. In production,
remove the `ports:` blocks for `admin_server`, `data_server`, and `data_provider`
— the dashboard and admin panel reach them over the internal Docker network.
